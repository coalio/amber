from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, TextIO

from src.attention.constants import DEFAULT_ATTENTION_MODEL, DEFAULT_ATTENTION_MODEL_REVISION
from src.attention.scoring.policy import AttentionPolicyScorer
from src.config.config import amber_home, resources_dir
from src.utils.process import open_host_process


class ManagedModernBertInference:
    """ModernBERT inference over a persistent worker subprocess."""

    def __init__(
        self,
        model_name: str,
        model_revision: str,
        *,
        device: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.model_revision = model_revision
        self.device = device or os.getenv("AMBER_ATTENTION_DEVICE") or "cpu"
        self._process: subprocess.Popen[str] | None = None
        self._stderr: TextIO | None = None
        self._lock = threading.Lock()

    def score_labels(self, text: str, hypotheses: dict[str, str], *, max_length: int) -> dict[str, float]:
        request = {
            "text": text,
            "hypotheses": hypotheses,
            "max_length": max_length,
        }
        with self._lock:
            process = self._ensure_process()
            assert process.stdin is not None
            process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
            process.stdin.flush()
            response = self._read_response(process)

        labels = response.get("labels")
        if not isinstance(labels, dict):
            raise RuntimeError("ModernBERT worker returned an invalid labels response.")
        return {str(label): float(value) for label, value in labels.items()}

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is not None:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None

    def _ensure_process(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process

        runtime_python = Path(
            os.getenv("AMBER_ML_RUNTIME_PYTHON") or amber_home() / "ml-runtime" / "bin" / "python"
        ).expanduser()
        worker_path = Path(
            os.getenv("AMBER_ML_WORKER") or resources_dir() / "ml" / "attention_worker.py"
        ).expanduser()
        model_cache = Path(
            os.getenv("AMBER_ML_MODEL_CACHE") or amber_home() / "models"
        ).expanduser()
        if not runtime_python.is_file():
            raise RuntimeError(
                "ModernBERT optional dependencies are not installed. "
                "Rerun the Amber installer and choose Full."
            )
        if not worker_path.is_file():
            raise RuntimeError(f"Amber's ModernBERT worker is missing: {worker_path}")

        # keep third-party runtime output out of the JSON protocol without risking a full pipe
        self._stderr = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        command = [
            str(runtime_python),
            str(worker_path),
            "--model",
            self.model_name,
            "--revision",
            self.model_revision,
            "--device",
            self.device,
            "--cache-dir",
            str(model_cache),
        ]
        process = open_host_process(
            command,
            opener=subprocess.Popen,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
            bufsize=1,
        )
        self._process = process
        response = self._read_response(process)
        if response.get("status") != "ready":
            self.close()
            raise RuntimeError("ModernBERT worker did not report ready.")
        return process

    def _read_response(self, process: subprocess.Popen[str]) -> dict[str, Any]:
        assert process.stdout is not None
        line = process.stdout.readline()
        if not line:
            detail = self._stderr_detail()
            raise RuntimeError(f"ModernBERT worker stopped unexpectedly{detail}.")
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError("ModernBERT worker returned invalid JSON.") from exc
        if not isinstance(response, dict):
            raise RuntimeError("ModernBERT worker returned an invalid response.")
        if response.get("error"):
            raise RuntimeError(f"ModernBERT worker failed: {response['error']}")
        return response

    def _stderr_detail(self) -> str:
        if self._stderr is None:
            return ""
        self._stderr.flush()
        self._stderr.seek(0)
        detail = self._stderr.read().strip()
        return f": {detail}" if detail else ""

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class ManagedAttentionPolicyScorer(AttentionPolicyScorer):
    """Attention policy scorer backed by the optional managed runtime."""

    def __init__(
        self,
        model_name: str | None = None,
        *,
        revision: str | None = None,
        device: str | None = None,
        max_length: int = 512,
        cache_size: int = 1024,
        warm: bool = True,
    ) -> None:
        resolved_model = model_name or os.getenv("AMBER_ATTENTION_MODEL") or DEFAULT_ATTENTION_MODEL
        resolved_revision = (
            revision or os.getenv("AMBER_ATTENTION_MODEL_REVISION") or DEFAULT_ATTENTION_MODEL_REVISION
        )
        inference = ManagedModernBertInference(
            resolved_model,
            resolved_revision,
            device=device,
        )
        super().__init__(
            inference,
            resolved_model,
            revision=resolved_revision,
            max_length=max_length,
            cache_size=cache_size,
            warm=warm,
        )
        self._managed_inference = inference

    def close(self) -> None:
        self._managed_inference.close()
