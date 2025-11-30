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
from datetime import datetime, timedelta

# ================= 基本設定 =================
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL_NAME = "llama3.1:8b-instruct-q4_K_M"
AI_AGENT_URL = "http://127.0.0.1:8001/suggest"  # 假設 ai_agent.py 跑在 8001

BASE_DIR = Path(__file__).resolve().parent
TRAIN_PATH = BASE_DIR / "setup_train.jsonl"
RAG_PATH = BASE_DIR / "setup_rag.jsonl"

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
            "business_hours": None,
            "dining_policy": None,
            "tables": None,
            "slot_policy": None
        },
        "suggestion": None,
        "applied": False,
        "store_id": None,
        "service_id": None,
        "category": "",
        "mode": "collect",   # 🟢 新增：目前在「收集模式」
    }
    return sid

def pretty_suggestion_msg(suggestion: Dict[str, Any]) -> str:
    dp = suggestion.get("dining_policy") or {}
    weekday_min = dp.get("weekday_min")
    weekend_min = dp.get("weekend_min", weekday_min)

    lines = []

    # 用餐時間
    if weekday_min:
        if weekend_min and weekend_min != weekday_min:
            lines.append(f"✅ 用餐時間建議：平日 {weekday_min} 分鐘、週末 {weekend_min} 分鐘。")
        else:
            lines.append(f"✅ 用餐時間建議：全週 {weekday_min} 分鐘。")

    # 線上預訂時段
    tw = suggestion.get("time_windows") or []
    if tw:
        seg_lines = []
        for w in tw:
            wd = w.get("weekday", [])
            begin_at = w.get("begin_at", "")
            end_at = w.get("end_at", "")
            if not wd or not begin_at or not end_at:
                continue

            # 粗略把 weekday 群組成「平日/週末」
            if wd == [1,2,3,4,5]:
                label = "平日"
            elif wd == [6,7]:
                label = "週末"
            elif len(wd) == 7:
                label = "全週"
            else:
                label = "週" + "、週".join(str(d) for d in wd)

            seg_lines.append(f"{label} {begin_at[:-3]}–{end_at[:-3]}")

        if seg_lines:
            lines.append("✅ 建議開放線上預訂時段：\n- " + "\n- ".join(seg_lines))
        else:
            lines.append("✅ 建議線上預訂時段：先全部比照營業時間，之後可依實際狀況再縮窄。")
    else:
        lines.append("✅ 建議線上預訂時段：先比照營業時間全開，之後可依實際狀況再縮窄。")

    # 桌型建議：簡單留一點給現場
    tables = suggestion.get("tables") or []
    if tables:
        t_lines = []
        for t in tables:
            size = t.get("size")
            qty = t.get("qty")
            if not size or not qty:
                continue

            # 小 heuristic：桌數 >=3 就留 1 張給現場，其餘開線上
            if qty >= 3:
                online = qty - 1
                walkin = 1
                t_lines.append(
                    f"{size} 人桌 {qty} 張 → 建議線上開 {online} 張，保留 {walkin} 張給現場候位。"
                )
            else:
                t_lines.append(
                    f"{size} 人桌 {qty} 張 → 建議全數開放線上預訂（現場需求少可再調整）。"
                )

        if t_lines:
            lines.append("✅ 桌型建議：\n- " + "\n- ".join(t_lines))

    # 間隔
    sp = suggestion.get("slot_policy") or {}
    interval_min = sp.get("interval_min")
    if interval_min:
        lines.append(f"✅ 每個預訂時間間隔建議 {interval_min} 分鐘。")

    lines.append("如果覺得 OK，可以直接套用右側的設定；若有想調整的地方，也可以跟我說，例如『週末晚餐先不要開線上』。")

    return "\n".join(lines)

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
你是 PB 撇步的「AI 開通小幫手」的欄位解析器。請用繁體中文理解使用者輸入，但**只輸出 JSON**。

你的唯一工作：從「使用者最新的一句話」裡，試著擷取以下欄位（有就給，沒有就略過，不要亂猜）：

{
  "fields": {
    "business_hours": {
      "segments": [ 
        { "weekday": [int], "begin_at": "HH:mm:00", "end_at": "HH:mm:00" }
      ],
      "closed_weekdays": [int]
    },
    "dining_policy": { "duration_min": int },
    "tables": [ { "size": int, "qty": int } ],
    "slot_policy": { "interval_min": int }
  }
}

規則說明：
- 只輸出一層 JSON 物件，頂層 key 一定是 "fields"。
- 若這一句話完全沒有相關資訊，就回：{ "fields": {} }
- 不要產生其他 key（例如 "type"、"message"、"suggestion" 等等）。
- **不要產生任何說明文字**，也不要加 markdown 或 ```，只輸出純 JSON。

擷取規則舉例：
- 使用者說「平日 17:30-21:30；週末 11:00-21:30」：
  -> business_hours.segments 需拆成平日、週末兩段，weekday 用數字 1~7（週一=1）
- 「週末公休」或「每週二公休」：
  -> 寫入 closed_weekdays，例如 [7] 或 [2]
- 「用餐時間 90 分鐘」：
  -> dining_policy.duration_min = 90
- 「桌型 2人×6、4人×4」：
  -> tables = [ {"size":2,"qty":6}, {"size":4,"qty":4} ]
- 「間隔 30 分鐘」：
  -> slot_policy.interval_min = 30
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
    """把 train 的 input JSON 縮成可閱讀的示例字串（不顯示真實店名）。"""
    sp = inp.get("store_profile", {})
    bh = inp.get("business_hours", [])
    hist = inp.get("history_features", {})

    segs = []
    for b in bh:
        segs.append(f"(週{b.get('weekday')} {b.get('open')}~{b.get('close')})")
    seg_txt = "；".join(segs) if segs else "（營業時間：無）"

    # 這裡用「示例店家」取代真實店名
    return (
        f"示例店家｜類別：{sp.get('category','') or '美食'}｜"
        f"營業：{seg_txt}｜歷史樣本數：{hist.get('raw_count',0)}"
    )

def build_fewshot_text(category: str) -> str: 
    few = pick_fewshot(category, k=2) 
    if not few: 
        return "（無示例）" 
    out = [] 
    for e in few: 
        out_json = e.get("output", {})
        out.append(
            "[示例]\n"
            "輸出JSON：" + json.dumps(out_json, ensure_ascii=False)
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
    prompt = f"""{SYSTEM}

使用者最新回覆：
{user_text}
"""
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "keep_alive": "30m",
        "options": {
            "num_predict": 160,
            "temperature": 0.1,
            "top_p": 0.8,
            "num_ctx": 2048
        }
    }

    try:
        txt = await call_ollama(payload)
    except httpx.ReadTimeout:
        return {"fields": {}}

    # 先試直接 parse
    obj = None
    try:
        obj = json.loads(txt)
    except Exception:
        # 嘗試從第一個 { 到最後一個 } 擷取
        try:
            start = txt.index("{")
            end = txt.rindex("}") + 1
            obj = json.loads(txt[start:end])
        except Exception:
            return {"fields": {}}

    if not isinstance(obj, dict):
        return {"fields": {}}

    fields = obj.get("fields") or {}
    # 👇 這裡把 slots 傳進去，讓 _filter_fields_by_text 知道目前缺什麼欄位
    fields = _filter_fields_by_text(user_text, fields, slots)
    return {"fields": fields}

async def parse_time_preference(user_text: str, suggestion: Dict[str, Any]) -> Dict[str, Any]:
    """
    解析「已經有一版建議後，店家用自然語言說要調整時段」的需求。
    輸出格式統一為：
    {
      "action": "update_time_windows" | "none",
      "time_windows": [
        { "weekday": [int], "begin_at": "HH:mm:00", "end_at": "HH:mm:00" }
      ]
    }
    """
    # 把目前建議當作 context 給模型參考
    cur_suggestion = json.dumps(suggestion or {}, ensure_ascii=False, indent=2)

    preference_system = """
你是 PB 撇步的「訂位時段調整助手」。使用者已經有一版建議設定，現在用自然語言描述他想「怎麼調整線上預訂時段」。

你只需要根據「最新這一句話」，決定是否要更新 time_windows。

請嚴格只輸出 JSON，格式如下：
{
  "action": "update_time_windows" 或 "none",
  "time_windows": [
    {
      "weekday": [1..7],    // 1=週一…7=週日
      "begin_at": "HH:mm:00",
      "end_at": "HH:mm:00"
    }
  ]
}

規則：
- 如果聽得出來使用者有明確指定「哪些天、從幾點到幾點要開放預訂」，就把 action 設為 "update_time_windows"，並用最少的 time_windows 列出他要的規則。
- 若描述只講平日（如「平日 9 點到 16 點」）→ weekday = [1,2,3,4,5]
- 若描述只講週末 → weekday = [6,7]
- 若講「每天」或「全週」→ weekday = [1,2,3,4,5,6,7]
- 若完全聽不出來要調整什麼，就回：
  { "action": "none", "time_windows": [] }

注意：
- 不要動用餐時間、桌型、間隔等欄位，只管理 time_windows。
- 時間一律轉成 24 小時制 HH:mm:00。
"""

    prompt = f"""{preference_system}

【目前的建議設定（供你參考，不用全部複製）】
{cur_suggestion}

使用者希望的調整：
{user_text}
"""

    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "keep_alive": "30m",
        "options": {
            "num_predict": 200,
            "temperature": 0.1,
            "top_p": 0.8,
            "num_ctx": 2048,
        },
    }

    try:
        txt = await call_ollama(payload)
    except httpx.ReadTimeout:
        return {"action": "none", "time_windows": []}

    # 嘗試解析 JSON
    try:
        obj = json.loads(txt)
    except Exception:
        try:
            start = txt.index("{")
            end = txt.rindex("}") + 1
            obj = json.loads(txt[start:end])
        except Exception:
            return {"action": "none", "time_windows": []}

    if not isinstance(obj, dict):
        return {"action": "none", "time_windows": []}

    action = obj.get("action") or "none"
    tw = obj.get("time_windows") or []

    # 做一點基本合法性檢查
    norm_tw = []
    for w in tw:
        weekdays = w.get("weekday") or []
        begin_at = w.get("begin_at") or ""
        end_at = w.get("end_at") or ""
        if not weekdays or not begin_at or not end_at:
            continue
        norm_tw.append({
            "weekday": [int(d) for d in weekdays],
            "begin_at": begin_at,
            "end_at": end_at,
        })

    if action != "update_time_windows" or not norm_tw:
        return {"action": "none", "time_windows": []}

    return {"action": "update_time_windows", "time_windows": norm_tw}


async def call_ai_agent_from_chat(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    從目前 slots + meta 組一個 context 給 ai_agent，
    讓它依營業時間/用餐時間/桌型/間隔算出建議。
    """
    ctx = {
        "business_hours": state["slots"].get("business_hours"),
        "dining_policy": state["slots"].get("dining_policy"),
        "tables": state["slots"].get("tables"),
        "slot_policy": state["slots"].get("slot_policy"),
    }

    payload = {
        "store_id": state.get("store_id") or 0,
        "service_id": state.get("service_id") or 0,
        "context": json.dumps(ctx, ensure_ascii=False)
    }

    async with httpx.AsyncClient(timeout=60) as cli:
        r = await cli.post(AI_AGENT_URL, json=payload)
        r.raise_for_status()
        return r.json()  # 就是 ai_agent.Suggestion 的 dict

def merge_slots(slots, fields):
    """
    只在「原本沒有值」時才寫入，避免 LLM 回傳的空物件把已擷取的欄位蓋掉。
    之後如果你真的要「覆蓋更新」，可以另外做重設功能。
    """
    for k in ["business_hours", "dining_policy", "tables", "slot_policy"]:
        if k not in fields:
            continue

        new_val = fields.get(k)

        # 1) 完全沒有就略過
        if new_val is None:
            continue

        old_val = slots.get(k)

        # 2) 如果原本已經有值，就不要輕易覆蓋
        #   （先求穩定：寧可不更新，也不要把完整資訊變成空的）
        if old_val:
            # 針對 business_hours 加一個保護：
            if k == "business_hours":
                # 如果新的沒有 segments 或 segments 為空，就不覆蓋
                segs = (new_val or {}).get("segments") or []
                if not segs:
                    continue

            # 其他欄位先一律「舊的優先」，之後真的想支援覆寫再調整
            continue

        # 3) 原本是空的 → 才寫入新的
        slots[k] = new_val

    return slots

def to_preview(suggestion: Dict[str, Any], slots: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "dining_policy": suggestion.get("dining_policy"),
        "tables": suggestion.get("tables"),
        "time_windows": suggestion.get("time_windows"),
        # 先用 suggest 的；沒有就退回 slots（collect 階段填過的）
        "slot_policy": suggestion.get("slot_policy") or (slots.get("slot_policy") or {}),
    }

def _filter_fields_by_text(user_text: str, fields: Dict[str, Any], slots: Dict[str, Any]) -> Dict[str, Any]:
    """
    根據使用者輸入的內容 + 目前缺哪些欄位，決定要保留哪些解析到的欄位。
    避免 LLM 亂湊不相關欄位。
    """
    t = user_text.strip()

    # 判斷目前還缺哪些欄位
    missing = missing_fields(slots)

    # 判斷這句話本身的關鍵字
    wants_bh = any(w in t for w in ["平日", "週末", "公休", "營業", "：", ":"])
    wants_dp = any(w in t for w in ["用餐", "分鐘", "分"])
    wants_tb = any(w in t for w in ["人", "桌", "×", "x", "*"])

    # slot_policy：兩種情境都要吃
    # 1) 句子提到「間隔 / 幾分鐘 / 每」這種字
    # 2) 目前唯一缺的是 slot_policy，而且使用者只輸入數字（例如「15」）
    wants_sp = any(w in t for w in ["間隔", "幾分鐘"])
    if "slot_policy.interval_min" in missing and t.isdigit():
        wants_sp = True

    cleaned = {}
    if wants_bh and "business_hours" in fields:
        cleaned["business_hours"] = fields["business_hours"]
    if wants_dp and "dining_policy" in fields:
        cleaned["dining_policy"] = fields["dining_policy"]
    if wants_tb and "tables" in fields:
        cleaned["tables"] = fields["tables"]
    if wants_sp and "slot_policy" in fields:
        cleaned["slot_policy"] = fields["slot_policy"]

    return cleaned



def simple_suggestion_from_slots(slots: Dict[str, Any]) -> Dict[str, Any]:
    """
    當所有欄位都齊時，先用一個簡單 rule-based 建議，
    這樣右側「預覽設定」可以先跑起來，之後你要再接 ai_agent 也很容易。
    """
    bh = slots.get("business_hours") or {}
    segs = bh.get("segments") or []

    # time_windows：直接等於營業時段（你之後可以改成只取收客範圍）
    time_windows = []
    for s in segs:
        time_windows.append({
            "weekday": s.get("weekday", []),
            "begin_at": s.get("begin_at", "11:00:00"),
            "end_at":   s.get("end_at", "21:00:00"),
        })

    dp = slots.get("dining_policy") or {}
    duration_min = dp.get("duration_min", 90)

    tables = slots.get("tables") or []
    sp = slots.get("slot_policy") or {}
    interval_min = sp.get("interval_min", 30)

    return {
        "dining_policy": {
            "duration_min": duration_min,
            "weekday_min": duration_min,
            "weekend_min": duration_min,
        },
        "tables": tables,
        "time_windows": time_windows,
        "slot_policy": {
            "interval_min": interval_min
        }
    }

def _add_minutes(hhmmss: str, minutes: int) -> str:
    t = datetime.strptime(hhmmss, "%H:%M:%S")
    t2 = t + timedelta(minutes=minutes)
    return t2.strftime("%H:%M:%S")

def convert_ai_agent_to_chat_suggestion(s: Dict[str, Any]) -> Dict[str, Any]:
    """
    ai_agent 給的是：
      duration: weekday_min/weekend_min
      table_mix: t2/t4/t5
      time_windows: [{weekday, begin_at, duration_min}]
    這裡把它轉成開通小幫手右側 preview 用的格式。
    """
    # 1) 用餐時間
    dur = s.get("duration") or {}
    weekday_min = dur.get("weekday_min", 60)
    weekend_min = dur.get("weekend_min", weekday_min)

    # 2) 桌型：t2/t4/t5 -> {size, qty}
    tm = s.get("table_mix") or {}
    tables = []
    if tm.get("t2", 0) > 0:
        tables.append({"size": 2, "qty": tm["t2"]})
    if tm.get("t4", 0) > 0:
        tables.append({"size": 4, "qty": tm["t4"]})
    if tm.get("t5", 0) > 0:
        tables.append({"size": 5, "qty": tm["t5"]})

    # 3) time_windows: begin_at + duration_min -> begin_at + end_at
    tw_out = []
    for w in s.get("time_windows") or []:
        begin_at = w.get("begin_at", "11:30:00")
        dur_min = w.get("duration_min", weekday_min)
        end_at = _add_minutes(begin_at, dur_min)
        tw_out.append({
            "weekday": w.get("weekday", []),
            "begin_at": begin_at,
            "end_at": end_at
        })

    # 4) slot_policy：先固定 30，之後你可以改成讀 ai_agent 多給的欄位
    slot_policy = {"interval_min": 30}

    return {
        "dining_policy": {
            "duration_min": weekday_min,
            "weekday_min": weekday_min,
            "weekend_min": weekend_min,
        },
        "tables": tables,
        "time_windows": tw_out,
        "slot_policy": slot_policy,
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
    # 1) 取得 session 狀態
    sid = get_session_id(session)
    state = SESS[sid]

    # 2) 拿表單文字
    form = await request.form()
    text = (form.get("text") or "").strip()
    if not text:
        return RedirectResponse("/", status_code=302)

    # 3) 記錄使用者訊息
    state["messages"].append({"role": "user", "text": text})

    # 3.5) 若已在「建議模式」，優先視為「調整偏好」，不再重跑收集 + 建議
    if state.get("mode") == "suggested":
        # 已經有建議才有調整的意義
        if not state.get("suggestion"):
            state["mode"] = "collect"
        else:
            # 呼叫偏好 parser，嘗試更新 time_windows
            pref = await parse_time_preference(text, state["suggestion"] or {})

            if pref.get("action") == "update_time_windows" and pref.get("time_windows"):
                state["suggestion"]["time_windows"] = pref["time_windows"]
                # 回饋使用者新設定（簡單 summary）
                tw = pref["time_windows"]
                seg_lines = []
                for w in tw:
                    wd = w.get("weekday", [])
                    begin_at = w.get("begin_at", "")
                    end_at = w.get("end_at", "")
                    if not wd or not begin_at or not end_at:
                        continue
                    if wd == [1,2,3,4,5]:
                        label = "平日"
                    elif wd == [6,7]:
                        label = "週末"
                    elif len(wd) == 7:
                        label = "全週"
                    else:
                        label = "週" + "、週".join(str(d) for d in wd)
                    seg_lines.append(f"{label} {begin_at[:-3]}–{end_at[:-3]}")

                if seg_lines:
                    msg = "已依照你的偏好更新線上預訂時段：\n- " + "\n- ".join(seg_lines) + "\n右側預覽設定已同步更新。"
                else:
                    msg = "已依照你的偏好更新線上預訂時段，右側預覽設定已同步更新。"

                state["messages"].append({"role": "ai", "text": msg})
                return RedirectResponse("/", status_code=302)

            # 若 parser 判斷為 action:"none" 或解析失敗，就回應說目前還只支援基本調整
            state["messages"].append({
                "role": "ai",
                "text": (
                    "目前我有先幫你算出一版設定建議，右側可以預覽 / 套用。\n"
                    "對於「調整預訂時段」的描述，如果可以，請用類似格式再講一次，例如：\n"
                    "「平日 09:00 到 16:00 都開放預訂」或「週末 11:00–21:00 開線上」"
                )
            })
            return RedirectResponse("/", status_code=302)

    # 🔹 走到這裡代表 mode != 'suggested'，還在「收集模式」

    # 3.6) 若目前只缺「slot_policy.interval_min」而且這句是純數字，就直接當作間隔分鐘數，不丟給 LLM
    missing = missing_fields(state["slots"])
    if "slot_policy.interval_min" in missing and text.isdigit():
        state["slots"]["slot_policy"] = {"interval_min": int(text)}

        wants = missing_fields(state["slots"])
        if wants:
            next_field = wants[0]
            if state.get("last_asked") == next_field and len(wants) > 1:
                next_field = wants[1]
            state["last_asked"] = next_field
            hint = ask_hint_for(next_field)
            state["messages"].append({"role": "ai", "text": hint})
        else:
            # ✅ 第一次收集完，直接算建議，並把 mode 改成 suggested
            state["last_asked"] = None
            state["messages"].append({"role": "ai", "text": "資料齊了，我來幫你算一版線上預訂設定建議。"})

            try:
                ai_raw = await call_ai_agent_from_chat(state)
                suggestion = convert_ai_agent_to_chat_suggestion(ai_raw)
                state["suggestion"] = suggestion
                state["mode"] = "suggested"   # 進入建議模式

                msg = pretty_suggestion_msg(suggestion)
                state["messages"].append({"role": "ai", "text": msg})
            except Exception:
                suggestion = simple_suggestion_from_slots(state["slots"])
                state["suggestion"] = suggestion
                state["mode"] = "suggested"

                state["messages"].append({
                    "role": "ai",
                    "text": "我在叫分析引擎時有點問題，先用你填的營業時間直接推一版基本設定，右側可以先預覽、之後再微調。"
                })

        return RedirectResponse("/", status_code=302)

    # 4) 其他情況才請 LLM 當「欄位解析器」
    res = await ask_llm(
        text,
        state["slots"],
        category=state.get("category", ""),
        store_id=state.get("store_id"),
        service_id=state.get("service_id")
    )

    fields = res.get("fields") or {}

    # 5) 有解析到欄位就 merge 進 slots
    if fields:
        state["slots"] = merge_slots(state["slots"], fields)

    # 6) 檢查還缺哪些欄位
    wants = missing_fields(state["slots"])

    if wants:
        next_field = wants[0]
        if state.get("last_asked") == next_field and len(wants) > 1:
            next_field = wants[1]

        state["last_asked"] = next_field
        hint = ask_hint_for(next_field)
        state["messages"].append({"role": "ai", "text": hint})
    else:
        # ✅ 全部欄位都齊了 → 叫 ai_agent 幫你算「真正的建議」
        state["last_asked"] = None
        state["messages"].append({"role": "ai", "text": "資料齊了，我來幫你算一版線上預訂設定建議。"})

        try:
            ai_raw = await call_ai_agent_from_chat(state)
            suggestion = convert_ai_agent_to_chat_suggestion(ai_raw)
            state["suggestion"] = suggestion
            state["mode"] = "suggested"

            msg = pretty_suggestion_msg(suggestion)
            state["messages"].append({"role": "ai", "text": msg})
        except Exception:
            suggestion = simple_suggestion_from_slots(state["slots"])
            state["suggestion"] = suggestion
            state["mode"] = "suggested"

            state["messages"].append({
                "role": "ai",
                "text": "我在叫分析引擎時有點問題，先用你填的營業時間直接推一版基本設定，右側可以先預覽、之後再微調。"
            })

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
