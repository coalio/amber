from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest

from src.events.ai import SemanticDecisionMadeEvent, SemanticDecisionPayload
from src.events.attention import AttentionDecisionMadeEvent, AttentionDecisionPayload, MemoryCardPayload
from src.events.bus import EventBus, emitter_context
from src.events.receiver import (
    TelegramAttachmentPayload,
    TelegramMessagePayload,
    TelegramReplySenderPayload,
    TelegramSenderPayload,
    TelegramTransportPayload,
)
from src.utils import logging as log_utils


@pytest.fixture(autouse=True)
def reset_event_bus() -> None:
    EventBus.reset_for_tests()
    yield
    EventBus.reset_for_tests()


def test_attention_dispatch_logs_decision_summary_without_emit_wrapper_noise(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    event = AttentionDecisionMadeEvent(
        chat_id=1001001001,
        payload=AttentionDecisionPayload(
            decision="surface",
            message=_build_message_payload("Hey amber can you help me review this patch?"),
            attention_score=0.7312,
            heuristic_score=0.88,
            model_score=0.55,
            reasons=["direct_mention", "question_intent", "memory_hit"],
            memory_cards=[MemoryCardPayload(memory_id="mem_patch_review", text="User asked for patch reviews before.")],
            engaged_user_bypass=False,
            reply_target_candidate=412,
        ),
    )

    with emitter_context("attention"):
        EventBus.emit(event)

    dispatch_record = _dispatch_record(caplog, "AttentionDecisionMadeEvent")
    payload = dispatch_record.context["payload"]

    assert dispatch_record.context["origin"] == "attention"
    assert payload["decision"] == "surface"
    assert payload["attention_score"] == 0.731
    assert payload["heuristic_score"] == 0.88
    assert payload["model_score"] == 0.55
    assert payload["reasons"] == ["direct_mention", "question_intent", "memory_hit"]
    assert payload["memory_count"] == 1
    assert payload["memory_ids"] == ["mem_patch_review"]
    assert payload["message_id"] == 412
    assert payload["sender_name"] == "Fixture Sender"
    assert payload["content_preview"] == "Hey amber can you help me review this patch?"
    assert payload["reply_target_candidate"] == 412
    assert payload["media_type"] is None
    assert payload["is_self"] is False
    assert "event_bus.emit.start" not in [record.getMessage() for record in caplog.records]
    assert "event_bus.emit.success" not in [record.getMessage() for record in caplog.records]


def test_semantic_dispatch_logs_action_summary(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    reply_text = "I found the issue. The handler never logs the attention decision payload."
    event = SemanticDecisionMadeEvent(
        chat_id=1001001001,
        payload=SemanticDecisionPayload(
            action="reply",
            chat_id=1001001001,
            reply_text=reply_text,
            reply_to_message_id=411,
            referenced_memory_ids=["mem_patch_review", "mem_logging"],
            confidence=0.8239,
            notes=["harness_passed"],
            trigger_message_id=412,
            session_id="sess_attention",
        ),
    )

    with emitter_context("ai"):
        EventBus.emit(event)

    dispatch_record = _dispatch_record(caplog, "SemanticDecisionMadeEvent")
    payload = dispatch_record.context["payload"]

    assert dispatch_record.context["origin"] == "ai"
    assert payload["action"] == "reply"
    assert payload["confidence"] == 0.824
    assert payload["reply_to_message_id"] == 411
    assert payload["trigger_message_id"] == 412
    assert payload["session_id"] == "sess_attention"
    assert payload["referenced_memory_count"] == 2
    assert payload["notes"] == ["harness_passed"]
    assert payload["reply_length"] == len(reply_text)
    assert payload["reply_preview"] == reply_text


def test_configure_logging_creates_timestamped_run_file(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = logging.getLogger()
    previous_handlers = set(root.handlers)
    monkeypatch.setattr(log_utils, "_RUN_LOG_PATH", None)

    run_path = log_utils.configure_logging(log_dir=tmp_path / ".logs", timezone_name="America/Managua")
    try:
        assert run_path is not None
        assert run_path.parent.parent == tmp_path / ".logs"
        assert run_path.parent.name.count("-") == 2
        assert run_path.suffix == ".log"

        log_utils.get_logger("amber.test").info(
            "log.smoke",
            extra={"event": "log.smoke", "context": {"message": "wrote log file", "ok": True}},
        )
        for handler in root.handlers:
            handler.flush()

        log_text = run_path.read_text(encoding="utf-8")
        assert "[INFO] log.smoke: wrote log file" in log_text
        assert "ok=true" in log_text
        assert "\033[" not in log_text
    finally:
        for handler in list(root.handlers):
            if handler not in previous_handlers and isinstance(handler, logging.FileHandler):
                root.removeHandler(handler)
                handler.close()


def test_human_readable_formatter_renders_codex_progress_as_colored_text() -> None:
    record = logging.LogRecord(
        name="amber.adapters.codex",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="codex.progress",
        args=(),
        exc_info=None,
    )
    record.event = "codex.progress"
    record.context = {
        "message": "preparing codex sandbox directories",
        "task_id": "task 1",
        "ready": True,
    }

    line = log_utils.HumanReadableFormatter(use_color=True).format(record)

    assert "\033[32m[INFO]" in line
    assert "amber.adapters.codex" not in line
    assert "codex.progress: preparing codex sandbox directories" in line
    assert 'task_id="task 1"' in line
    assert "ready=true" in line
    assert "message=" not in line


def test_human_readable_formatter_falls_back_to_logger_name() -> None:
    record = logging.LogRecord(
        name="amber.adapters.codex",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="sandbox setup took longer than expected",
        args=(),
        exc_info=None,
    )

    line = log_utils.HumanReadableFormatter().format(record)

    assert line == "[WARNING] amber.adapters.codex: sandbox setup took longer than expected"


def _build_message_payload(content: str) -> TelegramMessagePayload:
    return TelegramMessagePayload(
        message_id=412,
        chat_id=1001001001,
        sender=TelegramSenderPayload(id="user-123", name="Fixture Sender"),
        timestamp=datetime(2026, 4, 21, 3, 54, 5, tzinfo=timezone.utc),
        content=content,
        raw_text=content,
        reply_to_message_id=None,
        reply_to_sender=TelegramReplySenderPayload(),
        mentions=["amber"],
        attachment=TelegramAttachmentPayload(),
        transport=TelegramTransportPayload(peer_id=1001001001, raw_chat_id=1001001001, raw_message_id=412),
        reaction_count=2,
    )


def _dispatch_record(caplog: pytest.LogCaptureFixture, event_name: str) -> logging.LogRecord:
    return next(
        record
        for record in caplog.records
        if record.getMessage() == "event.dispatch" and getattr(record, "event", None) == event_name
    )
