import json
import unsloth
from unsloth import FastLanguageModel
from datasets import load_dataset
from transformers import TrainingArguments
from trl import SFTTrainer

MAX_SEQ_LEN = 2048

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name     = "unsloth/Meta-Llama-3.1-8B-Instruct",
    max_seq_length = MAX_SEQ_LEN,
    load_in_4bit   = True,
)
model = FastLanguageModel.get_peft_model(
    model,
    r           = 16,
    lora_alpha  = 32,
    lora_dropout= 0.05,
)

# 這裡用你剛產的 train_alpaca.json
raw_ds = load_dataset("json", data_files="data/train_alpaca.json")["train"]

SYSTEM_PROMPT = (
    "你是一個餐廳線上訂位設定助理，會根據店家營業時間與歷史訂位資料，"
    "產生訂位設定建議：服務時長（平日/週末）、桌型張數（2/4/5 人桌），"
    "以及每日可訂時段（午/晚）。"
    "回覆時只輸出 JSON，欄位為 duration、table_mix、time_windows。"
)

def format_example(example):
    instr = example.get("instruction", "")
    # 這裡的 input/output 是你剛剛 dump 出來的「字串」
    input_str  = example.get("input", "{}")
    output_str = example.get("output", "{}")

    # user 內容 = 任務說明 + input JSON 字串
    user_content = (
        f"任務說明：{instr}\n\n"
        f"輸入資料（store_profile, business_hours, history_features）：\n"
        f"{input_str}"
    )

    assistant_content = output_str

    messages = [
        {"role": "system",    "content": SYSTEM_PROMPT},
        {"role": "user",      "content": user_content},
        {"role": "assistant", "content": assistant_content},
    ]

    text = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=False,
    )

    example["text"] = text
    return example

dataset = raw_ds.map(format_example)

trainer = SFTTrainer(
    model             = model,
    tokenizer         = tokenizer,
    train_dataset     = dataset,
    dataset_text_field= "text",
    max_seq_length    = MAX_SEQ_LEN,
    args = TrainingArguments(
        output_dir                  = "checkpoints/pb-lora",
        num_train_epochs            = 3,
        per_device_train_batch_size = 2,
        gradient_accumulation_steps = 8,
        learning_rate               = 2e-4,
        logging_steps               = 25,
        save_steps                  = 500,
    ),
)

trainer.train()
trainer.save_model()
tokenizer.save_pretrained("checkpoints/pb-lora")
