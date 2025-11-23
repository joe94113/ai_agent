# scripts/train_lora.py
import unsloth
from unsloth import FastLanguageModel
from datasets import load_dataset
from transformers import TrainingArguments
from trl import SFTTrainer

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name = "unsloth/Meta-Llama-3.1-8B-Instruct",
    load_in_4bit = True,
)
model = FastLanguageModel.get_peft_model(model, r=16, lora_alpha=32, lora_dropout=0.05)

dataset = load_dataset("json", data_files="data/train_alpaca.json")["train"]

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    dataset_text_field="output",
    max_seq_length=2048,
    args=TrainingArguments(
        output_dir="checkpoints/pb-lora",
        num_train_epochs=3,
        per_device_train_batch_size=2,
        gradient_accumulation_steps=8,
        learning_rate=2e-4,
        logging_steps=25,
        save_steps=500,
    ),
)
trainer.train()
trainer.save_model()