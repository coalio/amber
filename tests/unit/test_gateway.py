from __future__ import annotations

import asyncio
import stat
import threading
from types import SimpleNamespace

import pytest

from src.action.config import ActionConfig
from src.action.telegram.layer import ActionLayer
from src.action.telegram.transport import RecordingTransport
from src.adapters.codex import app_server as codex_app_server
from src.adapters.codex import CodexNotification
from src.receiver.codex.receiver import CodexReceiver
from src.ai.config import AIConfig
from src.ai.semantic.layer import AILayer
from src.ai.semantic.schema import SemanticDecisionSchema
from src.attention.config import AttentionConfig
from src.attention.memory.store import MemoryStore
from src.attention.pipeline import AttentionLayer
from src.context.config import ContextConfig
from src.context.pipeline import ContextLayer
from src.events.bus import EventBus
from src.gateway.cli import request_gateway
from src.gateway.server import GatewayServer
from src.gateway.store import GatewayStore
from src.gateway.transport import GatewayTransport
from src.outbound.config import OutboundPreparationConfig
from src.outbound.layer import OutboundPreparationLayer
from src.receiver.telegram.receiver import TelegramReceiver
from src.state.store import GlobalStateStore
from src.utils.message_archive import MessageArchive
from src.utils.scheduler import RuntimeScheduler


@pytest.fixture(autouse=True)
def reset_runtime():
    EventBus.reset_for_tests()
    RuntimeScheduler.instance().shutdown()
    MessageArchive.instance().reset()
    yield
    RuntimeScheduler.instance().shutdown()
    EventBus.reset_for_tests()
    MessageArchive.instance().reset()


def test_gateway_runs_burst_and_typing_through_real_pipeline(tmp_path):
    store = GatewayStore(tmp_path / "captures", ["1001001001"], {"1001001001": "Fixture Admin"})
    delegate = RecordingTransport()
    transport = GatewayTransport(delegate, store)
    archive = MessageArchive.instance()
    state = GlobalStateStore(tmp_path / "state.json", "UTC")
    scheduler = RuntimeScheduler.instance()
    memory = MemoryStore(tmp_path / "memories")
    receiver = TelegramReceiver(object(), archive, state, transport)
    AttentionLayer(AttentionConfig(
        surface_threshold=0.48, urgent_threshold=0.72, memory_limit=3, disable_sleep_state=True,
        mode="work", always_surface_telegram_ids=frozenset({"1001001001"}),
    ), None, state, memory, archive)
    ContextLayer(ContextConfig(
        debounce_seconds=0.05, idle_timeout_seconds=0.0, competing_chat_timeout_seconds=15,
        recent_message_budget=8, max_compacted_facts=6, disable_sleep_state=True,
        initial_engagement_delay_min_seconds=0, initial_engagement_delay_max_seconds=0,
    ), state, scheduler, archive, memory, "UTC")
    store.register(SimpleNamespace(subscribe_task_completed=lambda callback: None))
    frames = []
    done = threading.Event()

    class Client:
        def decide(self, frame, **kwargs):
            frames.append(frame)
            return SemanticDecisionSchema(action="reply", chat_id=frame.chat_id, reply_text="received both", confidence=1)

    AILayer(AIConfig(semantic_retry_budget=0, max_reply_chars=1600), Client())
    OutboundPreparationLayer(OutboundPreparationConfig(max_chunk_chars=500), state)
    ActionLayer(ActionConfig(
        enable_real_delays=False, disable_sleep_state=True, transport_max_retries=1, transport_retry_delay_seconds=0,
    ), transport, state, scheduler, archive, "UTC")
    EventBus.subscribe("OutboundMessageSentEvent", lambda event: done.set())
    server = GatewayServer(tmp_path / "gateway.sock", receiver, store)

    async def scenario():
        await server.start()
        try:
            assert stat.S_IMODE(server.socket_path.stat().st_mode) == 0o600
            response = await request_gateway(server.socket_path, {
                "action": "send", "sender": "1001001001", "messages": ["inspect this", "include the comments"],
                "typing_seconds": 0.15,
            })
            assert await asyncio.to_thread(done.wait, 3)
            return store.read(response["session"])
        finally:
            await server.close()

    rows = asyncio.run(scenario())
    assert len(frames) == 1
    assert frames[0].current_message.sender_name == "Fixture Admin"
    assert len(frames[0].visible_surfaced_message_ids) == 2
    assert [item["message"] for item in rows if item["event"] == "reply"] == ["received both"]
    assert delegate.records == []
    assert delegate.read_records == []


def test_gateway_enforces_admin_and_session_ownership(tmp_path):
    store = GatewayStore(tmp_path / "captures", ["1001001001", "1001001002"])
    session = store.create("1001001001")
    server = GatewayServer(tmp_path / "gateway.sock", object(), store)
    for sender in ("999", "1001001002"):
        with pytest.raises(RuntimeError):
            asyncio.run(server._request({"action": "send", "sender": sender, "session": session, "messages": ["hello"]}))
    with pytest.raises(RuntimeError, match="Invalid gateway session"):
        store.read("../private")
    assert store.candidates({"amber_gateway_chat_id": session})[0]["chat_id"] == session
    assert store.candidates({}) is None


def test_gateway_socket_cannot_replace_running_owner(tmp_path):
    store = GatewayStore(tmp_path / "captures", ["1001001001"])

    async def scenario():
        first = GatewayServer(tmp_path / "gateway.sock", object(), store)
        second = GatewayServer(tmp_path / "gateway.sock", object(), store)
        await first.start()
        try:
            with pytest.raises(RuntimeError, match="Another runtime"):
                await second.start()
            assert first.socket_path.exists()
        finally:
            await first.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("worker_context", [{}, {"amber_gateway_chat_id": "forged", "detail": "complete"}])
def test_worker_notifications_preserve_gateway_delivery_route(tmp_path, worker_context):
    store = GatewayStore(tmp_path / "captures", ["1001001001"])
    session = store.create("1001001001")
    runner = codex_app_server.CodexTaskRunner("task_fixture", {"context": {"amber_gateway_chat_id": session}})
    receiver = CodexReceiver(object(), MemoryStore(tmp_path / "memories"), ["1001001001"], store.candidates)
    events = []
    EventBus.subscribe("CodexNotificationReceivedEvent", events.append)
    codex_app_server.EVENTS.clear()
    try:
        runner._append_user_notification(message="inspection complete", notification_kind="completion", context=worker_context)
        emitted = codex_app_server.EVENTS[-1]
        receiver._handle_notification(CodexNotification(
            app_server_id="fixture", task_id="task_fixture", notification_id="notification_fixture",
            notification_kind="completion", message=emitted["message"], task_description="inspect",
            context=emitted["context"],
        ))
        assert events[0].payload.candidate_people[0].chat_id == session
    finally:
        codex_app_server.EVENTS.clear()
