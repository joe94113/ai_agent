# make_ft_dataset.py
# 讀取 setup_train.jsonl，輸出 booking_setup_ft.jsonl（messages 格式）

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SRC_PATH = BASE_DIR / "setup_train.jsonl"        # Laravel 匯出的檔
OUT_PATH = BASE_DIR / "booking_setup_ft.jsonl"   # 給 fine-tune 用的檔


SYSTEM_PROMPT = (
    "你是 PB 撇步的餐飲訂位設定顧問。"
    "請根據店家資料、營業時間與歷史訂位特徵，產生 JSON 格式的訂位設定建議，"
    "包含 duration（weekday_min, weekend_min, confidence, rationale）、"
    "table_mix（t2, t4, t5, confidence, rationale）、"
    "time_windows（weekday, begin_at, duration_min）。"
    "只輸出 JSON，不要任何說明文字。"
)


def main():
    if not SRC_PATH.exists():
        print(f"找不到來源檔案：{SRC_PATH}")
        return

    count = 0
    with SRC_PATH.open("r", encoding="utf-8") as fin, \
         OUT_PATH.open("w", encoding="utf-8") as fout:

        for line in fin:
            line = line.strip()
            if not line:
                continue

            try:
                inst = json.loads(line)
            except Exception:
                continue

            input_obj = inst.get("input")
            output_obj = inst.get("output")
            if not input_obj or not output_obj:
                continue

            # 轉成字串，讓模型看到整包 input JSON
            input_json_str = json.dumps(input_obj, ensure_ascii=False)
            output_json_str = json.dumps(output_obj, ensure_ascii=False)

            row = {
                "messages": [
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT
                    },
                    {
                        "role": "user",
                        "content": input_json_str
                    },
                    {
                        "role": "assistant",
                        "content": output_json_str
                    },
                ]
            }

            fout.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1

    print(f"已產生 fine-tune 資料：{OUT_PATH}，共 {count} 筆樣本")


if __name__ == "__main__":
    main()
