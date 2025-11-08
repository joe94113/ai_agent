# -*- coding: utf-8 -*-
import json, uuid
from pathlib import Path
from typing import List, Dict, Any, Optional

import os
import httpx
import asyncio
from fastapi import FastAPI, Request, HTTPException, Cookie, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from itsdangerous import Signer
from pydantic import BaseModel, Field, conint
from fastapi import FastAPI
from contextlib import asynccontextmanager
from typing import Optional  # 已經有就略過

# ================= 基本設定 =================
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL_NAME = "llama3.1:8b-instruct-q4_K_M"

TRAIN_PATH = Path("/setup_train.jsonl")  # 你提供的 few-shot
RAG_PATH   = Path("/setup_rag.jsonl")    # 你提供的 rag

HTTP: Optional[httpx.AsyncClient] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global HTTP
    # 更細的逾時：連線 5s、寫 30s、讀 180s、池 180s
    HTTP = httpx.AsyncClient(timeout=httpx.Timeout(connect=5, write=30, read=180, pool=180))
    # 預熱：把模型常駐到記憶體
    try:
        await HTTP.post(OLLAMA_URL, json={
            "model": MODEL_NAME,
            "prompt": "ping",
            "stream": False,
            "keep_alive": "30m"   # 延長常駐時間
        })
    except Exception:
        pass
    yield
    await HTTP.aclose()

async def call_ollama(payload: dict, retries: int = 3, backoff: float = 1.5) -> str:
    """
    封裝呼叫，含重試與指數退避。回傳 Ollama 的 'response' 純文字。
    """
    last_err = None
    for i in range(retries):
        try:
            r = await HTTP.post(OLLAMA_URL, json=payload)
            r.raise_for_status()
            return r.json().get("response", "").strip()
        except (httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError) as e:
            last_err = e
            # 第一次失敗，嘗試做一次「輕量預熱」避免模型被卸載
            if i == 0:
                try:
                    await HTTP.post(OLLAMA_URL, json={
                        "model": MODEL_NAME, "prompt": "ping", "stream": False, "keep_alive": "30m"
                    })
                except Exception:
                    pass
            await asyncio.sleep(backoff ** i)
    # 全部失敗就把最後一次錯丟出去，讓上層顯示友善訊息
    raise last_err

app = FastAPI(lifespan=lifespan)

try:
    from dotenv import load_dotenv
    load_dotenv()  # 若沒裝可 pip install python-dotenv
except Exception:
    pass

SECRET = os.getenv("PB_SIGNER_SECRET")
if not SECRET:
    raise RuntimeError("缺少 PB_SIGNER_SECRET，請在 .env 設定或以環境變數提供。")

from itsdangerous import Signer
signer = Signer(SECRET)

# ================== 簡易模板 ==================
env = Environment(loader=FileSystemLoader("."), autoescape=select_autoescape())

INDEX_HTML = r"""
<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>PB 撇步｜AI 開通小幫手（測試版）</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans TC", "Helvetica Neue", Arial; margin:0; background:#0b1020; color:#e6edf3;}
  .wrap { max-width: 1200px; margin: 0 auto; padding: 24px;}
  .grid { display:grid; grid-template-columns: 2fr 1fr; gap: 16px;}
  .card { background:#0f172a; border:1px solid #1f2937; border-radius:12px; padding:16px;}
  .title { font-size:20px; font-weight:700; margin:0 0 6px;}
  .sub { opacity:.75; margin:0 0 12px; font-size:14px;}
  .msg { padding:10px 12px; border-radius:10px; margin:8px 0; line-height:1.5; white-space:pre-wrap;}
  .msg.user { background:#1f2937;}
  .msg.ai   { background:#111827;}
  .msg.sys  { background:#0b1220; border:1px dashed #24334d; opacity:.9;}
  .row { display:flex; gap:8px; }
  input[type=text], textarea, input[type=number] { width:100%; background:#0b1220; border:1px solid #1f2937; color:#e6edf3; border-radius:10px; padding:10px 12px; }
  textarea{ min-height:72px; }
  button { background:#2563eb; color:#fff; border:none; border-radius:10px; padding:10px 14px; cursor:pointer; }
  button.secondary { background:#374151; }
  .pill { display:inline-block; background:#1f2937; padding:4px 8px; border-radius:999px; margin-right:6px; font-size:12px; }
  .code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size:12px; background:#0b1220; padding:10px; border-radius:10px; border:1px solid #1f2937; overflow:auto; max-height:260px;}
  .dim { opacity:.8; }
  .kv { display:grid; grid-template-columns: 110px 1fr; gap:8px; align-items:center; }
  .sep { height:12px; }
</style>
</head>
<body>
  <div class="wrap">
    <h1 style="margin:0 0 12px;">PB 撇步｜AI 開通小幫手（測試版）</h1>
    <p class="sub">商家用聊天方式填資料。AI 會自動追問：<span class="pill">營業時段/店休日</span><span class="pill">翻桌(平/假日)</span><span class="pill">桌型張數/可拆桌</span>，填齊後輸出設定供預覽/套用。</p>

    <div class="grid">
      <!-- 左：聊天 -->
      <div class="card">
        <div class="title">對話</div>
        <div id="chatbox">
          {% for m in messages %}
            <div class="msg {{ m.role }}">
              <strong>{{ "你" if m.role=="user" else ("AI" if m.role=="ai" else "系統") }}：</strong>
              <div>{{ m.text }}</div>
            </div>
          {% endfor %}
        </div>
        <form id="chatForm" class="row" style="margin-top:12px;" method="post" action="/chat">
          <input id="msg" name="text" type="text" placeholder="輸入訊息，例如：週二公休；平日 11:30-14:30 / 17:30-21:30；週末 11:00-21:30；翻桌平日90、週末105；桌型 2人6、4人8、5人2，允許拆桌"/>
          <button type="submit">送出</button>
          <button class="secondary" formaction="/reset" formmethod="post">重設</button>
        </form>
      </div>

      <!-- 右：設定 / 擷取欄位 / 預覽 -->
      <div class="card">
        <div class="title">連動設定</div>
        <form class="kv" method="post" action="/setmeta">
          <label>store_id</label><input type="number" name="store_id" value="{{ store_id or '' }}" placeholder="例如 4058"/>
          <label>service_id</label><input type="number" name="service_id" value="{{ service_id or '' }}" placeholder="例如 1"/>
          <label>category</label><input type="text"   name="category" value="{{ category or '' }}" placeholder="例如 美食/火鍋/燒肉"/>
          <div></div><button type="submit" class="secondary">儲存</button>
        </form>

        <div class="sep"></div>
        <div class="title">目前擷取的欄位</div>
        <div class="sub">（AI 會逐步填滿；全滿後下方會出現「預覽 & 套用」）</div>
        <div id="slots">
          {{ slots_html | safe }}
        </div>

        <div id="preview" style="margin-top:16px;">
          {% if preview_json %}
            <div class="title" style="margin-top:10px;">預覽設定</div>
            <div class="code">{{ preview_json }}</div>
            <div class="row" style="margin-top:8px;">
              <form method="post" action="/apply/all"><button type="submit">套用（模擬）</button></form>
              <form method="get" action="/"><button type="submit" class="secondary">重新整理</button></form>
            </div>
          {% endif %}
          {% if applied %}
            <p class="ok">✅ 已套用（模擬）：是</p>
          {% endif %}
        </div>

      </div>
    </div>
  </div>
</body>
</html>
"""

template = env.from_string(INDEX_HTML)

# ================= 載入 JSONL（Few-shot 與 RAG） =================
def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    items = []
    if not path.exists():
        return items
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                items.append(json.loads(line))
            except Exception:
                continue
    return items

TRAIN = load_jsonl(TRAIN_PATH)  # 每行：schema_version, instruction, input{...}, output{...}
RAG   = load_jsonl(RAG_PATH)    # 每行：store_id, service_id, chunk_type, text

def pick_fewshot(category: str, k: int = 2) -> List[Dict[str, Any]]:
    """
    從 setup_train.jsonl 選 k 筆示例；盡量同品類，否則取前幾筆。
    你的 train input 是一大段 JSON，我們會直接嵌進示例，模型已能理解。
    """
    if not TRAIN:
        return []
    ex = []
    # 嘗試以 input.store_profile.category 篩選
    if category:
        for r in TRAIN:
            cat = (r.get("input") or {}).get("store_profile", {}).get("category", "")
            if cat == category:
                ex.append(r)
    if not ex:
        ex = TRAIN[:k]
    return ex[:k]

def retrieve_rag(category: str, store_id: Optional[int], service_id: Optional[int], k: int = 4) -> List[Dict[str, Any]]:
    """
    從 setup_rag.jsonl 取證據/政策：
    1) 先找相同 store_id + service_id
    2) 若沒有，再不過濾（因示例檔不含 category，可當通用規則/證據）
    """
    if not RAG:
        return []
    cand = []
    if store_id and service_id:
        cand = [c for c in RAG if c.get("store_id")==store_id and c.get("service_id")==service_id]
    if not cand:
        cand = RAG[:k]
    return cand[:k]

# ================== 會話狀態 ==================
SESS: Dict[str, Dict[str, Any]] = {}

def get_session_id(session_cookie: Optional[str]) -> str:
    if session_cookie:
        try:
            raw = signer.unsign(session_cookie).decode()
            if raw in SESS: return raw
        except Exception:
            pass
    sid = str(uuid.uuid4())
    SESS[sid] = {
        "messages": [
            {"role":"sys","text":"歡迎！請依序提供：1) 營業時段（平日/週末，可多段）與固定店休日、2) 每桌固定用餐時間（分鐘）與最後收客、3) 桌型清單（任意人數×張數，可先大概）。"}
        ],
        "slots": {
            "business_hours": None,        # {"segments":[{"weekday":[int], "begin_at":"HH:mm:00", "end_at":"HH:mm:00"}],
                                        #  "closed_weekdays":[int]}
            "dining_policy": None,         # {"duration_min": int}
            "tables": None,                # [{"size": int, "qty": int}]
            "slot_policy": None            # {"interval_min": int}
        },
        "suggestion": None,
        "applied": False,
        "store_id": None,
        "service_id": None,
        "category": ""
    }
    return sid

def render_slots_html(slots: Dict[str, Any]) -> str:
    def pretty(d): return "<pre class='code'>" + json.dumps(d, ensure_ascii=False, indent=2) + "</pre>"
    html = []
    if slots["business_hours"]:
        html.append("<div><b>營業/店休日（多時段）</b>" + pretty(slots["business_hours"]) + "</div>")
    else:
        html.append("<div class='dim'>營業/店休日：尚未完整</div>")
    if slots["dining_policy"]:
        html.append("<div><b>用餐時間（單一值，分鐘）</b>" + pretty(slots["dining_policy"]) + "</div>")
    else:
        html.append("<div class='dim'>用餐時間：尚未完整</div>")
    if slots["tables"]:
        html.append("<div><b>桌型（大概）</b>" + pretty(slots["tables"]) + "</div>")
    else:
        html.append("<div class='dim'>桌型（大概）：尚未完整</div>")
    if slots.get("slot_policy"):
        html.append("<div><b>可預約間隔</b>" + pretty(slots["slot_policy"]) + "</div>")
    else:
        html.append("<div class='dim'>可預約間隔：尚未完整</div>")
    return "\n".join(html)

# ================== LLM 提示 ==================
SYSTEM = """
你是 PB 撇步的「AI 開通小幫手」。請用自然、口語的繁體中文互動。
每次只針對「一個缺項」發問。

只輸出 JSON，不要 markdown 或多餘文字。格式其一：

1) 追問（單一缺項）：
{ "type": "ask", "message": "口語、簡短地問該缺項，並提供一行可複製的範例" }

2) 收到使用者回覆後，若能擷取欄位，回：
{
  "type": "collect",
  "fields": {
     "business_hours": {
       "segments": [ { "weekday": [int], "begin_at": "HH:mm:00", "end_at": "HH:mm:00" } ],
       "closed_weekdays": [int]
     },
     "dining_policy": { "duration_min": int },
     "tables": [ { "size": int, "qty": int } ],
     "slot_policy": { "interval_min": int }
  }
}

3) 三類都齊全時，回最終建議：
{
  "type": "suggest",
  "suggestion": {
     "dining_policy": { "duration_min": int },
     "tables": [ { "size": int, "qty": int } ],
     "time_windows": [ { "weekday": [int], "begin_at": "HH:mm:00", "end_at": "HH:mm:00" } ],
     "slot_policy": { "interval_min": int }
  }
}

通用規則：
- 時間格式 "HH:mm:00"
- weekday 用 1~7（週一=1…週日=7）
- 只輸出 JSON
"""

def build_context(slots: Dict[str, Any]) -> str:
    parts = []
    if slots["business_hours"]:
        parts.append("【已知】營業/店休日：" + json.dumps(slots["business_hours"], ensure_ascii=False))
    if slots["dining_policy"]:
        parts.append("【已知】用餐時間：" + json.dumps(slots["dining_policy"], ensure_ascii=False))
    if slots["tables"]:
        parts.append("【已知】桌型：" + json.dumps(slots["tables"], ensure_ascii=False))
    if not parts:
        parts.append("尚未擷取任何欄位。")
    return "\n".join(parts)

def missing_fields(slots):
    want = []
    bh = slots.get("business_hours") or {}
    dp = slots.get("dining_policy") or {}
    tb = slots.get("tables") or []
    sp = slots.get("slot_policy") or {}

    segs = bh.get("segments") or []
    if not segs:
        want.append("business_hours.segments")
    else:
        # 檢查每段都有 begin_at / end_at
        for s in segs:
            if not s.get("begin_at") or not s.get("end_at") or not s.get("weekday"):
                want.append("business_hours.segments")
                break

    if not dp.get("duration_min"):
        want.append("dining_policy.duration_min")

    if not tb:
        want.append("tables.list")

    if sp.get("interval_min") is None:
        want.append("slot_policy.interval_min")

    return want

def ask_hint_for(field: str) -> str:
    if field == "business_hours.segments":
        return ("先給我『平日/週末的營業時段』（可多段），用 24H 起訖時間：\n"
                "範例：\n平日：11:30-14:30、17:30-21:30\n週末：11:00-21:30\n"
                "（若某天固定公休，也可補：每週二公休）")
    if field == "dining_policy.duration_min":
        return ("你希望每桌固定用餐時間幾分鐘？\n範例：用餐時間 90 分鐘")
    if field == "tables.list":
        return ("桌子大概有哪幾種、各幾張？（先抓大概即可）\n範例：2人×6、3人×2、4人×5")
    if field == "slot_policy.interval_min":
        return ("每個可預約時段相隔幾分鐘？（常見：15 / 30）\n範例：間隔 30 分鐘")
    return "請補充資訊"

def pretty_train_input(inp: Dict[str, Any]) -> str:
    """把 train 的 input JSON 縮成可閱讀的示例字串。"""
    sp = inp.get("store_profile", {})
    bh = inp.get("business_hours", [])
    hist = inp.get("history_features", {})
    segs = []
    for b in bh:
        segs.append(f"(週{b.get('weekday')} {b.get('open')}~{b.get('close')})")
    seg_txt = "；".join(segs) if segs else "（營業時間：無）"
    return f"店家：{sp.get('name','')}｜類別：{sp.get('category','')}｜地區：{sp.get('county','')}{sp.get('district','')}｜營業：{seg_txt}｜歷史樣本數：{hist.get('raw_count',0)}"

def build_fewshot_text(category: str) -> str:
    few = pick_fewshot(category, k=2)
    if not few: return "（無示例）"
    out = []
    for e in few:
        inp = e.get("input", {})
        out_json = e.get("output", {})
        out.append(
            "[示例]\n使用者："
            + pretty_train_input(inp)
            + "\n輸出JSON："
            + json.dumps(out_json, ensure_ascii=False)
        )
    return "\n\n".join(out)

def build_rag_text(category: str, store_id: Optional[int], service_id: Optional[int]) -> str:
    rag = retrieve_rag(category, store_id, service_id, k=4)
    if not rag: return "（無）"
    lines = []
    for c in rag:
        t = c.get("chunk_type","policy")
        lines.append(f"[{t}] {c.get('text','')}")
    return "\n".join(lines)

def _trim(txt: str, limit: int = 1400) -> str:
    txt = txt.strip()
    return (txt[:limit] + "…") if len(txt) > limit else txt

async def ask_llm(user_text: str, slots: Dict[str, Any], category: str, store_id: Optional[int], service_id: Optional[int]) -> Dict[str, Any]:
    ctx_now = build_context(slots)
    few_txt = build_fewshot_text(category or "美食")
    rag_txt = build_rag_text(category or "美食", store_id, service_id)

    # 只提示「下一個缺項」
    missing = missing_fields(slots)
    next_field = missing[0] if missing else ""
    friendly_hint = ask_hint_for(next_field) if next_field else "（若無缺項，請產出最終建議）"

    prompt = f"""{SYSTEM}

【平台政策與證據（RAG）】
{rag_txt}

【目前已擷取】
{ctx_now}

【下一個要補的欄位】
{next_field or "（無）"}

【口語提問建議（給你參考語氣）】
{friendly_hint}

【已核准的示例（Few-shot）】
{few_txt}

使用者最新回覆：
{user_text}
"""
    # ✂️ 少量示例 & 證據，並裁切
    few_txt = _trim(build_fewshot_text(category or "美食"), 1200)
    rag_txt = _trim(build_rag_text(category or "美食", store_id, service_id), 800)

    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,         # 你的 prompt 如前
        "stream": False,
        "keep_alive": "30m",
        "options": {
            "num_predict": 160,   # JSON 很短，限制生成長度
            "temperature": 0.2,
            "top_p": 0.9,
            "num_ctx": 2048       # 夠用即可，避免超大 context
        }
    }

    try:
        txt = await call_ollama(payload)  # ← 使用重試封裝
    except httpx.ReadTimeout:
        # 友善地回覆使用者，請他補一個短資料點，順便讓下一輪 prompt 更短
        return {"type":"ask","message":"我這邊有點忙不過來🙇 先請你補「下一個欄位」就好（例如：用餐時間 90 分鐘），我再接著弄。"}

    try:
        return json.loads(txt)
    except Exception:
        return {"type":"ask","message": (friendly_hint if next_field else "可以把剛剛的資訊再具體一些嗎？")}

def merge_slots(slots, fields):
    for k in ["business_hours", "dining_policy", "tables", "slot_policy"]:
        if k in fields and fields[k] is not None:
            slots[k] = fields[k]
    return slots

def to_preview(suggestion: Dict[str, Any], slots: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "dining_policy": suggestion.get("dining_policy"),
        "tables": suggestion.get("tables"),
        "time_windows": suggestion.get("time_windows"),
        # 先用 suggest 的；沒有就退回 slots（collect 階段填過的）
        "slot_policy": suggestion.get("slot_policy") or (slots.get("slot_policy") or {}),
    }

# ================= 路由 =================
@app.get("/", response_class=HTMLResponse)
def index(session: Optional[str] = Cookie(None)):
    sid = get_session_id(session)
    state = SESS[sid]
    slots = state["slots"]
    preview_json = None
    if state["suggestion"]:
        preview = to_preview(state["suggestion"], state["slots"])
        preview_json = json.dumps(preview, ensure_ascii=False, indent=2)
    html = template.render(
        messages=state["messages"],
        slots_html=render_slots_html(slots),
        preview_json=preview_json,
        applied="是" if state["applied"] else "",
        store_id=state.get("store_id"),
        service_id=state.get("service_id"),
        category=state.get("category")
    )
    resp = HTMLResponse(html)
    resp.set_cookie("session", signer.sign(sid).decode("utf-8"), httponly=True, samesite="lax")
    return resp

@app.post("/setmeta")
def setmeta(
    session: Optional[str] = Cookie(None),
    store_id: Optional[str] = Form(None),
    service_id: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
):
    sid = get_session_id(session)
    state = SESS[sid]
    state["store_id"] = int(store_id) if store_id else None
    state["service_id"] = int(service_id) if service_id else None
    state["category"] = (category or "").strip()
    state["messages"].append({"role":"sys","text":f"已設定：store_id={state['store_id']}，service_id={state['service_id']}，category={state['category'] or '（未填）'}"})
    return RedirectResponse("/", status_code=302)

@app.post("/chat")
async def chat(request: Request, session: Optional[str] = Cookie(None)):
    sid = get_session_id(session)
    state = SESS[sid]
    form = await request.form()
    # ❌ 刪掉這行：text = (form.get("text") or "").trim()
    text = (form.get("text") or "").strip()   # ✅ 只留這行

    if not text:
        return RedirectResponse("/", status_code=302)

    state["messages"].append({"role":"user","text":text})

    # 呼叫 LLM（帶入 meta 增強 RAG 命中）
    res = await ask_llm(
        text,
        state["slots"],
        category=state.get("category",""),
        store_id=state.get("store_id"),
        service_id=state.get("service_id")
    )

    t = res.get("type")
    if t == "ask":
        msg = res.get("message", "我需要更多資訊。")
        state["messages"].append({"role":"ai","text":msg})
    elif t == "collect":
        fields = res.get("fields", {})
        state["slots"] = merge_slots(state["slots"], fields)

        # 1) 判斷還缺哪些欄位
        wants = missing_fields(state["slots"])

        if wants:
            # 2) 立刻主動追問下一個缺項（只問一個）
            next_field = wants[0]
            if state.get("last_asked") == next_field and next_field.split(".")[0] in state["slots"]:
                if len(wants) > 1:
                    next_field = wants[1]
            state["last_asked"] = next_field
            state["messages"].append({"role":"ai","text": ask_hint_for(next_field)})
        else:
            state["last_asked"] = None
            # 3) 都齊了就直接請模型產出最終建議（可選）
            state["messages"].append({"role":"ai","text":"資料齊了，我來產出建議設定。"})
            res2 = await ask_llm(
                "請產出建議設定",
                state["slots"],
                category=state.get("category",""),
                store_id=state.get("store_id"),
                service_id=state.get("service_id")
            )
            if res2.get("type") == "suggest":
                state["suggestion"] = res2.get("suggestion", {})
                state["messages"].append({"role":"ai","text":"已完成建議設定，右側可預覽並套用。"})
            else:
                # 萬一模型沒回 suggest，就保底再問一次「下一個缺項」
                wants = missing_fields(state["slots"])
                if wants:
                    state["messages"].append({"role":"ai","text": ask_hint_for(wants[0])})
                else:
                    state["messages"].append({"role":"ai","text":"我已記錄你提供的資訊。"})
    elif t == "suggest":
        # ✅ 還缺就不接受 suggest，改為繼續追問
        wants = missing_fields(state["slots"])
        if wants:
            state["messages"].append({"role":"ai","text": ask_hint_for(wants[0])})
        else:
            state["suggestion"] = res.get("suggestion", {})
            state["messages"].append({"role":"ai","text":"已完成建議設定，右側可預覽並套用。"})
    else:
        state["messages"].append({"role":"ai","text":"我收到非預期格式，請再補充一次關鍵資訊～"})

    return RedirectResponse("/", status_code=302)

def _hhmm_to_hhmmss(t: str) -> str:
    # 允許 "HH:mm" 也能吃
    if len(t) == 5 and t[2] == ":":
        return f"{t}:00"
    return t  # 假設已是 HH:mm:00

def _is_time_pair_valid(begin_at: str, end_at: str) -> bool:
    # 只做基本檢查（不跨日），若需跨日可放寬
    bh, bm, _ = map(int, begin_at.split(":"))
    eh, em, _ = map(int, end_at.split(":"))
    return (eh*60 + em) > (bh*60 + bm)

@app.post("/apply/all")
def apply_all(session: Optional[str] = Cookie(None)):
    sid = get_session_id(session)
    state = SESS[sid]
    if not state["suggestion"]:
        raise HTTPException(status_code=400, detail="尚未完成建議，無法套用。")

    preview = to_preview(state["suggestion"], state["slots"])

    # --- 使用 time_windows 的 begin_at + end_at（多段 / 多日） ---
    raw_windows = preview.get("time_windows") or []
    segments = []
    for w in raw_windows:
        weekdays = w.get("weekday", [])
        begin_at = _hhmm_to_hhmmss(w.get("begin_at", ""))
        end_at   = _hhmm_to_hhmmss(w.get("end_at", ""))

        # 若真的缺 end_at（極少數 LLM 回覆），就跳過或自行處理
        if not begin_at or not end_at:
            # 可選：跳過或 raise；這裡選擇跳過以保流程不中斷
            continue

        # 基本有效性檢查
        if not _is_time_pair_valid(begin_at, end_at):
            # 可選：自動糾正 / 跳過；這裡選擇跳過
            continue

        segments.append({
            "weekday": weekdays,
            "begin_at": begin_at,
            "end_at": end_at
        })

    business_payload = {
        "store_id": state.get("store_id"),
        "service_id": state.get("service_id"),
        "segments": segments,
        "closed_weekdays": []  # 若未擷取，先給空陣列
    }
    print("[save-business-hours]\n", json.dumps(business_payload, ensure_ascii=False, indent=2))

    # 用餐固定時間（單一值）維持原本，寫兩者同值或按你實作
    dp = preview.get("dining_policy") or {}
    duration_payload = {
        "store_id": state.get("store_id"),
        "service_id": state.get("service_id"),
        "weekday_min": dp.get("duration_min"),
        "weekend_min": dp.get("duration_min"),
    }
    print("[save-duration-policy]\n", json.dumps(duration_payload, ensure_ascii=False, indent=2))

    tables = preview.get("tables") or []
    table_payloads = []
    for it in tables:
        table_payloads.append({
            "store_id": state.get("store_id"),
            "service_id": state.get("service_id"),
            "party_size": it.get("size"),
            "spots_total": it.get("qty"),
            "enable": 1
        })
    print("[save-table-resources] 逐筆如下：\n", json.dumps(table_payloads, ensure_ascii=False, indent=2))

    sp = preview.get("slot_policy") or {}
    DEFAULT_SLOT_INTERVAL = 30  # 可自訂預設
    slot_payload = {
        "store_id": state.get("store_id"),
        "service_id": state.get("service_id"),
        "slot_interval_min": sp.get("interval_min", DEFAULT_SLOT_INTERVAL)
    }
    print("[save-slot-policy]\n", json.dumps(slot_payload, ensure_ascii=False, indent=2))

    state["applied"] = True
    state["messages"].append({"role":"sys","text":"已套用（模擬）。日後這一步會打 Laravel API。"})
    return RedirectResponse("/", status_code=302)

@app.post("/reset")
def reset(session: Optional[str] = Cookie(None)):
    sid = get_session_id(session)
    if sid in SESS:
        del SESS[sid]
    return RedirectResponse("/", status_code=302)
