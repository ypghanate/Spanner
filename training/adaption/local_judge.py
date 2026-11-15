"""The Adaption-trained step judge, running on this laptop (CPU) with transformers + peft.

    from training.adaption.local_judge import judge
    judge(user_text) -> ("{"status": ..., "mistake": ...}", latency_ms)

Base: the cached Qwen/Qwen3.5-2B. Adapter: data/models/adaption/judge (downloaded from the AutoScientist run).
Loads once per process. No thinking, greedy, short answers: the live budget is one decision per frame.

    python -m training.adaption.local_judge            # smoke test on 5 held-out rows, adapter vs base
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "data" / "models" / "adaption" / "judge"
BASE = os.getenv("JUDGE_BASE_MODEL", "Qwen/Qwen3.5-2B")
_model = _tok = None
_adapter_on = True


def load(adapter: bool = True):
    global _model, _tok, _adapter_on
    if _model is not None and _adapter_on == adapter:
        return _model, _tok
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.set_num_threads(max(1, (os.cpu_count() or 4) - 1))
    _tok = AutoTokenizer.from_pretrained(BASE)
    m = AutoModelForCausalLM.from_pretrained(BASE, dtype=torch.float32, low_cpu_mem_usage=True)
    if adapter:
        from peft import PeftModel

        m = PeftModel.from_pretrained(m, str(ADAPTER))
        m = m.merge_and_unload()  # plain weights: no adapter overhead per token
    m.eval()
    _model, _adapter_on = m, adapter
    return _model, _tok


def judge(user: str, system: str | None = None, max_new_tokens: int = 48, adapter: bool = True) -> tuple[str, int]:
    """One decision. Returns (raw model text, latency_ms)."""
    import torch

    if system is None:
        from training.step_tracker import SYSTEM

        system = SYSTEM
    model, tok = load(adapter)
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    except TypeError:
        text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    ids = tok(text, return_tensors="pt")
    t0 = time.perf_counter()
    with torch.inference_mode():
        out = model.generate(**ids, max_new_tokens=max_new_tokens, do_sample=False,
                             pad_token_id=tok.eos_token_id)
    ms = round((time.perf_counter() - t0) * 1000)
    return tok.decode(out[0, ids["input_ids"].shape[1]:], skip_special_tokens=True).strip(), ms


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    rows = [json.loads(l) for l in (ROOT / "training/adaption/data/eval.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()][:n]
    for adapter in (True, False):
        print(f"\n== {'ADAPTER' if adapter else 'BASE'} ==")
        t = time.perf_counter(); load(adapter); print(f"loaded in {time.perf_counter() - t:.0f} s")
        for r in rows:
            truth = json.loads(r["messages"][2]["content"])["status"]
            text, ms = judge(r["messages"][1]["content"], adapter=adapter)
            print(f"{ms:6d} ms  truth={truth:<12} -> {text[:100]!r}")
