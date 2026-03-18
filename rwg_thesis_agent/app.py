from __future__ import annotations

import argparse
import json
from typing import Any, Dict

from .baseline_fsm import BaselineFSMAgent
from .builders import build_internal_output
from .constraints import feed_readiness, update_constraints
from .evaluation import benchmark_as_json
from .extractors import extract_slot_value, get_extractor_mode, set_extractor_mode
from .policy_agent import DynamicPolicyAgent
from .prompt_handlers import ask_text, retry_hint
from .state_tracker import DEFAULT_MERCHANT_CONTEXT, add_history, create_state, increment_asked


AGENTS = {
    "fsm": BaselineFSMAgent,
    "policy": DynamicPolicyAgent,
}


def print_merchant_context(state: Dict[str, Any]) -> None:
    merchant = state["merchant_context"]
    print("\n=== 預載商家資料（唯讀）===")
    print(json.dumps(merchant, ensure_ascii=False, indent=2))
    print()



def run_interactive(agent_key: str) -> None:
    state = create_state(DEFAULT_MERCHANT_CONTEXT)
    agent = AGENTS[agent_key]()

    print(f"✅ 啟動 {agent_key} 版本 agent（extractor={get_extractor_mode()}，輸入 exit 離開）")
    print_merchant_context(state)

    while True:
        update_constraints(state)
        slot = agent.choose_next_slot(state)
        if slot is None:
            break

        question = ask_text(slot, state)

        while True:
            increment_asked(state, slot)
            print(f"\n🤖 {question}")
            user_in = input("你：").strip()
            if user_in.lower() in {"exit", "quit"}:
                print("結束。")
                return

            parsed_value, confidence, message = extract_slot_value(slot, user_in, state)
            if parsed_value is None:
                print(f"⚠️ {message}。{retry_hint(slot)}")
                continue

            state["slots"][slot]["value"] = parsed_value
            state["slots"][slot]["confidence"] = confidence
            state["slots"][slot]["confirmed"] = True
            add_history(state, slot, question, user_in, parsed_value)
            break

        update_constraints(state)
        if state.get("conflicts"):
            print("\n⚠️ 目前有些地方需要注意：")
            for c in state["conflicts"]:
                print("-", c)

    output = build_internal_output(state)
    print("\n✅ Feed readiness:", feed_readiness(state))
    print("\n✅ 完整內部輸出 JSON\n")
    print(json.dumps(output, ensure_ascii=False, indent=2))



def main() -> None:
    parser = argparse.ArgumentParser(description="RWG thesis agent demo")
    parser.add_argument("mode", choices=["fsm", "policy", "eval"], help="要執行的模式")
    parser.add_argument(
        "--extractor",
        choices=["rule", "ollama", "auto"],
        default="rule",
        help="slot extractor 模式：rule=規則、ollama=只用 OLLAMA、auto=先 OLLAMA 後退回規則",
    )
    args = parser.parse_args()

    set_extractor_mode(args.extractor)

    if args.mode == "eval":
        if args.extractor != "rule":
            print("⚠️ eval 建議使用 --extractor rule，避免本機模型波動影響可重現性。")
        print(benchmark_as_json())
        return
    run_interactive(args.mode)


if __name__ == "__main__":
    main()
