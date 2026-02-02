import json
import os
import sys
import time
import uuid
import threading
import subprocess
from dataclasses import dataclass, field
from typing import Dict, Optional, Any

from mcp.server.fastmcp import FastMCP  # 官方 SDK v1.x 內建 FastMCP :contentReference[oaicite:3]{index=3}

# ---- MCP Server ----
mcp = FastMCP("OnboardingFSM", json_response=True)  # :contentReference[oaicite:4]{index=4}

PROMPT_MARK = "\n你："          # 你 onboarding_fsm.main() 的 input prompt
FINAL_MARK = "FINAL_JSON:"      # 你程式最後印的 FINAL_JSON: {...}


@dataclass
class ProcSession:
    session_id: str
    proc: subprocess.Popen
    buf: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)
    updated: threading.Event = field(default_factory=threading.Event)
    read_pos: int = 0
    closed: bool = False

    def start_reader(self) -> None:
        def _reader():
            try:
                while True:
                    ch = self.proc.stdout.read(1)  # 讀 1 char，才抓得到沒有換行的「你：」
                    if ch == "" or ch is None:
                        break
                    with self.lock:
                        self.buf += ch
                        self.updated.set()
            finally:
                self.updated.set()

        t = threading.Thread(target=_reader, daemon=True)
        t.start()

    def _wait_for(self, predicate, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            # 等待新輸出
            self.updated.wait(timeout=0.2)
            self.updated.clear()
        # timeout 也不直接爆；讓上層拿目前累積輸出

    def read_until_prompt_or_exit(self, timeout: float = 30.0) -> str:
        """
        回傳：從上次 read_pos 之後，到下一次看到 PROMPT_MARK（不含 prompt）為止的輸出。
        若程式已結束，回傳剩餘輸出。
        """
        def has_prompt_or_exit() -> bool:
            with self.lock:
                if self.proc.poll() is not None:
                    return True
                return self.buf.find(PROMPT_MARK, self.read_pos) != -1

        self._wait_for(has_prompt_or_exit, timeout=timeout)

        with self.lock:
            # 如果看到 prompt，就切到 prompt 前
            idx = self.buf.find(PROMPT_MARK, self.read_pos)
            if idx != -1:
                out = self.buf[self.read_pos:idx]
                # read_pos 移到 prompt 後（跳過 prompt 本身）
                self.read_pos = idx + len(PROMPT_MARK)
                return out

            # 沒看到 prompt，但程式可能結束了：把剩下的吐出來
            out = self.buf[self.read_pos:]
            self.read_pos = len(self.buf)
            return out

    def send(self, text: str) -> None:
        if self.proc.poll() is not None:
            return
        self.proc.stdin.write(text + "\n")
        self.proc.stdin.flush()

    def terminate(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.proc.terminate()
        except Exception:
            pass


_sessions: Dict[str, ProcSession] = {}


def _spawn_onboarding_process() -> ProcSession:
    sid = str(uuid.uuid4())

    # 用 -u 讓 stdout 盡量即時（不然你會覺得卡住）
    # 注意：這裡是 "import onboarding_fsm as agent; agent.main()"
    cmd = [
        sys.executable, "-u",
        "-c", "import onboarding_fsm as agent; agent.main()"
    ]

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=0,
        cwd=os.getcwd(),
        env=os.environ.copy(),
    )

    sess = ProcSession(session_id=sid, proc=proc)
    sess.start_reader()
    return sess


def _try_extract_final_json(text: str) -> Optional[Dict[str, Any]]:
    # 從輸出中找 FINAL_JSON: {...}
    lines = text.splitlines()
    for line in reversed(lines):
        if FINAL_MARK in line:
            raw = line.split(FINAL_MARK, 1)[1].strip()
            try:
                return json.loads(raw)
            except Exception:
                return None
    return None


@mcp.tool()
def start_session() -> Dict[str, Any]:
    """
    開一個新的 onboarding session，回傳第一段 agent 輸出（通常是「請問店名是什麼？」）
    """
    sess = _spawn_onboarding_process()
    _sessions[sess.session_id] = sess

    output = sess.read_until_prompt_or_exit(timeout=30.0)
    return {
        "session_id": sess.session_id,
        "output": output.strip(),
        "done": sess.proc.poll() is not None,
    }


@mcp.tool()
def send(session_id: str, user_text: str) -> Dict[str, Any]:
    """
    送使用者輸入給該 session，回傳 agent 下一段輸出。
    """
    sess = _sessions.get(session_id)
    if not sess:
        return {"error": f"unknown session_id: {session_id}"}

    sess.send(user_text)

    output = sess.read_until_prompt_or_exit(timeout=60.0)
    done = sess.proc.poll() is not None

    final_json = _try_extract_final_json(output)  # 只抓這次輸出也行
    return {
        "session_id": session_id,
        "output": output.strip(),
        "done": done,
        "final_json": final_json,
    }


@mcp.tool()
def close_session(session_id: str) -> Dict[str, Any]:
    sess = _sessions.pop(session_id, None)
    if not sess:
        return {"ok": True, "message": "already closed"}
    sess.terminate()
    return {"ok": True}


if __name__ == "__main__":
    # stdio 最通用（Claude Desktop / inspector 通常用這個）
    # 也可以改成 transport="streamable-http"（SDK 也支援） :contentReference[oaicite:5]{index=5}
    mcp.run(transport="stdio")
