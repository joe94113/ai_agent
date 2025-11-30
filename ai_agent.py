from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, conint
from typing import List
import httpx, json
from jsonschema import validate, ValidationError

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL_NAME = "llama3.1:8b-instruct-q4_K_M"

app = FastAPI()

# ---- Pydantic models ----
class Duration(BaseModel):
    weekday_min: conint(gt=0)
    weekend_min: conint(gt=0)
    confidence: float = Field(ge=0, le=1)
    rationale: str

class TableMix(BaseModel):
    t2: conint(ge=0)
    t4: conint(ge=0)
    t5: conint(ge=0)
    confidence: float = Field(ge=0, le=1)
    rationale: str

class TimeWindow(BaseModel):
    weekday: List[int]  # 1..7
    begin_at: str       # "HH:mm:00"
    duration_min: conint(gt=0)

class Suggestion(BaseModel):
    duration: Duration
    table_mix: TableMix
    time_windows: List[TimeWindow]

class Req(BaseModel):
    store_id: int
    service_id: int
    context: str

# ---- JSON Schema（雙重保險）----
SCHEMA = { 
  "type": "object", 
  "required": ["duration", "table_mix", "time_windows"], 
  "properties": { 
    "duration": { 
      "type": "object", 
      "required": ["weekday_min","weekend_min","confidence","rationale"], 
      "properties": { 
        "weekday_min": {"type":"integer"},   # 先拿掉 minimum:30
        "weekend_min": {"type":"integer"},   # 先拿掉 minimum:30
        "confidence":  {"type":"number","minimum":0,"maximum":1}, 
        "rationale":   {"type":"string","minLength":3} 
      } 
    }, 
    "table_mix": { 
      "type":"object", 
      "required":["t2","t4","t5","confidence","rationale"], 
      "properties":{ 
        "t2":{"type":"integer","minimum":0}, 
        "t4":{"type":"integer","minimum":0}, 
        "t5":{"type":"integer","minimum":0}, 
        "confidence":{"type":"number","minimum":0,"maximum":1}, 
        "rationale":{"type":"string","minLength":3} 
      } 
    }, 
    "time_windows": {
      "type":"array",   # 拿掉 minItems:1
      "items":{ 
        "type":"object", 
        "required":["weekday","begin_at","duration_min"], 
        "properties":{ 
          "weekday":{
            "type":"array",
            "items":{"type":"integer","minimum":1,"maximum":7}
            # 拿掉 weekday 的 minItems
          }, 
          "begin_at":{"type":"string","pattern":"^\\d{2}:\\d{2}:\\d{2}$"}, 
          "duration_min":{"type":"integer"}  # 拿掉 minimum:30
        } 
      } 
    } 
  } 
}

SYSTEM_INSTR = """你是 PB 撇步的餐飲預約設定助手。
嚴格只輸出 JSON，結構如下：
{
  "duration": { "weekday_min": int, "weekend_min": int, "confidence": float, "rationale": str },
  "table_mix": { "t2": int, "t4": int, "t5": int, "confidence": float, "rationale": str },
  "time_windows": [ { "weekday": [int], "begin_at": "HH:mm:00", "duration_min": int } ]
}
規則：
- 所有 int 欄位不可為 null，若沒有資料請填 0 或沿用 weekday_min。
不要輸出任何多餘文字（例如解釋、markdown、```）。"""

PROMPT_TMPL = """{sys}

店家背景與限制：
{ctx}

請依上方規則產生設定，僅輸出 JSON。
"""

def _normalize_suggestion_obj(obj: dict) -> dict:
    """
    把模型回傳的 dict 做基本合法化：
    - weekday_min 為 None 或 <=0 時，補成 60
    - weekend_min 為 None 或 <=0 時，補成 weekday_min
    - t2/t4/t5 為 None 時，改成 0
    - rationale 為空或太短時，補上預設說明文字
    """
    # ---- duration ----
    dur = obj.get("duration") or {}
    wk = dur.get("weekday_min")
    we = dur.get("weekend_min")

    # weekday_min：沒有或 <=0 就給 60 當 fallback
    if wk is None or wk <= 0:
        wk = 60
        dur["weekday_min"] = wk

    # weekend_min：沒有或 <=0 都用 weekday_min
    if we is None or we <= 0:
        dur["weekend_min"] = wk

    # rationale：長度 < 3 就補預設句
    dr = dur.get("rationale")
    if not isinstance(dr, str) or len(dr.strip()) < 3:
        dur["rationale"] = "依店家提供的用餐時間與營業時段產生建議。"

    obj["duration"] = dur

    # ---- table_mix ----
    tm = obj.get("table_mix") or {}
    for k in ("t2", "t4", "t5"):
        if tm.get(k) is None:
            tm[k] = 0

    tr = tm.get("rationale")
    if not isinstance(tr, str) or len(tr.strip()) < 3:
        tm["rationale"] = "根據尖峰來客人數與人數分布推估桌型組合。"

    obj["table_mix"] = tm

    return obj


@app.post("/suggest", response_model=Suggestion)
async def suggest(req: Req):
    prompt = PROMPT_TMPL.format(sys=SYSTEM_INSTR, ctx=req.context)
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(OLLAMA_URL, json={
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False
        })
        r.raise_for_status()
        txt = r.json().get("response","").strip()

    print("=== [ai_agent raw LLM] ===")
    print(txt)
    print("=== [end raw] ===")

    try:
        # 第一輪：直接 parse
        try:
            obj = json.loads(txt)
        except json.JSONDecodeError:
            # 第二輪：從第一個 { 到最後一個 } 擷取，濾掉 ``` 這種 code fence
            try:
                start = txt.index("{")
                end = txt.rindex("}") + 1
                obj = json.loads(txt[start:end])
            except Exception as e:
                # 真的救不回來才丟錯
                raise json.JSONDecodeError(str(e), txt, 0)

        # 做合法化（weekend_min <=0 / rationale 空字串 等都在這裡處理）
        obj = _normalize_suggestion_obj(obj)

        validate(instance=obj, schema=SCHEMA)

        print("=== [ai_agent normalized] ===")
        print(json.dumps(obj, ensure_ascii=False, indent=2))
        print("=== [end normalized] ===")

        return Suggestion(**obj)

    except (json.JSONDecodeError, ValidationError) as e:
        print("=== [ai_agent validation error] ===")
        print(repr(e))
        print("=== [end error] ===")
        raise HTTPException(status_code=422, detail=f"模型回覆非合法JSON: {e}")
