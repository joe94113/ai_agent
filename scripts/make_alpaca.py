import json
from pathlib import Path

data = []
for line in Path("setup_train.jsonl").read_text(encoding="utf-8").splitlines():
    row = json.loads(line)
    record = {
        "instruction": row["instruction"],
        "input": json.dumps(row.get("input", {}), ensure_ascii=False),
        "output": json.dumps(row.get("output", {}), ensure_ascii=False),
    }
    data.append(record)

Path("data/train_alpaca.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")