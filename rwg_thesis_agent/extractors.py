from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

import requests

from .prompt_handlers import parse_slot

OLLAMA_URL = os.getenv("RWG_OLLAMA_URL", "http://localhost:11434/api/chat")
MODEL_NAME = os.getenv("RWG_OLLAMA_MODEL", "llama3.1:8b-instruct-q4_K_M")

_EXTRACTOR_MODE = os.getenv("RWG_EXTRACTOR_MODE", "rule")  # rule | ollama | auto

EXTRACTOR_SYSTEM = r"""
你是一個「資料抽取器」，只負責把使用者回答轉成 JSON object。
你必須只輸出一段 JSON object（不要文字、不要解釋）。
不可包含 Markdown code block。
若資訊不足或無法判斷，輸出空物件 {}。
"""

SCHEMA_GUIDES: Dict[str, str] = {
    "table_inventory": '輸出：{"table_inventory":[{"party_size":2,"spots_total":4},{"party_size":4,"spots_total":3}]}',
    "service_duration_sec": '輸出：{"service_duration_sec":3600} 或 5400 或 7200',
    "booking_hours_mode": '輸出：{"booking_hours_mode":"same_as_business_hours_minus_duration"} 或 {"booking_hours_mode":"custom"}',
    "online_booking_hours_json": '輸出：{"online_booking_hours_json":[{"open":{"day":1,"time":"1100"},"close":{"day":1,"time":"1300"}}]}',
    "can_merge_tables": '輸出：{"can_merge_tables":true} 或 false',
    "max_party_size": '輸出：{"max_party_size":10}',
    "service_scheduling_rules": '輸出：{"service_scheduling_rules":{"min_advance_booking_sec":7200,"min_advance_online_canceling_sec":86400}}',
    "default_policy": '輸出：{"default_policy":{"online_enabled":true,"online_quota_ratio":0.5,"channel_priority":"balanced"}}；若不開線上則 online_enabled=false',
    "time_block_overrides": '輸出：{"time_block_overrides":[{"periods":["weekend_dinner"],"online_enabled":false,"channel_priority":"walkin_only"}]}',
    "no_show_tolerance": '輸出：{"no_show_tolerance":"low"} 或 medium 或 high',
    "popularity": '輸出：{"popularity":"low"} 或 medium 或 high',
    "seating_sections": '輸出：{"seating_sections":[]} 或 {"seating_sections":[{"id":"bar","name":"bar"}]}',
    "merchant_terms": '輸出：{"merchant_terms":{"enabled":false,"text":null,"url":null,"source":"agent_input"}} 或 enabled=true 並填 text/url',
}

EXPECTED_KEYS: Dict[str, str] = {
    "table_inventory": "table_inventory",
    "service_duration_sec": "service_duration_sec",
    "booking_hours_mode": "booking_hours_mode",
    "online_booking_hours_json": "online_booking_hours_json",
    "can_merge_tables": "can_merge_tables",
    "max_party_size": "max_party_size",
    "service_scheduling_rules": "service_scheduling_rules",
    "default_policy": "default_policy",
    "time_block_overrides": "time_block_overrides",
    "no_show_tolerance": "no_show_tolerance",
    "popularity": "popularity",
    "seating_sections": "seating_sections",
    "merchant_terms": "merchant_terms",
}


def set_extractor_mode(mode: str) -> None:
    global _EXTRACTOR_MODE
    if mode not in {"rule", "ollama", "auto"}:
        raise ValueError(f"Unsupported extractor mode: {mode}")
    _EXTRACTOR_MODE = mode


def get_extractor_mode() -> str:
    return _EXTRACTOR_MODE


def extract_first_json_object_str(text: str) -> Optional[str]:
    if not text:
        return None
    s = text.strip().strip("`").strip()
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return s[start:i+1]
    return None


def parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    obj_str = extract_first_json_object_str(text)
    if not obj_str:
        return None
    try:
        obj = json.loads(obj_str)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def call_ollama(messages: List[Dict[str, str]]) -> Optional[str]:
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1, "top_p": 0.9},
    }
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=20)
        resp.raise_for_status()
        return resp.json()["message"]["content"]
    except Exception:
        return None


def llm_extract_slot(slot_name: str, user_text: str, state: Dict[str, Any]) -> Tuple[Any, float, str]:
    guide = SCHEMA_GUIDES.get(slot_name, '輸出：{}')
    expected_key = EXPECTED_KEYS.get(slot_name, slot_name)
    prompt = (
        f"【步驟】{slot_name}\n"
        f"【輸出格式】{guide}\n"
        f"【已知狀態摘要】{json.dumps(state, ensure_ascii=False)}\n"
        f"【使用者回答】{user_text}\n"
        f"請只輸出 JSON object。"
    )
    raw = call_ollama([
        {"role": "system", "content": EXTRACTOR_SYSTEM},
        {"role": "user", "content": prompt},
    ])
    if not raw:
        return None, 0.0, "OLLAMA 呼叫失敗"
    obj = parse_json_object(raw)
    if not obj or expected_key not in obj:
        return None, 0.0, "OLLAMA 無法輸出預期欄位"
    return obj[expected_key], 0.85, "ok"


def extract_slot_value(slot_name: str, user_text: str, state: Dict[str, Any]) -> Tuple[Any, float, str]:
    """可切換 rule-based / OLLAMA 的 slot extractor。

    modes:
    - rule: 只用 rule-based parser，適合可重現實驗
    - ollama: 優先用 OLLAMA，失敗則回傳失敗
    - auto: 先用 OLLAMA，失敗時退回 rule-based parser
    """
    mode = get_extractor_mode()
    if mode == "rule":
        return parse_slot(slot_name, user_text, state)

    if mode == "ollama":
        return llm_extract_slot(slot_name, user_text, state)

    value, confidence, message = llm_extract_slot(slot_name, user_text, state)
    if value is not None:
        return value, confidence, message
    return parse_slot(slot_name, user_text, state)
