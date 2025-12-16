import json
import requests
import re
from typing import Dict, Any, Optional

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "llama3.1"  # 你用的模型名稱

SYSTEM_PROMPT = """
你是一個「新商家線上訂位設定助手」AI Agent，任務是用多輪對話蒐集資料，最後產生設定 JSON。

你需要蒐集的資料（缺一不可）：
1) store_name（店名）
2) resources（桌型清單，任意人數桌都可以）：
   - 每筆包含 party_size（整數，桌子標準可坐幾人）、spots_total（整數，這種桌子幾張）
3) duration_sec（整數秒數，例如 90 分鐘=5400）
4) business_hours_json（GMB 格式，day:0-6, time:"HHMM"）
   - 可支援多段，例如週末午晚分段
5) strategy：
   - goal_type: fill_seats | control_queue | keep_walkin
   - online_role: primary | assistant | minimal
   - peak_periods: weekday_lunch/weekday_dinner/weekend_brunch/weekend_dinner（可多選）
   - peak_strategy: online_first | walkin_first | no_online
   - no_show_tolerance: low | medium | high
   - can_merge_tables: true/false
   - max_party_size: 整數（例如 8/10/12）

你必須遵守：
- 對話時用自然中文問問題，一次問 1~2 個問題，避免一口氣問太多。
- 若使用者回覆不完整，你要追問缺的部分。
- 你要維持一個「內部狀態 state」，並且在每次回覆最後輸出一行：
  STATE_PATCH: <JSON>
  這個 JSON 只包含本輪你新增或修正的欄位（partial update），例如：
  STATE_PATCH: {"store_name":"赤客燒肉"}
  STATE_PATCH: {"resources":[{"party_size":4,"spots_total":10}]}

- 當你確認資料齊全後，你要輸出：
  FINAL_JSON: <JSON>
  且 <JSON> 必須是合法 JSON、完全符合 schema、所有欄位都是字面值（不允許 90*60）。

- 若尚未蒐集完，不要輸出 FINAL_JSON。
- 注意 business_hours_json 的 time 一律為 "HHMM"（4 位），不要輸出 "173000" 或 "17:30"。
"""

def call_ollama(messages):
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "stream": False
    }
    resp = requests.post(OLLAMA_URL, json=payload)
    resp.raise_for_status()
    return resp.json()["message"]["content"]

def extract_json_after_prefix(text: str, prefix: str) -> Optional[Dict[str, Any]]:
    """
    從回覆中抓 prefix: 後面的 JSON
    """
    m = re.search(rf"{re.escape(prefix)}\s*(\{{.*\}}|\[.*\])\s*$", text.strip(), flags=re.S)
    if not m:
        return None
    raw = m.group(1).strip()
    try:
        return json.loads(raw)
    except Exception:
        return None

def validate_time_hhmm(bh: list) -> bool:
    """
    簡單檢查 business_hours_json time 是否為 4 位 HHMM
    """
    hhmm_re = re.compile(r"^\d{4}$")
    for p in bh:
        ot = p.get("open", {}).get("time", "")
        ct = p.get("close", {}).get("time", "")
        if not hhmm_re.match(str(ot)) or not hhmm_re.match(str(ct)):
            return False
    return True

def is_complete_state(state: Dict[str, Any]) -> bool:
    required_top = ["store_name", "resources", "duration_sec", "business_hours_json", "strategy"]
    for k in required_top:
        if k not in state or state[k] in (None, "", []):
            return False

    # resources
    if not isinstance(state["resources"], list) or len(state["resources"]) == 0:
        return False
    for r in state["resources"]:
        if not isinstance(r, dict):
            return False
        if "party_size" not in r or "spots_total" not in r:
            return False

    # duration_sec
    if not isinstance(state["duration_sec"], int):
        return False

    # business_hours_json
    if not isinstance(state["business_hours_json"], list) or len(state["business_hours_json"]) == 0:
        return False
    if not validate_time_hhmm(state["business_hours_json"]):
        return False

    # strategy
    s = state["strategy"]
    need = ["goal_type", "online_role", "peak_periods", "peak_strategy", "no_show_tolerance", "can_merge_tables", "max_party_size"]
    if not isinstance(s, dict):
        return False
    for k in need:
        if k not in s:
            return False

    return True

def main():
    state: Dict[str, Any] = {
        "store_id": None  # 新商家通常沒有，先固定
    }

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "我們開始吧。請先問我店名。"}
    ]

    print("✅ Onboarding Agent（多輪蒐集資料）已啟動。輸入 exit 離開。\n")

    while True:
        assistant_text = call_ollama(messages)
        print("\n🤖 Agent：")
        print(assistant_text)

        # 解析 state patch
        patch = extract_json_after_prefix(assistant_text, "STATE_PATCH:")
        if patch:
            # merge patch into state（簡單 merge：同名覆蓋）
            for k, v in patch.items():
                state[k] = v

        # 解析 final json（若完成）
        final = extract_json_after_prefix(assistant_text, "FINAL_JSON:")
        if final:
            print("\n✅ Agent 產出 FINAL_JSON（已完成蒐集）")
            print(json.dumps(final, ensure_ascii=False, indent=2))
            break

        # 你也可以在每輪顯示目前 state（debug 用）
        # print("\n[DEBUG] Current State:", json.dumps(state, ensure_ascii=False, indent=2))

        # 若 state 其實已完整，但 LLM 沒輸出 FINAL_JSON，我們提示它收尾
        if is_complete_state(state):
            messages.append({"role": "user", "content": "資料看起來已齊全，請輸出 FINAL_JSON。"})
            continue

        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            print("Bye")
            break

        messages.append({"role": "assistant", "content": assistant_text})
        messages.append({"role": "user", "content": user_in})

if __name__ == "__main__":
    main()
