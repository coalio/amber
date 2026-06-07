from __future__ import annotations

import json
import os
import random
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pytest

from src.events.action import OutboundMessageSentEvent
from src.events.ai import SemanticDecisionMadeEvent
from src.events.attention import AttentionDecisionMadeEvent
from src.events.bus import EventBus, emitter_context
from src.events.context import ContextFrameReadyEvent
from src.events.outbound import OutboundMessagePreparedEvent
from src.events.receiver import (
    TelegramAttachmentPayload,
    TelegramMessagePayload,
    TelegramMessageReceivedEvent,
    TelegramReplySenderPayload,
    TelegramSenderPayload,
    TelegramTransportPayload,
)
from src.runtime import build_application
from src.utils.time import parse_iso_datetime


ROOT = Path(__file__).resolve().parents[2]
TEST_OUTPUT_DIR = ROOT / "test_outputs" / "orchestration_v1" / "integration"
RANDOM_SEGMENT_PATHS = [
    ROOT / "training" / "datasets" / "v1-reviewed" / "policy" / "train.jsonl",
    ROOT / "training" / "datasets" / "v1-reviewed" / "policy" / "val.jsonl",
    ROOT / "training" / "datasets" / "v1-reviewed" / "reply" / "train.jsonl",
    ROOT / "training" / "datasets" / "v1-reviewed" / "reply" / "val.jsonl",
    ROOT / "training" / "datasets" / "v1-weak" / "policy" / "val.jsonl",
]
TEST_CHAT_ID = 1333870316
MIN_SEGMENTS = 5
MAX_SEGMENTS = 30
REQUIRED_ROUTES = {
    "reply",
    "ignore",
    "reply_target",
    "engaged_user_continuation",
    "engaged_user_expansion",
}


@dataclass(frozen=True)
class DatasetSegment:
    sample_id: str
    source_path: str
    split_key: str
    focus_message_id: int
    focus_sender: str
    label_reply_policy: str | None
    label_reply_target_candidate: int | None
    route_hints: tuple[str, ...]
    window: list[dict[str, Any]]


class PipelineCapture:
    def __init__(self) -> None:
        self.attention: list[AttentionDecisionMadeEvent] = []
        self.frames: list[ContextFrameReadyEvent] = []
        self.semantic: list[SemanticDecisionMadeEvent] = []
        self.outbound: list[OutboundMessagePreparedEvent] = []
        self.delivery: list[OutboundMessageSentEvent] = []
        EventBus.subscribe("AttentionDecisionMadeEvent", self._capture_attention)
        EventBus.subscribe("ContextFrameReadyEvent", self._capture_frame)
        EventBus.subscribe("SemanticDecisionMadeEvent", self._capture_semantic)
        EventBus.subscribe("OutboundMessagePreparedEvent", self._capture_outbound)
        EventBus.subscribe("OutboundMessageSentEvent", self._capture_delivery)

    def _capture_attention(self, event: AttentionDecisionMadeEvent) -> None:
        self.attention.append(event)

    def _capture_frame(self, event: ContextFrameReadyEvent) -> None:
        self.frames.append(event)

    def _capture_semantic(self, event: SemanticDecisionMadeEvent) -> None:
        self.semantic.append(event)

    def _capture_outbound(self, event: OutboundMessagePreparedEvent) -> None:
        self.outbound.append(event)

    def _capture_delivery(self, event: OutboundMessageSentEvent) -> None:
        self.delivery.append(event)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def build_route_hints(row: dict[str, Any]) -> tuple[str, ...]:
    hints: set[str] = set()
    target = row.get("target") or {}
    reply_policy = target.get("reply_policy") or row.get("target_reply_policy")
    reply_target_candidate = target.get("reply_target_candidate") or row.get("target_reply_target_candidate")
    if reply_policy == "reply":
        hints.add("reply")
    elif reply_policy == "ignore":
        hints.add("ignore")
    if reply_policy == "reply" and reply_target_candidate is not None and str(reply_target_candidate) != str(row.get("focus_message_id")):
        hints.add("reply_target")
    window = row.get("window") or []
    if not window:
        return tuple(sorted(hints))
    focus_message = window[-1]
    focus_sender_id = str(focus_message.get("sender_id")) if focus_message.get("sender_id") is not None else None
    prior_messages = window[:-1]
    prior_sender_ids = {
        str(item.get("sender_id"))
        for item in prior_messages
        if item.get("sender_id") is not None and not item.get("is_amber")
    }
    if focus_sender_id is not None and focus_sender_id in prior_sender_ids:
        hints.add("engaged_user_continuation")
    window_ids = {int(item["message_id"]) for item in window if item.get("message_id") is not None}
    focus_reply_to_message_id = focus_message.get("reply_to_message_id")
    if focus_reply_to_message_id is not None and int(focus_reply_to_message_id) != int(row.get("focus_message_id")):
        hints.add("reply_target")
    if (
        focus_sender_id is not None
        and focus_sender_id in prior_sender_ids
        and focus_reply_to_message_id is not None
        and int(focus_reply_to_message_id) not in window_ids
    ):
        hints.add("engaged_user_expansion")
    return tuple(sorted(hints))


def select_segment_window(row: dict[str, Any]) -> list[dict[str, Any]]:
    window = row.get("window") or []
    focus_message_id = int(row["focus_message_id"])
    for index, item in enumerate(window):
        if int(item["message_id"]) == focus_message_id:
            return window[: index + 1]
    return window


def load_candidate_segments() -> list[DatasetSegment]:
    segments: list[DatasetSegment] = []
    seen_sample_ids: set[str] = set()
    for path in RANDOM_SEGMENT_PATHS:
        for row in load_jsonl(path):
            sample_id = row.get("sample_id")
            if not sample_id or sample_id in seen_sample_ids:
                continue
            if row.get("sample_type") != "candidate_message":
                continue
            window = select_segment_window(row)
            if not window:
                continue
            target = row.get("target") or {}
            reply_target_candidate = target.get("reply_target_candidate") or row.get("target_reply_target_candidate")
            segments.append(
                DatasetSegment(
                    sample_id=sample_id,
                    source_path=str(path.relative_to(ROOT)),
                    split_key=str(row.get("split_key") or ""),
                    focus_message_id=int(row["focus_message_id"]),
                    focus_sender=str(row.get("focus_sender") or "unknown"),
                    label_reply_policy=target.get("reply_policy") or row.get("target_reply_policy"),
                    label_reply_target_candidate=int(reply_target_candidate) if reply_target_candidate is not None else None,
                    route_hints=build_route_hints({**row, "window": window}),
                    window=window,
                )
            )
            seen_sample_ids.add(sample_id)
    return segments


def build_test_settings(tmp_path: Path):
    from src.config.config import get_settings

    settings = get_settings()
    return settings.model_copy(
        update={
            "memories_dir": tmp_path / "memories",
            "runtime_state_path": tmp_path / "runtime_state" / "global_state.json",
            "context_debounce_seconds": 0.0,
            "context_idle_timeout_seconds": 2.0,
            "context_competing_chat_timeout_seconds": 1.0,
            "attention_surface_threshold": 0.0,
            "attention_urgent_threshold": 0.6,
            "enable_real_delays": False,
        }
    )


def ensure_live_openai_settings(settings) -> None:
    if not settings.ai_api_key:
        pytest.skip("Real OpenAI integration requires AMBER_BLUE_AI_API_KEY.")
    if settings.ai_provider.lower() != "openai":
        pytest.skip("This integration batch expects the OpenAI AI provider.")


def event_from_message_dict(message: dict[str, Any]) -> TelegramMessageReceivedEvent:
    sender_name = str(message.get("sender_name") or "unknown")
    content = str(message.get("content") or "[no text content]")
    payload = TelegramMessagePayload(
        message_id=int(message["message_id"]),
        chat_id=TEST_CHAT_ID,
        sender=TelegramSenderPayload(
            id=str(message.get("sender_id") or "unknown"),
            name=sender_name,
            username=None,
            is_self=bool(message.get("is_amber")),
        ),
        timestamp=parse_iso_datetime(message["date"]),
        content=content,
        raw_text=message.get("raw_text"),
        reply_to_message_id=message.get("reply_to_message_id"),
        reply_to_sender=TelegramReplySenderPayload(
            id=str(message["reply_to_sender_id"]) if message.get("reply_to_sender_id") is not None else None,
            name=message.get("reply_to_sender_name"),
        ),
        mentions=["amber"] if message.get("mentions_amber") else [],
        attachment=TelegramAttachmentPayload(
            media_type=message.get("media_type"),
            file_id=message.get("file_name"),
            file_name=message.get("file_name"),
            mime_type=message.get("mime_type"),
            sticker_set_id=None,
        ),
        transport=TelegramTransportPayload(
            peer_id=TEST_CHAT_ID,
            raw_chat_id=TEST_CHAT_ID,
            raw_message_id=int(message["message_id"]),
        ),
        edited_at=parse_iso_datetime(message.get("edited")),
        reaction_count=int(message.get("reaction_count") or 0),
    )
    return TelegramMessageReceivedEvent(chat_id=TEST_CHAT_ID, payload=payload)


def emit_messages(app, messages: list[dict[str, Any]]) -> None:
    for message in messages:
        event = event_from_message_dict(message)
        app.message_archive.put(event.payload)
        with emitter_context("receiver.fixture"):
            EventBus.emit(event)


def capture_signature(capture: PipelineCapture, app) -> tuple[Any, ...]:
    return (
        len(capture.attention),
        len(capture.frames),
        len(capture.semantic),
        len(capture.outbound),
        len(capture.delivery),
        len(getattr(app.transport, "records", [])),
        tuple(item.payload.action for item in capture.semantic[-3:]),
        tuple(item.payload.no_send for item in capture.delivery[-3:]),
    )


def drain_pipeline(capture: PipelineCapture, app, timeout_seconds: float = 25.0, downstream_grace_seconds: float = 8.0) -> None:
    deadline = time.time() + timeout_seconds
    previous: tuple[Any, ...] | None = None
    stable_polls = 0
    last_change_at = time.time()
    time.sleep(0.5)
    while time.time() < deadline:
        current = capture_signature(capture, app)
        if current == previous:
            stable_polls += 1
        else:
            previous = current
            stable_polls = 0
            last_change_at = time.time()
        surfaced_attention = any(item.payload.decision in {"surface", "surface_urgent"} for item in capture.attention)
        downstream_started = bool(capture.semantic or capture.outbound or capture.delivery)
        if surfaced_attention and not downstream_started and (time.time() - last_change_at) < downstream_grace_seconds:
            time.sleep(0.5)
            continue
        if stable_polls >= 3:
            return
        time.sleep(0.5)
    raise RuntimeError("Pipeline did not settle before timeout.")


def summarize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "message_id": int(item["message_id"]),
            "sender_name": str(item.get("sender_name") or "unknown"),
            "reply_to_message_id": item.get("reply_to_message_id"),
            "content": str(item.get("content") or "[no text content]"),
        }
        for item in messages
    ]


def detect_observed_routes(capture: PipelineCapture) -> set[str]:
    observed: set[str] = set()
    if any(not item.payload.no_send and item.payload.ordered_messages for item in capture.delivery):
        observed.add("reply")
    if any(item.payload.action == "ignore" for item in capture.semantic) or any(item.payload.no_send for item in capture.delivery):
        observed.add("ignore")
    reply_sessions = {
        (item.payload.session_id, item.payload.trigger_message_id)
        for item in capture.semantic
        if item.payload.action == "reply"
    } | {
        (item.payload.session_id, item.payload.trigger_message_id)
        for item in capture.delivery
        if not item.payload.no_send
    }
    semantic_targets = {
        (item.payload.session_id, item.payload.trigger_message_id, item.payload.reply_to_message_id)
        for item in capture.semantic
        if item.payload.action == "reply"
        and item.payload.reply_to_message_id is not None
        and item.payload.reply_to_message_id != item.payload.trigger_message_id
    }
    delivery_targets = {
        (item.payload.session_id, item.payload.trigger_message_id, item.payload.reply_to_message_id)
        for item in capture.delivery
        if not item.payload.no_send
        and item.payload.reply_to_message_id is not None
        and item.payload.reply_to_message_id != item.payload.trigger_message_id
    }
    structural_reply_targets = {
        (frame.payload.session_id, frame.payload.trigger_message_id, frame.payload.recommended_reply_candidate)
        for frame in capture.frames
        if frame.payload.recommended_reply_candidate is not None
        and frame.payload.recommended_reply_candidate != frame.payload.trigger_message_id
    }
    if semantic_targets & delivery_targets or any(
        (session_id, trigger_message_id) in reply_sessions
        for session_id, trigger_message_id, _ in structural_reply_targets
    ):
        observed.add("reply_target")
    if any(item.payload.engaged_user_bypass for item in capture.attention):
        observed.add("engaged_user_continuation")
    if any(message.source == "injected_reply_context" for frame in capture.frames for message in frame.payload.recent_messages):
        observed.add("engaged_user_expansion")
    return observed


def build_segment_payload(
    *,
    segment: DatasetSegment,
    capture: PipelineCapture,
    app,
    observed_routes: set[str],
) -> dict[str, Any]:
    return {
        "pass": True,
        "fixture_id": segment.sample_id,
        "source_path": segment.source_path,
        "split_key": segment.split_key,
        "focus_message_id": segment.focus_message_id,
        "focus_sender": segment.focus_sender,
        "route_hints": list(segment.route_hints),
        "observed_routes": sorted(observed_routes),
        "label_reply_policy": segment.label_reply_policy,
        "label_reply_target_candidate": segment.label_reply_target_candidate,
        "input_summary": summarize_messages(segment.window),
        "attention_payloads": [item.payload.model_dump(mode="json") for item in capture.attention],
        "context_payloads": [item.payload.model_dump(mode="json") for item in capture.frames],
        "semantic_payloads": [item.payload.model_dump(mode="json") for item in capture.semantic],
        "outbound_payloads": [item.payload.model_dump(mode="json") for item in capture.outbound],
        "delivery_payloads": [item.payload.model_dump(mode="json") for item in capture.delivery],
        "transport_records": [asdict(item) for item in getattr(app.transport, "records", [])],
        "final_state": app.state_store.snapshot().model_dump(mode="json"),
    }


def sanitize_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")[:120] or "segment"


def write_segment_output(run_dir: Path, index: int, payload: dict[str, Any]) -> None:
    segment_slug = sanitize_slug(payload["fixture_id"])
    base_name = f"{index:02d}_{segment_slug}"
    json_path = run_dir / f"{base_name}.json"
    md_path = run_dir / f"{base_name}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        f"# Segment {index:02d}",
        "",
        f"- fixture: {payload['fixture_id']}",
        f"- source: {payload['source_path']}",
        f"- focus: {payload['focus_message_id']} ({payload['focus_sender']})",
        f"- label reply policy: {payload['label_reply_policy']}",
        f"- route hints: {payload['route_hints']}",
        f"- observed routes: {payload['observed_routes']}",
        "",
        "## Input summary",
    ]
    for item in payload["input_summary"]:
        lines.append(f"- [{item['message_id']}] {item['sender_name']} -> {item['reply_to_message_id']} | {item['content']}")
    lines.extend(
        [
            "",
            "## Attention payloads",
            "```json",
            json.dumps(payload["attention_payloads"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## Context payloads",
            "```json",
            json.dumps(payload["context_payloads"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## Semantic payloads",
            "```json",
            json.dumps(payload["semantic_payloads"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## Outbound payloads",
            "```json",
            json.dumps(payload["outbound_payloads"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## Delivery payloads",
            "```json",
            json.dumps(payload["delivery_payloads"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## Transport records",
            "```json",
            json.dumps(payload["transport_records"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## Final state",
            "```json",
            json.dumps(payload["final_state"], ensure_ascii=False, indent=2),
            "```",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_summary_output(run_dir: Path, payload: dict[str, Any]) -> None:
    json_path = run_dir / "summary.json"
    md_path = run_dir / "summary.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Random Unseen Batch",
        "",
        f"- seed: {payload['seed']}",
        f"- min segments: {payload['min_segments']}",
        f"- max segments: {payload['max_segments']}",
        f"- tested segments: {payload['tested_segments']}",
        f"- candidate pool size: {payload['candidate_pool_size']}",
        f"- required routes: {payload['required_routes']}",
        f"- covered routes: {payload['covered_routes']}",
        f"- missing routes: {payload['missing_routes']}",
        "",
        "## Segment summary",
    ]
    for item in payload["segments"]:
        lines.append(
            f"- [{item['index']:02d}] {item['fixture_id']} | hints={item['route_hints']} | observed={item['observed_routes']} | source={item['source_path']}"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def choose_next_segment(
    remaining: list[DatasetSegment],
    *,
    covered_routes: set[str],
    tested_segments: int,
    rng: random.Random,
) -> DatasetSegment | None:
    if not remaining:
        return None
    if tested_segments < MIN_SEGMENTS:
        return remaining.pop(0)
    missing_routes = sorted(REQUIRED_ROUTES - covered_routes)
    if not missing_routes:
        return None
    route_candidate_counts = {
        route: sum(1 for segment in remaining if route in segment.route_hints)
        for route in missing_routes
    }
    available_routes = {route: count for route, count in route_candidate_counts.items() if count > 0}
    if available_routes:
        smallest_pool = min(available_routes.values())
        rarest_routes = [route for route, count in available_routes.items() if count == smallest_pool]
        preferred_route = rng.choice(sorted(rarest_routes))
    else:
        preferred_route = rng.choice(missing_routes)
    for index, segment in enumerate(remaining):
        if preferred_route in segment.route_hints:
            return remaining.pop(index)
    return remaining.pop(0)


@pytest.mark.integration
def test_orchestration_v1_live_random_batch(tmp_path: Path) -> None:
    candidate_segments = load_candidate_segments()
    assert candidate_segments, "expected at least one candidate segment fixture"
    seed = int(os.getenv("AMBER_BLUE_TEST_SEED", str(int(time.time()))))
    rng = random.Random(seed)
    remaining = list(candidate_segments)
    rng.shuffle(remaining)

    run_id = f"live_random_batch_{seed}_{int(time.time())}"
    run_dir = TEST_OUTPUT_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    base_settings = build_test_settings(tmp_path / "base")
    ensure_live_openai_settings(base_settings)

    covered_routes: set[str] = set()
    segment_summaries: list[dict[str, Any]] = []
    tested_segments = 0

    while tested_segments < MAX_SEGMENTS and (tested_segments < MIN_SEGMENTS or covered_routes != REQUIRED_ROUTES):
        segment = choose_next_segment(
            remaining,
            covered_routes=covered_routes,
            tested_segments=tested_segments,
            rng=rng,
        )
        if segment is None:
            break
        tested_segments += 1
        segment_settings = build_test_settings(tmp_path / f"segment_{tested_segments:02d}")
        app = build_application(settings=segment_settings)
        capture = PipelineCapture()
        emit_messages(app, segment.window)
        drain_pipeline(capture, app)
        observed_routes = detect_observed_routes(capture)
        covered_routes |= observed_routes
        payload = build_segment_payload(segment=segment, capture=capture, app=app, observed_routes=observed_routes)
        write_segment_output(run_dir, tested_segments, payload)
        app.scheduler.shutdown()
        segment_summaries.append(
            {
                "index": tested_segments,
                "fixture_id": segment.sample_id,
                "source_path": segment.source_path,
                "route_hints": list(segment.route_hints),
                "observed_routes": sorted(observed_routes),
            }
        )

    summary_payload = {
        "seed": seed,
        "min_segments": MIN_SEGMENTS,
        "max_segments": MAX_SEGMENTS,
        "tested_segments": tested_segments,
        "candidate_pool_size": len(candidate_segments),
        "required_routes": sorted(REQUIRED_ROUTES),
        "covered_routes": sorted(covered_routes),
        "missing_routes": sorted(REQUIRED_ROUTES - covered_routes),
        "segments": segment_summaries,
    }
    write_summary_output(run_dir, summary_payload)

    assert tested_segments >= MIN_SEGMENTS, f"expected at least {MIN_SEGMENTS} random unseen segments"
    missing_routes = REQUIRED_ROUTES - covered_routes
    assert not missing_routes, (
        f"missing route coverage after {tested_segments} random unseen segments: {sorted(missing_routes)}. "
        f"Review {run_dir}/summary.md and per-segment artifacts."
    )
