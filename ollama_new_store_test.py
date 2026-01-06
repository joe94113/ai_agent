import json
import requests
import re
from typing import Dict, Any, Optional, Tuple, List

# ======================
# 基本設定
# ======================

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "llama3.1:8b-instruct-q4_K_M"

# ======================
# SYSTEM_PROMPT（v4.1.1）
# ======================

SYSTEM_PROMPT = r"""
你是一個「新商家線上訂位設定助手」AI Agent。
目標：透過多輪自然對話蒐集必要資訊，最後輸出一份 **可直接寫入後端資料庫、用於啟用 Google 預訂** 的 FINAL_JSON。

【開場固定腳本（必須照做）】
- 你的第一句一定只問：「請問店名是什麼？」
- 不要提供 A/B/C 範例店名
- 在使用者提供店名之前，STATE_PATCH 必須是空物件：STATE_PATCH: {}

【流程順序固定（必須照做）】
你必須依序完成以下問題，每回合結尾都要提出「下一個問題」：

Step 1 問店名 store_name
Step 2 問桌型 resources（可一次輸入多種桌型，例如：2人桌5張、4人桌8張）
Step 3 問用餐時間（60/90/120 分）
Step 4 問營業時間（例如：每天早八晚五、週日公休）
Step 5 問是否可併桌 + 最大接待人數（例如：可併桌、最大10人）
Step 6 問策略（用情境式 A/B/C）

規則：
- 在尚未完成 Step 6 前，你每次回覆都必須在正文最後「問出下一題」。
- 禁止在未完成前結束對話或只輸出確認文字。

【策略提問強制規則（非常重要）】

- strategy 相關問題 **一次只能問一題**
- 禁止使用「請回答以下幾個項目」這類說法
- 禁止列出多個設定項目清單
- 每一題都必須是「情境式 + A/B/C 選項」
- 使用者如果回覆困惑（例如：我要回答什麼、看不懂）
  → 立刻切換成「簡化模式」，不要再解釋名詞

================================
【平台固定規則（請嚴格遵守）】

- 線上訂位一律至少提前一天
  → strategy.allow_same_day 固定為 false
  → strategy.advance_days_min 固定為 1
- 你不需要、也不得詢問任何「提前多久」相關問題
- 不要向使用者說明「秒數、欄位名稱、系統規則」

================================
【必須蒐集的資料】

1) store_name（店名）

2) resources（桌型，實體桌）：
   - party_size：一張桌子標準可坐幾人（整數）
   - spots_total：這種桌子有幾張（整數）
   - 若使用者說「兩人桌 5 個」，即代表 party_size=2, spots_total=5（不要再追問座位數）

3) duration_sec（用餐時間，整數秒）：
   - 60 分鐘 → 3600
   - 90 分鐘 → 5400
   - 120 分鐘 → 7200
   - 不清楚時預設 5400
   - 不要對使用者顯示「秒」

4) business_hours_json（GMB 營業時間，必須是 list）：
   - 每筆格式：
     {"open":{"day":0-6,"time":"HHMM"},"close":{"day":0-6,"time":"HHMM"}}
   - 若每天固定，必須展開成 7 筆（day 0~6）
   - 星期日公休 → 不要產生 day=0
   - time 只能 4 位 HHMM（0800 / 1730）
   - 若使用者口語（例如「早八晚五」），你要轉成 HHMM，並用一句話確認：「所以是每天 08:00–17:00，對嗎？」

5) 併桌能力：
   - can_merge_tables：true/false
   - max_party_size：整數（例如 8、10、12）
   - 若使用者不回答，預設 can_merge_tables=true, max_party_size=8

6) strategy（用選項題問，不要顯示 enum、不要給 JSON）：
   - goal_type（坐滿/控排隊/保留現場）
   - online_role（主要/輔助/少量）
   - peak_periods（平日午餐/平日晚餐/假日早午餐/假日晚餐，可多選）
   - peak_strategy（尖峰線上為主/現場為主/尖峰不開）
   - peak_online_quota_ratio（0.8/0.5/0.2）
   - no_show_tolerance（低/中/高）
   - min_party_size（1/2/4）

【固定規則】
- strategy.allow_same_day 固定 false
- strategy.advance_days_min 固定 1
- resources 必須是 list，每一筆是一個桌型物件
- 禁止用陣列方式分開 party_size 與 spots_total

================================
【最終輸出 FINAL_JSON（必須符合這個 schema）】

FINAL_JSON 必須包含以下欄位：
{
  "store_id": null 或 整數,
  "store_name": "字串",

  "table_plan": {
    "recommended_tables": [
      {"party_size": 整數, "table_count": 整數}
    ],
    "estimated_capacity": 整數,
    "merge_policy": {
      "can_merge": 布林值,
      "max_party_size": 整數,
      "merge_unit_sizes": [整數...],
      "notes": "字串"
    }
  },

  "booking_time": {
    "business_hours_json": [ ... ],
    "booking_windows": [
      {"day": 0-6, "start": "HHMM", "end": "HHMM"}
    ],
    "slot_openings": [
      {"weekday": 1-7, "time": "HH:MM", "open": 0 或 1}
    ]
  },

  "duration_sec": 整數,

  "strategy": {
    "goal_type": "fill_seats" | "control_queue" | "keep_walkin",
    "online_role": "primary" | "assistant" | "minimal",
    "peak_periods": ["weekday_lunch"|"weekday_dinner"|"weekend_brunch"|"weekend_dinner"],
    "peak_strategy": "online_first" | "walkin_first" | "no_online",
    "peak_online_quota_ratio": 0.8 | 0.5 | 0.2,
    "no_show_tolerance": "low" | "medium" | "high",
    "min_party_size": 1 | 2 | 4,
    "can_merge_tables": true/false,
    "max_party_size": 整數,
    "allow_same_day": false,
    "advance_days_min": 1
  }
}

【STATE_PATCH 格式硬規則】
- STATE_PATCH 後面必須是「合法 JSON」，key 必須加雙引號，字串也必須用雙引號。
  ✅ 正確：STATE_PATCH: {"store_name":"成功燒烤"}
  ❌ 錯誤：STATE_PATCH: {store_name: "成功燒烤"}
- 禁止輸出 STORE_NAME: 這種非規格前綴。

【重要】
- FINAL_JSON 只能用前綴「FINAL_JSON:」輸出，不可加粗、不用 code block、輸出後立即結束
- 每輪結尾都必須輸出 STATE_PATCH: {...}（即使是空 {}）
"""

# ======================
# Ollama 呼叫
# ======================

def call_ollama(messages: List[Dict[str, str]]) -> str:
    resp = requests.post(
        OLLAMA_URL,
        json={"model": MODEL_NAME, "messages": messages, "stream": False}
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]

# ======================
# JSON 擷取工具
# ======================

def extract_json_after_prefix(text: str, prefix: str) -> Optional[Any]:
    idx = text.rfind(prefix)
    if idx == -1:
        return None
    raw = text[idx + len(prefix):].strip()
    raw = raw.strip("`").strip()
    # 允許前面有一些空白或換行，但 JSON 必須從 { 開始
    start = raw.find("{")
    if start == -1:
        return None
    raw = raw[start:].strip()
    try:
        return json.loads(raw)
    except Exception:
        return None

# ======================
# Validators（加強版）
# ======================

HHMM_RE = re.compile(r"^\d{4}$")
TIME_COLON_RE = re.compile(r"^\d{2}:\d{2}$")

def _validate_business_hours_json(bh: Any) -> Tuple[bool, str]:
    if not isinstance(bh, list) or len(bh) == 0:
        return False, "business_hours_json 必須是非空 list"
    for i, p in enumerate(bh):
        if not isinstance(p, dict) or "open" not in p or "close" not in p:
            return False, f"business_hours_json[{i}] 必須包含 open/close"
        o, c = p["open"], p["close"]
        if not isinstance(o, dict) or not isinstance(c, dict):
            return False, f"business_hours_json[{i}] open/close 必須是 object"
        if "day" not in o or "time" not in o or "day" not in c or "time" not in c:
            return False, f"business_hours_json[{i}] open/close 必須有 day/time"
        if not (isinstance(o["day"], int) and 0 <= o["day"] <= 6):
            return False, f"business_hours_json[{i}].open.day 必須 0~6"
        if not (isinstance(c["day"], int) and 0 <= c["day"] <= 6):
            return False, f"business_hours_json[{i}].close.day 必須 0~6"
        if not HHMM_RE.match(str(o["time"])) or not HHMM_RE.match(str(c["time"])):
            return False, f"business_hours_json[{i}] time 必須 4 位 HHMM"
    return True, "ok"

def _validate_table_plan(tp: Any) -> Tuple[bool, str]:
    if not isinstance(tp, dict):
        return False, "table_plan 必須是 object"
    for k in ["recommended_tables", "estimated_capacity", "merge_policy"]:
        if k not in tp:
            return False, f"table_plan 缺少 {k}"
    if not isinstance(tp["recommended_tables"], list) or len(tp["recommended_tables"]) == 0:
        return False, "recommended_tables 必須非空 list"
    for r in tp["recommended_tables"]:
        if not isinstance(r, dict) or "party_size" not in r or "table_count" not in r:
            return False, "recommended_tables 每筆需有 party_size/table_count"
        if not isinstance(r["party_size"], int) or r["party_size"] <= 0:
            return False, "recommended_tables.party_size 必須正整數"
        if not isinstance(r["table_count"], int) or r["table_count"] < 0:
            return False, "recommended_tables.table_count 必須整數 >=0"
    if not isinstance(tp["estimated_capacity"], int) or tp["estimated_capacity"] <= 0:
        return False, "estimated_capacity 必須正整數"

    mp = tp["merge_policy"]
    if not isinstance(mp, dict):
        return False, "merge_policy 必須 object"
    for k in ["can_merge", "max_party_size", "merge_unit_sizes", "notes"]:
        if k not in mp:
            return False, f"merge_policy 缺少 {k}"
    if not isinstance(mp["can_merge"], bool):
        return False, "merge_policy.can_merge 必須 boolean"
    if not isinstance(mp["max_party_size"], int) or mp["max_party_size"] <= 0:
        return False, "merge_policy.max_party_size 必須正整數"
    if not isinstance(mp["merge_unit_sizes"], list) or len(mp["merge_unit_sizes"]) == 0:
        return False, "merge_policy.merge_unit_sizes 必須非空 list"
    if not isinstance(mp["notes"], str):
        return False, "merge_policy.notes 必須字串"
    return True, "ok"

def _validate_booking_time(bt: Any) -> Tuple[bool, str]:
    if not isinstance(bt, dict):
        return False, "booking_time 必須是 object"
    for k in ["business_hours_json", "booking_windows", "slot_openings"]:
        if k not in bt:
            return False, f"booking_time 缺少 {k}"

    ok, msg = _validate_business_hours_json(bt["business_hours_json"])
    if not ok:
        return False, f"booking_time.business_hours_json: {msg}"

    bw = bt["booking_windows"]
    if not isinstance(bw, list) or len(bw) == 0:
        return False, "booking_windows 必須非空 list"
    for w in bw:
        if not isinstance(w, dict) or "day" not in w or "start" not in w or "end" not in w:
            return False, "booking_windows 每筆需有 day/start/end"
        if not isinstance(w["day"], int) or not (0 <= w["day"] <= 6):
            return False, "booking_windows.day 必須 0~6"
        if not HHMM_RE.match(str(w["start"])) or not HHMM_RE.match(str(w["end"])):
            return False, "booking_windows start/end 必須 HHMM"

    slots = bt["slot_openings"]
    if not isinstance(slots, list) or len(slots) == 0:
        return False, "slot_openings 必須非空 list"
    for s in slots:
        if not isinstance(s, dict) or "weekday" not in s or "time" not in s or "open" not in s:
            return False, "slot_openings 每筆需有 weekday/time/open"
        if not isinstance(s["weekday"], int) or not (1 <= s["weekday"] <= 7):
            return False, "slot_openings.weekday 必須 1~7"
        if not TIME_COLON_RE.match(str(s["time"])):
            return False, "slot_openings.time 必須 HH:MM"
        if s["open"] not in [0, 1]:
            return False, "slot_openings.open 必須 0/1"
    return True, "ok"

def _validate_strategy(s: Any) -> Tuple[bool, str]:
    if not isinstance(s, dict):
        return False, "strategy 必須 object"

    required = [
        "goal_type","online_role","peak_periods","peak_strategy","peak_online_quota_ratio",
        "no_show_tolerance","min_party_size","can_merge_tables","max_party_size",
        "allow_same_day","advance_days_min"
    ]
    for k in required:
        if k not in s:
            return False, f"strategy 缺少 {k}"

    if s["goal_type"] not in ["fill_seats","control_queue","keep_walkin"]:
        return False, "goal_type 不合法"
    if s["online_role"] not in ["primary","assistant","minimal"]:
        return False, "online_role 不合法"
    if s["peak_strategy"] not in ["online_first","walkin_first","no_online"]:
        return False, "peak_strategy 不合法"
    if s["peak_online_quota_ratio"] not in [0.8, 0.5, 0.2]:
        return False, "peak_online_quota_ratio 必須 0.8/0.5/0.2"
    if s["no_show_tolerance"] not in ["low","medium","high"]:
        return False, "no_show_tolerance 不合法"
    if s["min_party_size"] not in [1,2,4]:
        return False, "min_party_size 必須 1/2/4"
    if not isinstance(s["can_merge_tables"], bool):
        return False, "can_merge_tables 必須 boolean"
    if not isinstance(s["max_party_size"], int) or s["max_party_size"] <= 0:
        return False, "max_party_size 必須正整數"

    if s["allow_same_day"] is not False:
        return False, "allow_same_day 必須 false"
    if s["advance_days_min"] != 1:
        return False, "advance_days_min 必須 1"

    if not isinstance(s["peak_periods"], list):
        return False, "peak_periods 必須 list"
    allowed = {"weekday_lunch","weekday_dinner","weekend_brunch","weekend_dinner"}
    for x in s["peak_periods"]:
        if x not in allowed:
            return False, f"peak_periods 不允許值：{x}"

    return True, "ok"

def validate_final_json(final: Dict[str, Any]) -> Tuple[bool, str]:
    for k in ["store_id","store_name","table_plan","booking_time","duration_sec","strategy"]:
        if k not in final:
            return False, f"缺少欄位 {k}"

    if not isinstance(final["store_name"], str) or not final["store_name"].strip():
        return False, "store_name 必須非空字串"
    if final["store_id"] is not None and not isinstance(final["store_id"], int):
        return False, "store_id 必須 null 或 int"
    if not isinstance(final["duration_sec"], int) or final["duration_sec"] <= 0:
        return False, "duration_sec 必須正整數"

    ok, msg = _validate_table_plan(final["table_plan"])
    if not ok:
        return False, msg
    ok, msg = _validate_booking_time(final["booking_time"])
    if not ok:
        return False, msg
    ok, msg = _validate_strategy(final["strategy"])
    if not ok:
        return False, msg

    return True, "ok"

# ======================
# Patch merge（防 null）
# ======================

def merge_patch(state: Dict[str, Any], patch: Dict[str, Any]) -> None:
    # 不接受 store_name: null
    if patch.get("store_name", "__none__") is None:
        patch.pop("store_name", None)
    # strategy 合併
    if "strategy" in patch and isinstance(patch["strategy"], dict):
        state.setdefault("strategy", {})
        if isinstance(state["strategy"], dict):
            state["strategy"].update(patch["strategy"])
        patch.pop("strategy", None)
    state.update(patch)

# ======================
# 主流程
# ======================

def main():
    state = {"store_id": None}

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        # 更硬的開場指令，避免模型亂生選項
        {"role": "user", "content": "請直接問我：『請問店名是什麼？』不要提供範例選項。"}
    ]

    print("✅ Onboarding Agent v4.1.1 啟動\n")

    while True:
        assistant = call_ollama(messages)
        print("\n🤖 Agent：")
        print(assistant)

        patch = extract_json_after_prefix(assistant, "STATE_PATCH:")
        if isinstance(patch, dict):
            merge_patch(state, patch)

        final = extract_json_after_prefix(assistant, "FINAL_JSON:")
        if final:
            ok, reason = validate_final_json(final)
            if ok:
                print("\n✅ FINAL_JSON 驗證通過（可直接送後端）\n")
                print(json.dumps(final, ensure_ascii=False, indent=2))
                break
            else:
                messages.append({"role": "assistant", "content": assistant})
                messages.append({"role": "user", "content": f"FINAL_JSON 不合格：{reason}。請修正後重新輸出 FINAL_JSON（只輸出 FINAL_JSON）。"})
                continue

        user = input("\n你：").strip()
        if user.lower() in ("exit", "quit"):
            break
        if user == "":
            print("（提示：請輸入回答；如果你不確定，可以說『不知道』或『用預設』）")
            continue

        messages.append({"role": "assistant", "content": assistant})
        messages.append({"role": "user", "content": user})

if __name__ == "__main__":
    main()
