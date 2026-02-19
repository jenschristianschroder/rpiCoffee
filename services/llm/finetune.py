"""
Fine-tune Qwen2.5-0.5B-Instruct with QLoRA (4-bit) on the coffee dataset.
Designed for a small dataset (~48 samples): high epochs, small batch, aggressive
learning rate with cosine schedule.

Run:
    python finetune.py                       # defaults
    python finetune.py --epochs 10 --lr 2e-4 # override
"""

import argparse
import json
from pathlib import Path

import torch
from datasets import Dataset
from peft import LoraConfig, get_peft_model, TaskType
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
)
from trl import SFTTrainer, SFTConfig

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
BASE_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
DATA_PATH = "dataset-chat.jsonl"
OUTPUT_DIR = "coffee-lora"
MAX_SEQ_LEN = 512  # keeps memory low; longest sample is ~200 tokens


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--model", default=BASE_MODEL)
    p.add_argument("--data", default=DATA_PATH)
    p.add_argument("--output", default=OUTPUT_DIR)
    p.add_argument("--epochs", type=int, default=8)
    p.add_argument("--lr", type=float, default=2e-4)
    p.add_argument("--batch", type=int, default=2)
    p.add_argument("--grad_accum", type=int, default=4)
    p.add_argument("--max_seq_len", type=int, default=MAX_SEQ_LEN)
    p.add_argument("--lora_r", type=int, default=16)
    p.add_argument("--lora_alpha", type=int, default=32)
    p.add_argument("--lora_dropout", type=float, default=0.05)
    return p.parse_args()


def load_dataset(path: str) -> Dataset:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return Dataset.from_list(rows)


def main():
    args = parse_args()

    # --- Tokenizer ---
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # --- Load base model (QLoRA on GPU, full precision on CPU) ---
    has_cuda = torch.cuda.is_available()

    if has_cuda:
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )
        model = AutoModelForCausalLM.from_pretrained(
            args.model,
            quantization_config=bnb_config,
            device_map="auto",
            trust_remote_code=True,
        )
    else:
        print("WARNING: No CUDA GPU detected — training on CPU (slower)")
        model = AutoModelForCausalLM.from_pretrained(
            args.model,
            torch_dtype=torch.float32,
            trust_remote_code=True,
        )
    model.config.use_cache = False

    # --- LoRA ---
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                         "gate_proj", "up_proj", "down_proj"],
        bias="none",
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # --- Dataset ---
    dataset = load_dataset(args.data)
    print(f"Training samples: {len(dataset)}")

    # --- Training args ---
    use_bf16 = has_cuda and torch.cuda.is_bf16_supported()
    use_fp16 = has_cuda and not use_bf16

    training_args = SFTConfig(
        output_dir=args.output,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_steps=5,
        weight_decay=0.01,
        bf16=use_bf16,
        fp16=use_fp16,
        logging_steps=5,
        save_strategy="epoch",
        save_total_limit=2,
        optim="paged_adamw_8bit" if has_cuda else "adamw_torch",
        report_to="none",
        seed=42,
        dataloader_pin_memory=False,
        max_length=args.max_seq_len,
    )

    # --- Trainer (SFTTrainer handles chat template formatting) ---
    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        processing_class=tokenizer,
    )

    trainer.train()
    trainer.save_model(args.output)
    tokenizer.save_pretrained(args.output)
    print(f"\nLoRA adapter saved to {args.output}/")


if __name__ == "__main__":
    main()
