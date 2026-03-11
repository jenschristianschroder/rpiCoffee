"""
Merge the LoRA adapter back into the base model, then convert to GGUF
format with Q4_K_M quantisation for fast inference on Raspberry Pi.

Prerequisites:
    pip install -r requirements-train.txt
    git clone https://github.com/ggerganov/llama.cpp.git   (for convert script)

Run:
    python merge_and_export.py
    python merge_and_export.py --quant Q5_K_M   # different quant level
"""

import argparse
import subprocess
import sys
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

BASE_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
LORA_DIR = "coffee-lora"
MERGED_DIR = "coffee-merged"
GGUF_DIR = "coffee-gguf"
LLAMA_CPP = "llama.cpp"  # path to cloned llama.cpp repo


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default=BASE_MODEL)
    p.add_argument("--lora", default=LORA_DIR)
    p.add_argument("--merged", default=MERGED_DIR)
    p.add_argument("--gguf_dir", default=GGUF_DIR)
    p.add_argument("--llama_cpp", default=LLAMA_CPP)
    p.add_argument("--quant", default="Q4_K_M",
                    help="GGUF quantisation type (Q4_K_M recommended for Pi)")
    return p.parse_args()


def merge(args):
    print("==> Loading base model …")
    base = AutoModelForCausalLM.from_pretrained(
        args.base, torch_dtype=torch.float16, trust_remote_code=True
    )
    tokenizer = AutoTokenizer.from_pretrained(args.base, trust_remote_code=True)

    print("==> Applying LoRA adapter …")
    model = PeftModel.from_pretrained(base, args.lora)
    model = model.merge_and_unload()

    print(f"==> Saving merged model to {args.merged}/ …")
    model.save_pretrained(args.merged, safe_serialization=True)
    tokenizer.save_pretrained(args.merged)
    print("    Merge complete.")


def convert_to_gguf(args):
    convert_script = Path(args.llama_cpp) / "convert_hf_to_gguf.py"
    if not convert_script.exists():
        sys.exit(
            f"ERROR: {convert_script} not found.\n"
            f"Clone llama.cpp first:\n"
            f"  git clone https://github.com/ggerganov/llama.cpp.git"
        )

    out_dir = Path(args.gguf_dir)
    out_dir.mkdir(exist_ok=True)
    f16_path = out_dir / "coffee-f16.gguf"
    quant_path = out_dir / f"coffee-{args.quant}.gguf"

    # Step 1 – convert to f16 GGUF
    print("==> Converting to f16 GGUF …")
    subprocess.run(
        [sys.executable, str(convert_script), args.merged,
         "--outfile", str(f16_path), "--outtype", "f16"],
        check=True,
    )

    # Step 2 – quantise
    quantize_bin = Path(args.llama_cpp) / "build" / "bin" / "llama-quantize"
    if not quantize_bin.exists():
        # Try alternate location
        quantize_bin = Path(args.llama_cpp) / "llama-quantize"
    if not quantize_bin.exists():
        print(
            f"WARNING: llama-quantize not found at {quantize_bin}.\n"
            f"Build llama.cpp first, then run:\n"
            f"  llama-quantize {f16_path} {quant_path} {args.quant}\n"
        )
        return

    print(f"==> Quantising to {args.quant} …")
    subprocess.run(
        [str(quantize_bin), str(f16_path), str(quant_path), args.quant],
        check=True,
    )
    print(f"    Quantised model: {quant_path}")
    print(f"    Size: {quant_path.stat().st_size / 1024 / 1024:.1f} MB")

    # Clean up f16 intermediate
    f16_path.unlink(missing_ok=True)
    print("    Removed intermediate f16 file.")


def main():
    args = parse_args()
    merge(args)
    convert_to_gguf(args)
    print("\nDone! Copy the GGUF file to your Raspberry Pi or use Docker.")


if __name__ == "__main__":
    main()
