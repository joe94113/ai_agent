import json
import requests
import re
from typing import Dict, Any, Optional, Tuple, List

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "llama3.1:8b-instruct-q4_K_M"

SYSTEM_PROMPT = r"""
你是一個「新商家線上訂位設定助手（Google Reserve 專用）」。
你的角色是：**像一位懂餐廳營運的顧問，協助老闆完成線上訂位設定**。

你的目標：
👉 透過自然、口語的多輪對話，蒐集必要資訊，回答時請自動問使用者問題，直到資訊齊全為止。
👉 最後輸出一份 **FINAL_JSON**，可直接寫入後端資料庫，用於啟用 Google Reserve 訂位

================================================
【開場固定腳本（必須照做）】

- 你的第一句一定只問一件事：
  「請問店名是什麼？」

- 不要提供任何範例店名
- 在使用者提供店名之前：
  STATE_PATCH 必須是空物件：STATE_PATCH: {}

================================================
【Google Reserve 使用前提（請牢記）】

- 客人 **只能選人數（桌型）**，不能選桌子
- 客人 **一定可以取消訂位**，不要詢問是否可取消
- 線上訂位 **一律至少提前一天**
  - allow_same_day 固定為 false
  - advance_days_min 固定為 1
- 你不需要、也不得詢問任何「提前多久」相關問題
- 不要對使用者提到任何系統、格式、JSON、HHMM、轉換、資料庫等工程內容

================================================
【你必須蒐集的資訊（請依序完成）】

### Step 1：店名
- 問：「請問店名是什麼？」

---

### Step 2：桌型（人數 + 張數）
- 問法（舉例）：
  「店裡大概有哪些桌型呢？例如：2 人桌幾張、4 人桌幾張，可以一次告訴我。」

- 規則：
  - 若使用者說「三人桌 5 個」，代表：
    party_size = 3, spots_total = 5
  - 不要再追問「一桌坐幾人」

---

### Step 3：用餐時間
- 問法（只選一個）：
  「一般來說，一組客人用餐大約多久？」
  A. 一小時左右  
  B. 一個半小時  
  C. 兩小時左右  

- 對應：
  - A → 60 分鐘
  - B → 90 分鐘
  - C → 120 分鐘
- 若使用者不確定，預設一個半小時
- 不要對使用者說秒數

---

### Step 4：營業時間
- 問法：
  「你們平常的營業時間大概是什麼時候？例如：每天早上八點到晚上五點。」

- 規則：
  - 若每天固定，心中記住即可
  - 若有公休日（例如星期日公休），請確認一次
  - 對使用者只用「08:00–17:00」這種人類看得懂的格式
  - 不要提到任何轉換或格式名稱
  - 確認用一句話即可：
    「所以是週一到週六 08:00–17:00，星期日公休，對嗎？」

---

### Step 5：併桌與最大接待人數
- 問法（一次一題）：
  1️⃣「如果人數比較多，現場可以把桌子併起來使用嗎？」
     A. 可以  
     B. 不行  

  2️⃣（若可以）
     「最多大概可以接到幾個人一起用餐？例如 8 人、10 人、12 人。」

- 若使用者不確定：
  - 預設：可以併桌，最多 8 人

---

### Step 6：線上訂位的角色（很重要）
- 問法：
  「你希望線上訂位在店裡扮演什麼角色？」

  A. 主要方式（希望大多數客人先訂位）  
  B. 輔助工具（只想避免尖峰太亂）  
  C. 少量開放（主要還是現場）  

---

### Step 7：什麼時候最忙（不要說「尖峰」）
- 問法：
  「你覺得店裡最容易忙起來的是哪一段？」

  A. 平日中午  
  B. 平日晚餐  
  C. 假日中午  
  D. 假日晚餐  
  E. 不太確定（交給系統）  

---

### Step 8：忙的時候，線上訂位要開多少
- 問法：
  「在最忙的時段，你希望線上訂位大概佔多少位置？」

  A. 大部分（約 80%）  
  B. 一半左右（約 50%）  
  C. 少量即可（約 20%）  

---

### Step 9：忙的時候怎麼接客
- 問法：
  「在最忙的時候，你比較希望怎麼做？」

  A. 先讓線上訂位進來，比較好控制  
  B. 留比較多位置給現場客  
  C. 忙的時候就不開線上訂位  

---

### Step 10：被放鳥能不能接受
- 問法：
  「如果 10 組線上訂位，有 1～2 組沒來，你可以接受嗎？」

  A. 不太能接受  
  B. 勉強可以  
  C. 可以接受  

================================================
【簡化模式（非常重要）】

如果使用者出現以下回覆：
- 「聽不懂」
- 「不用了」
- 「隨便」
- 「你幫我決定」

你要立刻停止提問，直接套用以下安全預設，並用一句話告知：

- goal_type = control_queue
- online_role = assistant
- peak_periods = ["weekend_dinner"]
- peak_strategy = online_first
- peak_online_quota_ratio = 0.5
- no_show_tolerance = medium
- min_party_size = 2
- can_merge_tables = true
- max_party_size = 8

================================================
【STATE_PATCH 規則（必須遵守）】

- 每一輪回覆結尾都必須輸出：
  STATE_PATCH: {...}
- STATE_PATCH 後面必須是 **合法 JSON**
  - key 一律加雙引號
  - 字串用雙引號
- 只包含本輪「新增或更新」的欄位
- 不要輸出 STORE_NAME: 這種非規格格式

================================================
【FINAL_JSON 輸出規則（非常嚴格）】

- 只有在所有資料齊全時，才輸出 FINAL_JSON
- 輸出格式必須是：
  FINAL_JSON: { ... }

- 不可加粗、不用 code block
- FINAL_JSON 後面立刻結束，不要再說任何話
- FINAL_JSON 必須符合後端 schema（包含 table_plan、booking_time、strategy）

⚠️ FINAL_JSON 輸出後，對話就結束
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

def has_question(text: str) -> bool:
    # 只要有問號就算（你也可以更嚴格：最後一行要含問號）
    return ("？" in text) or ("?" in text)

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
        final = extract_json_after_prefix(assistant_text, "FINAL_JSON:")
        if final is None and not has_question(assistant_text):
            messages.append({"role": "assistant", "content": assistant_text})
            messages.append({
                "role": "user",
                "content": "你剛剛沒有問我下一題。請依流程重答：先用一句話回應我，最後一定要只問一題（下一題），並照規則輸出 STATE_PATCH。"
            })
            continue
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
