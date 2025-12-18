import json
import requests
import re
from typing import Dict, Any, Optional, Tuple, List

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "llama3.1:8b-instruct-q4_K_M"

SYSTEM_PROMPT = r"""
你是一個「新商家線上訂位設定助手」AI Agent。
目標：用多輪對話蒐集資料，最後產出完全符合 schema 的 FINAL_JSON，讓系統可以直接寫入資料庫。

========================
【必須蒐集的資料（缺一不可）】
1) store_name（店名）
2) resources（桌型清單，可任意人數桌）：
   - 每筆包含 party_size（整數，桌子標準可坐幾人）、spots_total（整數，這種桌子幾張）
3) duration_sec（整數秒數，例如 90 分鐘=5400）
4) business_hours_json（GMB 格式，array/list；可多段）：
   - 每筆必須是：
     {"open":{"day":0-6,"time":"HHMM"},"close":{"day":0-6,"time":"HHMM"}}
   - day 定義：0=Sun,1=Mon,2=Tue,3=Wed,4=Thu,5=Fri,6=Sat
   - time 必須是 4 位數字字串 "HHMM"（例："0800","1730"），禁止 "080000"、禁止 "08:00"
5) strategy（策略）：
   - goal_type: fill_seats | control_queue | keep_walkin
   - online_role: primary | assistant | minimal
   - peak_periods: ["weekday_lunch"|"weekday_dinner"|"weekend_brunch"|"weekend_dinner"]（可多選）
   - peak_strategy: online_first | walkin_first | no_online
   - no_show_tolerance: low | medium | high
   - can_merge_tables: true/false
   - max_party_size: 整數（例如 8/10/12）

========================
【你要怎麼問】
- 一次問 1~2 個問題，避免太多
- 若使用者回覆不完整，你要追問缺的
- 若使用者用口語時間（例：早八晚五、下午五到晚上十點），你要自己轉成 HHMM，並在下一句用簡短方式確認：
  例如：「所以每天是 08:00–17:00 對嗎？」

========================
【輸出格式（非常重要）】
- 每次回覆最後都要輸出一行：
  STATE_PATCH: <JSON>
  只包含本輪新增/修正的欄位（partial update）
  例：
  STATE_PATCH: {"store_name":"好口福火鍋"}
  STATE_PATCH: {"resources":[{"party_size":4,"spots_total":10},{"party_size":2,"spots_total":4}]}
  STATE_PATCH: {"duration_sec":5400}

- 當你「確定資料齊全且格式正確」時，才輸出：
  FINAL_JSON: <JSON>
  且 <JSON> 必須是合法 JSON，完全符合 schema，不能缺欄位，不能用運算式（90*60 不行）

========================
【強制規則（務必遵守）】
- business_hours_json 必須是 array/list，不可輸出 dict（例如 {"day0":...} 這種不行）
- time 一律 4 位 "HHMM"
- duration_sec 必須是整數（秒）
- max_party_size 必須是整數
- peak_periods 對照：
  - 「週末晚上/假日晚餐」→ weekend_dinner
  - 「平日晚餐」→ weekday_dinner
  - 「平日午餐」→ weekday_lunch
  - 「假日早午餐/週末早午餐」→ weekend_brunch
- 如果使用者只說「週末晚上」，peak_periods 只能放 ["weekend_dinner"]，不要額外加其他。
"""

def call_ollama(messages: List[Dict[str, str]]) -> str:
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "stream": False
    }
    resp = requests.post(OLLAMA_URL, json=payload)
    resp.raise_for_status()
    return resp.json()["message"]["content"]

def extract_json_after_prefix(text: str, prefix: str) -> Optional[Any]:
    """
    抓取 prefix 之後的 JSON（允許 object 或 array）
    例如 STATE_PATCH: {...}
    """
    # 找最後一個 prefix（避免內文也出現）
    idx = text.rfind(prefix)
    if idx == -1:
        return None
    raw = text[idx + len(prefix):].strip()
    # raw 可能以 ``` 包住，先清掉
    raw = raw.strip("`").strip()
    # 若不是以 { 或 [ 開頭，判定失敗
    if not raw or raw[0] not in "{[":
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None

HHMM_RE = re.compile(r"^\d{4}$")

def validate_business_hours_json(bh: Any) -> Tuple[bool, str]:
    if not isinstance(bh, list) or len(bh) == 0:
        return False, "business_hours_json 必須是非空 list"
    for i, p in enumerate(bh):
        if not isinstance(p, dict):
            return False, f"business_hours_json[{i}] 必須是 object"
        if "open" not in p or "close" not in p:
            return False, f"business_hours_json[{i}] 必須包含 open/close"
        o = p["open"]; c = p["close"]
        if not isinstance(o, dict) or not isinstance(c, dict):
            return False, f"business_hours_json[{i}].open/close 必須是 object"
        if "day" not in o or "time" not in o or "day" not in c or "time" not in c:
            return False, f"business_hours_json[{i}] open/close 必須包含 day/time"
        if not (isinstance(o["day"], int) and 0 <= o["day"] <= 6):
            return False, f"business_hours_json[{i}].open.day 必須 0~6"
        if not (isinstance(c["day"], int) and 0 <= c["day"] <= 6):
            return False, f"business_hours_json[{i}].close.day 必須 0~6"
        ot = str(o["time"]); ct = str(c["time"])
        if not HHMM_RE.match(ot):
            return False, f"business_hours_json[{i}].open.time 必須是 4 位 HHMM"
        if not HHMM_RE.match(ct):
            return False, f"business_hours_json[{i}].close.time 必須是 4 位 HHMM"
    return True, "ok"

def validate_resources(res: Any) -> Tuple[bool, str]:
    if not isinstance(res, list) or len(res) == 0:
        return False, "resources 必須是非空 list"
    for i, r in enumerate(res):
        if not isinstance(r, dict):
            return False, f"resources[{i}] 必須是 object"
        if "party_size" not in r or "spots_total" not in r:
            return False, f"resources[{i}] 必須包含 party_size/spots_total"
        if not isinstance(r["party_size"], int) or r["party_size"] <= 0:
            return False, f"resources[{i}].party_size 必須是正整數"
        if not isinstance(r["spots_total"], int) or r["spots_total"] < 0:
            return False, f"resources[{i}].spots_total 必須是整數且 >=0"
    return True, "ok"

def validate_strategy(s: Any) -> Tuple[bool, str]:
    if not isinstance(s, dict):
        return False, "strategy 必須是 object"
    need = ["goal_type","online_role","peak_periods","peak_strategy","no_show_tolerance","can_merge_tables","max_party_size"]
    for k in need:
        if k not in s:
            return False, f"strategy 缺少 {k}"

    if s["goal_type"] not in ["fill_seats","control_queue","keep_walkin"]:
        return False, "strategy.goal_type 不合法"
    if s["online_role"] not in ["primary","assistant","minimal"]:
        return False, "strategy.online_role 不合法"
    if s["peak_strategy"] not in ["online_first","walkin_first","no_online"]:
        return False, "strategy.peak_strategy 不合法"
    if s["no_show_tolerance"] not in ["low","medium","high"]:
        return False, "strategy.no_show_tolerance 不合法"
    if not isinstance(s["can_merge_tables"], bool):
        return False, "strategy.can_merge_tables 必須是 boolean"
    if not isinstance(s["max_party_size"], int) or s["max_party_size"] <= 0:
        return False, "strategy.max_party_size 必須是正整數"

    if not isinstance(s["peak_periods"], list):
        return False, "strategy.peak_periods 必須是 list"
    allowed = {"weekday_lunch","weekday_dinner","weekend_brunch","weekend_dinner"}
    for x in s["peak_periods"]:
        if x not in allowed:
            return False, f"strategy.peak_periods 出現不允許的值：{x}"

    return True, "ok"

def validate_final_json(final: Any) -> Tuple[bool, str]:
    if not isinstance(final, dict):
        return False, "FINAL_JSON 必須是 object"
    # top fields
    for k in ["store_id","store_name","capacity_hint","resources","duration_sec","business_hours_json","strategy"]:
        if k not in final:
            return False, f"缺少欄位 {k}"

    if not isinstance(final["store_name"], str) or not final["store_name"].strip():
        return False, "store_name 必須是非空字串"

    if final["store_id"] is not None and not isinstance(final["store_id"], int):
        return False, "store_id 必須是 null 或 int"

    if not isinstance(final["capacity_hint"], int) or final["capacity_hint"] <= 0:
        return False, "capacity_hint 必須是正整數"

    ok, msg = validate_resources(final["resources"])
    if not ok:
        return False, msg

    if not isinstance(final["duration_sec"], int) or final["duration_sec"] <= 0:
        return False, "duration_sec 必須是正整數（秒）"

    ok, msg = validate_business_hours_json(final["business_hours_json"])
    if not ok:
        return False, msg

    ok, msg = validate_strategy(final["strategy"])
    if not ok:
        return False, msg

    return True, "ok"

def merge_patch(state: Dict[str, Any], patch: Dict[str, Any]) -> None:
    """
    簡易 merge：頂層 key 覆蓋
    strategy 若 patch 只提供部分，也做 dict merge
    """
    for k, v in patch.items():
        if k == "strategy" and isinstance(v, dict):
            state.setdefault("strategy", {})
            if isinstance(state["strategy"], dict):
                state["strategy"].update(v)
            else:
                state["strategy"] = v
        else:
            state[k] = v

def main():
    # 初始 state（store_id 通常沒有，先固定 null）
    state: Dict[str, Any] = {"store_id": None}

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "我們開始吧。請先問我店名。"}
    ]

    print("✅ Onboarding Agent v2 已啟動（輸入 exit 離開）\n")

    while True:
        assistant_text = call_ollama(messages)
        print("\n🤖 Agent：")
        print(assistant_text)

        patch = extract_json_after_prefix(assistant_text, "STATE_PATCH:")
        if isinstance(patch, dict):
            merge_patch(state, patch)

        final = extract_json_after_prefix(assistant_text, "FINAL_JSON:")
        if final is not None:
            ok, reason = validate_final_json(final)
            if ok:
                print("\n✅ FINAL_JSON 驗證通過（可直接送後端）")
                print(json.dumps(final, ensure_ascii=False, indent=2))
                break
            else:
                # 強制模型修正 FINAL_JSON
                messages.append({"role": "assistant", "content": assistant_text})
                messages.append({"role": "user", "content": f"你輸出的 FINAL_JSON 不合格：{reason}。請修正並重新輸出 FINAL_JSON（只輸出 FINAL_JSON，不要其他文字）。"})
                continue

        # 若 state 看起來快完成但不合規（例如 business_hours_json 不是 list 或 time 不是 HHMM），提示模型修正
        # 這裡只做「必要欄位缺失」提示，避免太吵
        missing = []
        for k in ["store_name","resources","duration_sec","business_hours_json","strategy"]:
            if k not in state or state[k] in (None, "", []):
                missing.append(k)
        # strategy 子欄位
        if "strategy" in state and isinstance(state["strategy"], dict):
            for sk in ["goal_type","online_role","peak_periods","peak_strategy","no_show_tolerance","can_merge_tables","max_party_size"]:
                if sk not in state["strategy"]:
                    missing.append(f"strategy.{sk}")

        if missing:
            # 繼續讓使用者回答
            user_in = input("\n你：").strip()
            if user_in.lower() in ("exit", "quit"):
                print("Bye")
                break
            messages.append({"role": "assistant", "content": assistant_text})
            messages.append({"role": "user", "content": user_in})
            continue

        # 若模型沒有輸出 FINAL_JSON，但 state 欄位齊了，要求它輸出 FINAL_JSON
        # 同時要求 business_hours_json 必須是 list + HHMM
        messages.append({"role": "assistant", "content": assistant_text})
        messages.append({"role": "user", "content": "資料應該已齊全。請檢查 business_hours_json 必須為 list 且 time 為 4 位 HHMM，duration_sec/max_party_size 必須是整數，然後輸出 FINAL_JSON。"})
        continue

if __name__ == "__main__":
    main()
