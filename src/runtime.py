from __future__ import annotations

import asyncio
import importlib
import os
from dataclasses import dataclass

from telethon import TelegramClient

from src.action.config import ActionConfig
from src.action.telegram.layer import ActionLayer
from src.action.telegram.transport import RecordingTransport, TelegramTransport
from src.adapters.codex import build_codex_adapter
from src.adapters.linear import LinearAdapter
from src.adapters.registry import AdapterRegistry
from src.ai.config import AIConfig
from src.ai.semantic.client import SemanticClient, SemanticModelClient
from src.ai.semantic.config import SemanticConfig
from src.ai.semantic.layer import AILayer
from src.attention.config import AttentionConfig
from src.attention.memory.store import MemoryStore
from src.attention.pipeline import AttentionLayer
from src.config.config import Settings, get_settings
from src.context.config import ContextConfig
from src.context.pipeline import ContextLayer
from src.events.bus import EventBus
from src.outbound.config import OutboundPreparationConfig
from src.outbound.layer import OutboundPreparationLayer
from src.providers.gateway import ModelProviderGateway
from src.adapters.linear import LinearGraphQLClient
from src.receiver.codex.receiver import CodexReceiver
from src.receiver.linear.config import LinearReceiverConfig
from src.receiver.linear.receiver import LinearReceiver
from src.receiver.telegram.config import TelegramReceiverConfig
from src.receiver.telegram.receiver import TelegramReceiver
from src.state.store import GlobalStateStore
from src.adapters.codex import CodexTaskLifecycleHandler
from src.utils.logging import configure_logging
from src.utils.message_archive import MessageArchive
from src.utils.scheduler import RuntimeScheduler


@dataclass
class AmberApplication:
    settings: Settings
    state_store: GlobalStateStore
    message_archive: MessageArchive
    scheduler: RuntimeScheduler
    memory_store: MemoryStore
    adapter_registry: AdapterRegistry
    attention_layer: AttentionLayer
    context_layer: ContextLayer
    ai_layer: AILayer
    outbound_preparation_layer: OutboundPreparationLayer
    action_layer: ActionLayer
    transport: object
    receiver: TelegramReceiver | None = None
    codex_receiver: CodexReceiver | None = None
    codex_task_lifecycle_handler: CodexTaskLifecycleHandler | None = None
    linear_receiver: LinearReceiver | None = None
    telegram_client: TelegramClient | None = None

    async def run_telegram_forever(self) -> None:
        if self.telegram_client is None or self.receiver is None:
            raise RuntimeError("Telegram runtime was not configured.")
        if self.codex_receiver is not None:
            self.codex_receiver.register()
        if self.codex_task_lifecycle_handler is not None:
            self.codex_task_lifecycle_handler.register()
        if self.linear_receiver is not None:
            self.linear_receiver.register()
        self.receiver.register()
        await self.telegram_client.start()
        await self.receiver.replay_open_question_backlog()
        self.action_layer.sync_presence_from_state()
        await self.telegram_client.run_until_disconnected()


def build_application(
    *,
    settings: Settings | None = None,
    semantic_client: SemanticClient | None = None,
    attention_scorer: object | None = None,
    transport: object | None = None,
    enable_telegram: bool = False,
) -> AmberApplication:
    EventBus.reset_for_tests()
    settings = settings or get_settings()
    configure_logging(log_dir=settings.log_dir, timezone_name=settings.timezone_name)
    state_store = GlobalStateStore(settings.runtime_state_path, settings.timezone_name)
    message_archive = MessageArchive.instance()
    message_archive.reset()
    scheduler = RuntimeScheduler.instance()
    scheduler.shutdown()
    memory_store = MemoryStore(settings.memories_dir)
    adapter_registry = AdapterRegistry()
    codex_adapter = build_codex_adapter(settings)
    if settings.mode == "work":
        codex_adapter.ensure_app_server()
    adapter_registry.register(codex_adapter)
    linear_adapter = LinearAdapter(
        api_key=settings.linear_api_key,
        api_url=settings.linear_api_url,
        status_in_progress=settings.linear_status_in_progress,
        status_under_review=settings.linear_status_under_review,
        status_completed=settings.linear_status_completed,
    )
    adapter_registry.register(linear_adapter)
    codex_receiver = CodexReceiver(codex_adapter, memory_store, settings.always_surface_telegram_ids)
    codex_task_lifecycle_handler = CodexTaskLifecycleHandler(
        codex_adapter,
        adapter_registry=adapter_registry,
        state_store=state_store,
    )
    linear_receiver = None
    if settings.mode == "work" and settings.linear_enabled:
        linear_config = LinearReceiverConfig.from_settings(settings)
        if not linear_config.api_key:
            raise RuntimeError("AMBER_LINEAR_ENABLED=1 requires AMBER_LINEAR_API_KEY.")
        linear_receiver = LinearReceiver(
            client=LinearGraphQLClient(api_key=linear_config.api_key, api_url=linear_config.api_url),
            state_store=state_store,
            timezone_name=settings.timezone_name,
            poll_seconds=linear_config.poll_seconds,
            due_window_days=linear_config.due_window_days,
        )
    scorer = _build_attention_scorer(attention_scorer, mode=settings.attention_scorer)
    receiver = None
    telegram_client = None
    if transport is None:
        if enable_telegram:
            telegram_config = TelegramReceiverConfig.from_settings(settings)
            if not telegram_config.api_id or not telegram_config.api_hash:
                raise RuntimeError("Missing Telegram API credentials.")
            loop = asyncio.get_event_loop()
            telegram_client = TelegramClient(str(telegram_config.session_path), int(telegram_config.api_id), telegram_config.api_hash, loop=loop)
            transport = TelegramTransport(telegram_client, loop)
            receiver = TelegramReceiver(telegram_client, message_archive, state_store, transport)
        else:
            transport = RecordingTransport()
    semantic_config = SemanticConfig.from_settings(
        settings,
        memory_store=memory_store,
        adapter_registry=adapter_registry,
        state_store=state_store,
        telegram_transport=transport,
        codex_workspace=settings.codex_workdir,
    )
    semantic_client = semantic_client or SemanticModelClient(semantic_config, ModelProviderGateway(semantic_config).provider)
    attention_layer = AttentionLayer(AttentionConfig.from_settings(settings), scorer, state_store, memory_store, message_archive)
    context_layer = ContextLayer(
        ContextConfig.from_settings(settings),
        state_store,
        scheduler,
        message_archive,
        memory_store,
        settings.timezone_name,
        adapter_registry=adapter_registry,
    )
    ai_layer = AILayer(AIConfig.from_settings(settings), semantic_client)
    outbound_preparation_layer = OutboundPreparationLayer(OutboundPreparationConfig.from_settings(settings), state_store)
    action_layer = ActionLayer(
        ActionConfig.from_settings(settings),
        transport,
        state_store,
        scheduler,
        message_archive,
        settings.timezone_name,
    )
    return AmberApplication(
        settings=settings,
        state_store=state_store,
        message_archive=message_archive,
        scheduler=scheduler,
        memory_store=memory_store,
        adapter_registry=adapter_registry,
        attention_layer=attention_layer,
        context_layer=context_layer,
        ai_layer=ai_layer,
        outbound_preparation_layer=outbound_preparation_layer,
        action_layer=action_layer,
        transport=transport,
        receiver=receiver,
        codex_receiver=codex_receiver,
        codex_task_lifecycle_handler=codex_task_lifecycle_handler,
        linear_receiver=linear_receiver,
        telegram_client=telegram_client,
    )


def _build_attention_scorer(override: object | None = None, *, mode: str | None = None) -> object | None:
    if override is not None:
        return override
    scorer_mode = (mode if mode is not None else os.getenv("AMBER_ATTENTION_SCORER", "heuristic")).strip().lower()
    if scorer_mode in {"", "heuristic", "heuristics", "none", "off"}:
        return None
    if scorer_mode not in {"modernbert", "zero-shot", "zero_shot", "local-ml", "local_ml"}:
        raise RuntimeError("AMBER_ATTENTION_SCORER must be heuristic or modernbert.")
    try:
        module = importlib.import_module("src.attention.scoring.zero_shot")
        scorer_cls = getattr(module, "AttentionPolicyScorer")
        return scorer_cls()
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeError(
            "AMBER_ATTENTION_SCORER=modernbert requires optional ML dependencies. "
            "Install them with `pip install -r requirements-ml.txt`, or use the default heuristic scorer."
        ) from exc
