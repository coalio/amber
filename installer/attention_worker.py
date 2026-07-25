#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Amber ModernBERT inference worker")
    parser.add_argument("--model", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--prefetch", action="store_true")
    return parser


def load_runtime(args: argparse.Namespace) -> tuple[Any, Any, Any, int]:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    # full installs intentionally use the maintainers' CPU wheel instead of CUDA payloads
    if args.device == "cpu" and torch.version.cuda is not None:
        raise RuntimeError(
            f"Expected CPU-only PyTorch, but {torch.__version__} reports CUDA {torch.version.cuda}."
        )
    cache_dir = Path(args.cache_dir).expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    tokenizer = AutoTokenizer.from_pretrained(
        args.model,
        revision=args.revision,
        cache_dir=cache_dir,
    )
    model = AutoModelForSequenceClassification.from_pretrained(
        args.model,
        revision=args.revision,
        cache_dir=cache_dir,
    )
    model.to(args.device)
    model.eval()

    label2id = getattr(model.config, "label2id", {}) or {}
    entailment_index = next(
        (int(index) for label, index in label2id.items() if str(label).lower() == "entailment"),
        0,
    )
    return torch, tokenizer, model, entailment_index


def score_request(
    request: dict[str, Any],
    *,
    torch: Any,
    tokenizer: Any,
    model: Any,
    entailment_index: int,
) -> dict[str, float]:
    text = str(request.get("text") or "")
    hypotheses = request.get("hypotheses")
    if not isinstance(hypotheses, dict) or not hypotheses:
        raise ValueError("hypotheses must be a non-empty object")
    labels = [str(label) for label in hypotheses]
    max_length = int(request.get("max_length") or 512)

    # evaluate every policy hypothesis in one model batch
    inputs = tokenizer(
        [text] * len(labels),
        [str(hypotheses[label]) for label in labels],
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=max_length,
    )
    device = next(model.parameters()).device
    inputs = {key: value.to(device) for key, value in inputs.items()}
    with torch.inference_mode():
        logits = model(**inputs).logits
        probabilities = torch.softmax(logits.float(), dim=-1)[:, entailment_index]
    values = probabilities.detach().cpu().tolist()
    return {label: round(float(value), 4) for label, value in zip(labels, values)}


def serve(args: argparse.Namespace, runtime: tuple[Any, Any, Any, int]) -> int:
    torch, tokenizer, model, entailment_index = runtime
    print(json.dumps({"status": "ready"}), flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            labels = score_request(
                request,
                torch=torch,
                tokenizer=tokenizer,
                model=model,
                entailment_index=entailment_index,
            )
            response = {"labels": labels}
        except Exception as exc:
            response = {"error": str(exc)}
        print(json.dumps(response, separators=(",", ":")), flush=True)
    return 0


def main() -> int:
    args = build_parser().parse_args()
    runtime = load_runtime(args)
    if args.prefetch:
        return 0
    return serve(args, runtime)


if __name__ == "__main__":
    raise SystemExit(main())
