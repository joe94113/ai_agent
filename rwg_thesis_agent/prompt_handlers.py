from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

ALLOWED_PERIODS = {"weekday_lunch", "weekday_dinner", "weekend_brunch", "weekend_dinner"}


FULLWIDTH_TRANS = str.maketrans({
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
    "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
    "，": ",", "：": ":", "；": ";", "（": "(", "）": ")",
})

CH_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


def normalize_user_text(text: str) -> str:
    return text.translate(FULLWIDTH_TRANS).replace("个", "個").strip()


def chinese_number_to_int(token: str) -> int | None:
    token = token.strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    token = token.replace("兩", "二")
    if token in CH_DIGITS:
        return CH_DIGITS[token]
    if token == "十":
        return 10
    if "十" in token:
        left, right = token.split("十", 1)
        tens = 1 if left == "" else CH_DIGITS.get(left)
        ones = 0 if right == "" else CH_DIGITS.get(right)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    return None


def parse_any_number(token: str) -> int | None:
    token = normalize_user_text(token)
    m = re.search(r"\d+", token)
    if m:
        return int(m.group(0))
    m = re.search(r"[零〇一二兩三四五六七八九十]+", token)
    if m:
        return chinese_number_to_int(m.group(0))
    return None


def extract_first_number(text: str) -> int | None:
    return parse_any_number(text)


def ask_text(slot_name: str, state: Dict[str, Any]) -> str:
    business_hours = state["merchant_context"]["business_hours_json"]
    examples = {
        "table_inventory": "店裡有哪些桌型呢？例如：2 人桌 4 張、4 人桌 3 張、6 人桌 1 張。",
        "service_duration_sec": "大部分客人通常會坐多久？可直接輸入 60、90、120 分鐘。",
        "booking_hours_mode": (
            "線上訂位時間要怎麼設定？\n"
            "A. 直接沿用營業時間，系統自動幫您扣掉用餐時間\n"
            "B. 我要自己另外設定"
        ),
        "online_booking_hours_json": (
            "如果您真的要自訂整體線上訂位時段，請直接貼 JSON。\n"
            "如果您只是想『某些忙時不要開線上』，不用在這裡改，後面我會再問您忙時規則。"
        ),
        "can_merge_tables": "如果客人比較多，可以幫他們併桌嗎？請輸入：可以 / 不行。",
        "max_party_size": "最多大概可以接待幾位客人一起用餐？請直接輸入數字，例如 8、10、12。",
        "service_scheduling_rules": (
            "想確認兩個時間規則：\n"
            "1. 客人最晚要在多久前才能線上訂位？\n"
            "2. 客人最晚要在多久前可以自己在線上取消？\n"
            "如果兩個規則一樣，您可以直接回答一個選項：A 不限制 / B 30 分鐘前 / C 2 小時前 / D 前一天。\n"
            "如果兩個規則不同，您可以直接說：『訂位 2 小時前、取消前一天』或『訂位 C、取消 D』。"
        ),
        "default_policy": (
            "一般時段，您希望線上訂位大概占多少位置？\n"
            "A. 大部分都可以給線上\n"
            "B. 一半左右\n"
            "C. 少量就好，現場還是為主\n"
            "D. 平常就不開放線上"
        ),
        "time_block_overrides": (
            "有沒有特別忙的時段，要跟平常用不同規則？\n"
            "例如：『假日晚餐不開線上，只接現場』或『平日中午多開一點線上』。\n"
            "如果沒有，輸入：沒有"
        ),
        "no_show_tolerance": (
            "如果線上客人訂了卻沒來，您比較能接受嗎？\n"
            "A. 不太能接受\n"
            "B. 還可以\n"
            "C. 可以接受"
        ),
        "popularity": (
            "忙的時候，現場通常排隊情況如何？\n"
            "A. 幾乎不用等\n"
            "B. 稍微等一下\n"
            "C. 常常排很久"
        ),
        "seating_sections": "店裡有特別分座位區嗎？例如吧台、戶外區、包廂。沒有就輸入：沒有。",
        "merchant_terms": "是否要額外顯示訂位條款？沒有就輸入：沒有；有的話可輸入『text:條款內容 | url:https://...』。",
    }
    if slot_name == "online_booking_hours_json":
        return examples[slot_name] + f"\n目前預載營業時間共有 {len(business_hours)} 段。"
    return examples[slot_name]


RETRY_HINTS = {
    "table_inventory": "可以像這樣回答：2 人桌 4 張、4 人桌 3 張。",
    "service_duration_sec": "請直接輸入 60、90 或 120。",
    "booking_hours_mode": "請回答 A / B，或輸入『沿用』/『另外設定』。",
    "online_booking_hours_json": "這題目前需要貼 JSON；如果只是想忙時不開線上，可以回上一題選沿用，後面再設忙時規則。",
    "can_merge_tables": "請回答：可以 / 不行。",
    "max_party_size": "請直接輸入數字，例如 8、10、12。",
    "service_scheduling_rules": "如果兩個都一樣，可直接輸入 A / B / C / D；若不同，可回答『訂位 2 小時前、取消前一天』。",
    "default_policy": "請回答 A / B / C / D，或直接說『大部分給線上 / 一半左右 / 少量就好 / 不開線上』。",
    "time_block_overrides": "可以像這樣回答：『假日晚餐不開線上，只接現場』；沒有就輸入『沒有』。",
    "no_show_tolerance": "請回答 A / B / C，或直接說『不太能接受 / 還可以 / 可以接受』。",
    "popularity": "請回答 A / B / C，或直接說『不用等 / 稍微等 / 常常排很久』。",
    "seating_sections": "例如：吧台、戶外區、包廂；沒有就輸入『沒有』。",
    "merchant_terms": "沒有就輸入『沒有』；有的話可輸入 text:... | url:...。",
}


def retry_hint(slot_name: str) -> str:
    return RETRY_HINTS.get(slot_name, "請換個更簡單的說法再試一次。")


ERROR_LABELS = {
    "table_inventory": "桌型資訊",
    "service_duration_sec": "用餐時間",
    "booking_hours_mode": "線上訂位時間設定方式",
    "online_booking_hours_json": "自訂線上訂位時段",
    "can_merge_tables": "是否可併桌",
    "max_party_size": "最大接待人數",
    "service_scheduling_rules": "最晚訂位 / 取消規則",
    "default_policy": "一般時段的線上策略",
    "time_block_overrides": "忙時特殊規則",
    "no_show_tolerance": "對 no-show 的容忍度",
    "popularity": "店家熱門程度",
    "seating_sections": "座位區設定",
    "merchant_terms": "商家條款",
}


def parse_slot(slot_name: str, text: str, state: Dict[str, Any]) -> Tuple[Any, float, str]:
    parsers = {
        "table_inventory": parse_table_inventory,
        "service_duration_sec": parse_duration,
        "booking_hours_mode": parse_booking_hours_mode,
        "online_booking_hours_json": parse_hours_json,
        "can_merge_tables": parse_yes_no,
        "max_party_size": parse_positive_int,
        "service_scheduling_rules": parse_scheduling_rules,
        "default_policy": parse_default_policy,
        "time_block_overrides": parse_time_block_overrides,
        "no_show_tolerance": parse_no_show_tolerance,
        "popularity": parse_popularity,
        "seating_sections": parse_seating_sections,
        "merchant_terms": parse_merchant_terms,
    }
    parser = parsers[slot_name]
    value = parser(text)
    if value is None:
        return None, 0.0, f"我沒有看懂{ERROR_LABELS.get(slot_name, slot_name)}"
    return value, 1.0, "ok"



def parse_table_inventory(text: str) -> List[Dict[str, int]] | None:
    text = normalize_user_text(text)
    if text.startswith("["):
        try:
            obj = json.loads(text)
            if isinstance(obj, list):
                out = []
                for x in obj:
                    ps = parse_any_number(str(x.get("party_size", "")))
                    cnt = parse_any_number(str(x.get("spots_total", "")))
                    if ps is None or cnt is None:
                        return None
                    out.append({"party_size": ps, "spots_total": cnt})
                return out or None
        except Exception:
            return None

    compact = re.sub(r"\s+", "", text)
    pattern = re.compile(
        r"([0-9零〇一二兩三四五六七八九十]+)\s*人(?:桌|位|座)?\s*([0-9零〇一二兩三四五六七八九十]+)\s*(?:張|個|桌|組)?"
    )
    matches = pattern.findall(compact)
    if not matches:
        return None

    out: List[Dict[str, int]] = []
    for ps_raw, cnt_raw in matches:
        ps = parse_any_number(ps_raw)
        cnt = parse_any_number(cnt_raw)
        if ps is None or cnt is None or ps <= 0 or cnt < 0:
            return None
        out.append({"party_size": ps, "spots_total": cnt})

    merged: Dict[int, int] = {}
    for item in out:
        merged[item["party_size"]] = merged.get(item["party_size"], 0) + item["spots_total"]
    return [{"party_size": ps, "spots_total": merged[ps]} for ps in sorted(merged)]



def parse_duration(text: str) -> int | None:
    t = normalize_user_text(text)
    if "一個半小時" in t or "1個半小時" in t or "1.5小時" in t or "九十分鐘" in t:
        return 90 * 60
    if "兩小時" in t or "二小時" in t or "120分鐘" in t or "二個小時" in t:
        return 120 * 60
    if "一小時" in t or "60分鐘" in t or "一個小時" in t:
        return 60 * 60
    minutes = extract_first_number(t)
    if minutes is None:
        return None
    if "小時" in t and minutes in (1, 2):
        minutes *= 60
    if minutes not in (60, 90, 120):
        return None
    return minutes * 60



def parse_booking_hours_mode(text: str) -> str | None:
    t = text.strip().lower()
    if t in {"a", "same", "auto", "沿用", "照營業時間", "same_as_business_hours_minus_duration"}:
        return "same_as_business_hours_minus_duration"
    if t in {"b", "custom", "manual", "自訂", "另外設定", "自己設定"}:
        return "custom"
    return None



def parse_hours_json(text: str) -> List[Dict[str, Any]] | None:
    text = text.strip()
    if text.startswith("{"):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict) and "online_booking_hours_json" in obj:
                obj = obj["online_booking_hours_json"]
            if isinstance(obj, list):
                return obj
        except Exception:
            return None
    if text.startswith("["):
        try:
            obj = json.loads(text)
            if isinstance(obj, list):
                return obj
        except Exception:
            return None
    return None



def parse_yes_no(text: str) -> bool | None:
    t = text.strip().lower()
    if t in {"yes", "y", "true", "可以", "可", "能", "1"}:
        return True
    if t in {"no", "n", "false", "不行", "不能", "否", "0"}:
        return False
    return None



def parse_positive_int(text: str) -> int | None:
    n = extract_first_number(text)
    return n if n is not None and n > 0 else None


TIME_CHOICE_TO_SEC = {"a": 0, "b": 1800, "c": 7200, "d": 86400}



def parse_relative_time_to_seconds(text: str) -> int | None:
    t = normalize_user_text(text).lower()
    if not t:
        return None
    if t in TIME_CHOICE_TO_SEC:
        return TIME_CHOICE_TO_SEC[t]

    if any(x in t for x in ["不限制", "不限", "隨時", "都可以", "立即"]):
        return 0
    if any(x in t for x in ["前一天", "一天前"]):
        return 86400
    if "半小時" in t:
        return 1800
    if any(x in t for x in ["30分鐘", "三十分鐘"]):
        return 1800
    if any(x in t for x in ["2小時", "兩小時", "二小時"]):
        return 7200
    if any(x in t for x in ["1小時", "一小時"]):
        return 3600

    m = re.search(r"(\d+)\s*h\b", t)
    if m:
        return int(m.group(1)) * 3600

    m = re.search(r"(\d+)\s*(秒|sec|secs|second|seconds)", t)
    if m:
        return int(m.group(1))

    m = re.search(r"([0-9零〇一二兩三四五六七八九十]+)\s*(分|分鐘|min|mins|minute|minutes)", t)
    if m:
        n = parse_any_number(m.group(1))
        return None if n is None else n * 60
    m = re.search(r"([0-9零〇一二兩三四五六七八九十]+)\s*(小時|hr|hrs|hour|hours)", t)
    if m:
        n = parse_any_number(m.group(1))
        return None if n is None else n * 3600
    m = re.search(r"([0-9零〇一二兩三四五六七八九十]+)\s*(天|day|days)", t)
    if m:
        n = parse_any_number(m.group(1))
        return None if n is None else n * 86400
    return None



def parse_scheduling_rules(text: str) -> Dict[str, int] | None:
    text = text.strip()
    if text.startswith("{"):
        try:
            obj = json.loads(text)
            return {
                "min_advance_booking_sec": int(obj["min_advance_booking_sec"]),
                "min_advance_online_canceling_sec": int(obj["min_advance_online_canceling_sec"]),
            }
        except Exception:
            return None

    booking = re.search(r"booking\s*=\s*(\d+)", text, re.I)
    cancel = re.search(r"cancel\s*=\s*(\d+)", text, re.I)
    if booking and cancel:
        return {
            "min_advance_booking_sec": int(booking.group(1)),
            "min_advance_online_canceling_sec": int(cancel.group(1)),
        }

    t = text.lower().replace("：", ":")
    booking_match = re.search(r"(?:訂位|預訂|booking)\s*[:=]?\s*(.+?)(?=(?:取消|cancel|$))", t)
    cancel_match = re.search(r"(?:取消|cancel)\s*[:=]?\s*(.+)$", t)
    booking_sec = parse_relative_time_to_seconds(booking_match.group(1).strip(" ，,;；、")) if booking_match else None
    cancel_sec = parse_relative_time_to_seconds(cancel_match.group(1).strip(" ，,;；、")) if cancel_match else None
    if booking_sec is not None and cancel_sec is not None:
        return {
            "min_advance_booking_sec": booking_sec,
            "min_advance_online_canceling_sec": cancel_sec,
        }

    # 支援像「A」這種代表兩個規則都相同的單一選項
    single_choice = re.fullmatch(r"\s*([A-Da-d])\s*", text)
    if single_choice:
        sec = TIME_CHOICE_TO_SEC[single_choice.group(1).lower()]
        return {
            "min_advance_booking_sec": sec,
            "min_advance_online_canceling_sec": sec,
        }

    # 支援像「B D」這種簡單選項
    choices = re.findall(r"\b([A-Da-d])\b", text)
    if len(choices) >= 2:
        return {
            "min_advance_booking_sec": TIME_CHOICE_TO_SEC[choices[0].lower()],
            "min_advance_online_canceling_sec": TIME_CHOICE_TO_SEC[choices[1].lower()],
        }

    # 支援像「30分鐘前，前一天」這種兩段式描述
    parts = [p.strip() for p in re.split(r"[，,;；/]", text) if p.strip()]
    if len(parts) >= 2:
        first = parse_relative_time_to_seconds(parts[0])
        second = parse_relative_time_to_seconds(parts[1])
        if first is not None and second is not None:
            return {
                "min_advance_booking_sec": first,
                "min_advance_online_canceling_sec": second,
            }
    return None



def parse_default_policy(text: str) -> Dict[str, Any] | None:
    t = text.strip().lower()
    if t in {"d", "不開線上", "不要開線上", "平常不開線上", "只接現場", "walkin_only", "no_online"}:
        return {"online_enabled": False, "online_quota_ratio": 0.0, "channel_priority": "walkin_only"}
    if t.startswith("{"):
        try:
            obj = json.loads(t)
            return {
                "online_enabled": bool(obj.get("online_enabled", True)),
                "online_quota_ratio": float(obj.get("online_quota_ratio", 0.5)),
                "channel_priority": str(obj.get("channel_priority", "balanced")),
            }
        except Exception:
            return None
    if t in {"a", "大部分", "大部分給線上", "大部分都給線上", "線上優先", "online_first"}:
        return {"online_enabled": True, "online_quota_ratio": 0.8, "channel_priority": "online_first"}
    if t in {"b", "一半", "一半左右", "差不多一半", "大概一半", "平衡", "balanced"}:
        return {"online_enabled": True, "online_quota_ratio": 0.5, "channel_priority": "balanced"}
    if t in {"c", "少量", "少量就好", "少開一點線上", "現場為主", "留比較多給現場", "walkin_first"}:
        return {"online_enabled": True, "online_quota_ratio": 0.2, "channel_priority": "walkin_first"}

    m = re.match(r"(online_first|walkin_first|balanced)\s*(0(?:\.\d+)?|1(?:\.0+)?)?", t)
    if m:
        priority = m.group(1)
        ratio = float(m.group(2) or (0.5 if priority != "walkin_first" else 0.2))
        return {"online_enabled": True, "online_quota_ratio": ratio, "channel_priority": priority}
    return None


PERIOD_KEYWORDS = {
    "weekday_lunch": ["平日中午", "平日午餐", "週間中午", "週間午餐"],
    "weekday_dinner": ["平日晚餐", "平日晚上", "週間晚餐", "週間晚上"],
    "weekend_brunch": ["假日中午", "假日午餐", "週末中午", "週末午餐"],
    "weekend_dinner": ["假日晚餐", "假日晚上", "週末晚餐", "週末晚上"],
}


def detect_periods(text: str) -> List[str]:
    t = text.lower()
    out: List[str] = []
    for period, keywords in PERIOD_KEYWORDS.items():
        if any(k in t for k in keywords) or period in t:
            out.append(period)
    return out



def parse_time_block_overrides(text: str) -> List[Dict[str, Any]] | None:
    t = text.strip().lower()
    if t in {"none", "skip", "無", "沒有", "不用", "沒有特別規則"}:
        return []
    if t.startswith("["):
        try:
            obj = json.loads(t)
            return obj if isinstance(obj, list) else None
        except Exception:
            return None

    # 相容舊版技術語法
    if "=" in text:
        result: List[Dict[str, Any]] = []
        parts = [p.strip() for p in text.split(";") if p.strip()]
        for part in parts:
            if "=" not in part:
                return None
            period_raw, rule_raw = [x.strip().lower() for x in part.split("=", 1)]
            periods = [p.strip() for p in period_raw.split(",") if p.strip()]
            if not periods or any(p not in ALLOWED_PERIODS for p in periods):
                return None
            if rule_raw == "no_online":
                result.append({"periods": periods, "online_enabled": False, "channel_priority": "walkin_only"})
                continue
            m = re.match(r"(online_first|walkin_first|balanced)\s*,\s*(0(?:\.\d+)?|1(?:\.0+)?)", rule_raw)
            if not m:
                return None
            result.append({
                "periods": periods,
                "online_enabled": True,
                "channel_priority": m.group(1),
                "online_quota_ratio": float(m.group(2)),
            })
        return result

    # 商家口語化語法
    result: List[Dict[str, Any]] = []
    parts = [p.strip() for p in re.split(r"[;；\n]", text) if p.strip()]
    for part in parts:
        periods = detect_periods(part)
        if not periods:
            return None
        if any(x in part for x in ["不開線上", "不要開線上", "不接線上", "只接現場", "都接現場"]):
            result.append({"periods": periods, "online_enabled": False, "channel_priority": "walkin_only"})
            continue
        if any(x in part for x in ["多留給現場", "留比較多給現場", "現場優先", "少量線上"]):
            result.append({
                "periods": periods,
                "online_enabled": True,
                "channel_priority": "walkin_first",
                "online_quota_ratio": 0.2,
            })
            continue
        if any(x in part for x in ["多開線上", "線上優先", "大部分給線上"]):
            result.append({
                "periods": periods,
                "online_enabled": True,
                "channel_priority": "online_first",
                "online_quota_ratio": 0.8,
            })
            continue
        if any(x in part for x in ["照常", "跟平常一樣", "維持原本"]):
            result.append({
                "periods": periods,
                "online_enabled": True,
                "channel_priority": "balanced",
                "online_quota_ratio": 0.5,
            })
            continue
        return None
    return result



def parse_no_show_tolerance(text: str) -> str | None:
    t = text.strip().lower()
    if t in {"a", "low", "低", "不太能接受", "不能接受", "很在意"}:
        return "low"
    if t in {"b", "medium", "中", "還可以", "勉強可以", "普通"}:
        return "medium"
    if t in {"c", "high", "高", "可以接受", "能接受", "沒關係"}:
        return "high"
    return None



def parse_popularity(text: str) -> str | None:
    t = text.strip().lower()
    if t in {"a", "low", "低", "幾乎不用等", "不用等", "很少排隊"}:
        return "low"
    if t in {"b", "medium", "中", "稍微等一下", "稍微排", "普通"}:
        return "medium"
    if t in {"c", "high", "高", "常常排很久", "排很久", "很多人排隊"}:
        return "high"
    return None



def parse_seating_sections(text: str) -> List[Dict[str, str]] | None:
    t = text.strip()
    if not t:
        return None
    if t.lower() in {"none", "skip", "無", "沒有"}:
        return []
    items = [x.strip() for x in re.split(r"[，,]", t) if x.strip()]
    if not items:
        return None
    return [{"room_id": slugify(x), "room_name": x} for x in items]



def parse_merchant_terms(text: str) -> Dict[str, Any] | None:
    t = text.strip()
    if not t:
        return None
    if t.lower() in {"none", "skip", "無", "沒有"}:
        return {"enabled": False, "text": None, "url": None, "source": "agent_input"}
    if t.startswith("{"):
        try:
            obj = json.loads(t)
            return {
                "enabled": bool(obj.get("enabled", True)),
                "text": obj.get("text"),
                "url": obj.get("url"),
                "source": "agent_input",
            }
        except Exception:
            return None

    text_part = None
    url_part = None
    for piece in [p.strip() for p in t.split("|")]:
        if piece.lower().startswith("text:"):
            text_part = piece[5:].strip() or None
        elif piece.lower().startswith("url:"):
            url_part = piece[4:].strip() or None
    if text_part is None and url_part is None:
        return None
    return {"enabled": True, "text": text_part, "url": url_part, "source": "agent_input"}



def slugify(text: str) -> str:
    s = re.sub(r"\s+", "_", text.strip().lower())
    s = re.sub(r"[^a-z0-9_\-]", "", s)
    return s or "section"
