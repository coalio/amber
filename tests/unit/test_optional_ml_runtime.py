from __future__ import annotations

import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from src import cli
from src.attention.constants import ATTENTION_LABEL_HYPOTHESES
from src.attention.constants import DEFAULT_ATTENTION_MODEL, DEFAULT_ATTENTION_MODEL_REVISION
from src.attention.scoring.managed import ManagedAttentionPolicyScorer
from src.attention.scoring.zero_shot import AttentionPolicyScorer


class _RecordingInference:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], int]] = []

    def score_labels(self, text: str, hypotheses: dict[str, str], *, max_length: int) -> dict[str, float]:
        self.calls.append((text, hypotheses, max_length))
        return {
            label: 0.8 if label == "direct_question_or_request" else 0.1
            for label in hypotheses
        }


def test_attention_policy_can_use_external_inference_without_loading_torch() -> None:
    inference = _RecordingInference()
    scorer = AttentionPolicyScorer(inference=inference, warm=False)

    first = scorer.classify_text("  Can   you help? ")
    second = scorer.classify_text("Can you help?")

    assert first is second
    assert first.labels["direct_question_or_request"] == 0.8
    assert first.top_labels == ["direct_question_or_request"]
    assert inference.calls == [("Can you help?", ATTENTION_LABEL_HYPOTHESES, 512)]


def test_installer_prefetch_pin_matches_runtime_constants() -> None:
    installer = (Path(__file__).resolve().parents[2] / "installer" / "install.sh").read_text(encoding="utf-8")

    assert f'DEFAULT_ATTENTION_MODEL="{DEFAULT_ATTENTION_MODEL}"' in installer
    assert f'DEFAULT_ATTENTION_MODEL_REVISION="{DEFAULT_ATTENTION_MODEL_REVISION}"' in installer


def test_release_builder_rejects_bundled_ml_variant() -> None:
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["AMBER_BUILD_ML"] = "1"

    result = subprocess.run(
        ["bash", "scripts/build_release.sh"],
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode != 0
    assert "Full installs fetch optional ML dependencies at install time" in result.stderr


def test_managed_attention_scorer_uses_installer_runtime_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = tmp_path / "worker.py"
    worker.write_text(
        textwrap.dedent(
            """\
            import json
            import sys

            print(json.dumps({"status": "ready"}), flush=True)
            for line in sys.stdin:
                request = json.loads(line)
                labels = {
                    label: 0.75 if label == "worth_replying_to" else 0.05
                    for label in request["hypotheses"]
                }
                print(json.dumps({"labels": labels}), flush=True)
            """
        ),
        encoding="utf-8",
    )
    worker.chmod(worker.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("AMBER_ML_RUNTIME_PYTHON", sys.executable)
    monkeypatch.setenv("AMBER_ML_WORKER", str(worker))
    monkeypatch.setenv("AMBER_ML_MODEL_CACHE", str(tmp_path / "models"))

    scorer = ManagedAttentionPolicyScorer(warm=False)
    try:
        classification = scorer.classify_text("Please take a look.")
    finally:
        scorer.close()

    assert classification.labels["worth_replying_to"] == 0.75
    assert classification.model_name


def test_managed_attention_scorer_explains_missing_optional_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AMBER_ML_RUNTIME_PYTHON", str(tmp_path / "missing-python"))
    scorer = ManagedAttentionPolicyScorer(warm=False)

    with pytest.raises(RuntimeError, match="choose Full"):
        scorer.classify_text("hello")


def test_attention_check_exercises_scorer_and_closes_it(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeClassification:
        model_name = "example/model"
        model_revision = "revision"

    class FakeScorer:
        closed = False

        def classify_text(self, text: str) -> FakeClassification:
            assert text == "hello"
            return FakeClassification()

        def close(self) -> None:
            self.closed = True

    scorer = FakeScorer()
    monkeypatch.setattr("src.runtime._build_attention_scorer", lambda **_kwargs: scorer)

    assert cli.main(["attention", "check"]) == 0
    assert scorer.closed is True
    assert capsys.readouterr().out == "modernbert scorer ok: example/model@revision\n"
