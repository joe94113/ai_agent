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
        "weekday_min": {"type":"integer","minimum":30},
        "weekend_min": {"type":"integer","minimum":30},
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
      "type":"array","minItems":1,
      "items":{
        "type":"object",
        "required":["weekday","begin_at","duration_min"],
        "properties":{
          "weekday":{"type":"array","items":{"type":"integer","minimum":1,"maximum":7},"minItems":1},
          "begin_at":{"type":"string","pattern":"^\\d{2}:\\d{2}:\\d{2}$"},
          "duration_min":{"type":"integer","minimum":30}
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
不要輸出任何多餘文字（例如解釋、markdown、```）。"""

PROMPT_TMPL = """{sys}

店家背景與限制：
{ctx}

請依上方規則產生設定，僅輸出 JSON。
"""

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

    # 嘗試解析 + schema 驗證
    try:
        obj = json.loads(txt)
        validate(instance=obj, schema=SCHEMA)
        # Pydantic 再驗一次型別
        return Suggestion(**obj)
    except (json.JSONDecodeError, ValidationError) as e:
        raise HTTPException(status_code=422, detail=f"模型回覆非合法JSON: {e}")
