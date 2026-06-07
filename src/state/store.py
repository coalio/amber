from __future__ import annotations

import hashlib
import json
from datetime import datetime, time
from pathlib import Path
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

from src.state.models import (
    ConversationIgnoreRule,
    GlobalState,
    LinearQueuedTask,
    OpenQuestion,
    OpenQuestionCandidate,
    PendingInterruption,
)
from src.utils.files import read_json, write_json
from src.utils.time import local_now, utc_now

_UNSET = object()
_LINEAR_BUSY_STATUSES = {"codex_running", "waiting_for_user", "under_review"}
_LINEAR_TERMINAL_STATUSES = {"completed", "skipped", "error"}


class GlobalStateStore:
    def __init__(self, path: Path, timezone_name: str) -> None:
        self._path = path
        self._timezone_name = timezone_name
        self._lock = Lock()
        self._state = self._load()

    def _default_state(self) -> GlobalState:
        now_local = local_now(self._timezone_name)
        default_woke_at = datetime.combine(now_local.date(), time(hour=8), tzinfo=ZoneInfo(self._timezone_name))
        return GlobalState(woke_at=default_woke_at.astimezone(now_local.tzinfo))

    def _load(self) -> GlobalState:
        payload = read_json(self._path, None)
        if payload is None:
            state = self._default_state()
            self._persist(state)
            return state
        return GlobalState.model_validate(payload)

    def _persist(self, state: GlobalState) -> None:
        write_json(self._path, state.model_dump(mode="json"))

    def snapshot(self) -> GlobalState:
        with self._lock:
            return self._state.model_copy(deep=True)

    def update_attention_state(self, *, mood: str | None = None) -> GlobalState:
        with self._lock:
            if mood is not None:
                self._state.mood = mood
            self._persist(self._state)
            return self._state.model_copy(deep=True)

    def update_context_state(
        self,
        *,
        active_chat_id: int | str | None | object = _UNSET,
        active_session_id: str | None | object = _UNSET,
        pending_chat_id: int | str | None | object = _UNSET,
        pending_session_id: str | None | object = _UNSET,
        last_engagement_at: datetime | None | object = _UNSET,
        conversation_engaged_user_ids: list[str] | None | object = _UNSET,
        pending_engaged_user_ids: list[str] | None | object = _UNSET,
    ) -> GlobalState:
        with self._lock:
            if active_chat_id is not _UNSET:
                self._state.active_chat_id = active_chat_id
            if active_session_id is not _UNSET:
                self._state.active_session_id = active_session_id
            if pending_chat_id is not _UNSET:
                self._state.pending_chat_id = pending_chat_id
            if pending_session_id is not _UNSET:
                self._state.pending_session_id = pending_session_id
            if last_engagement_at is not _UNSET:
                self._state.last_engagement_at = last_engagement_at
            if conversation_engaged_user_ids is not _UNSET:
                self._state.conversation_engaged_user_ids = conversation_engaged_user_ids
            if pending_engaged_user_ids is not _UNSET:
                self._state.pending_engaged_user_ids = pending_engaged_user_ids
            self._persist(self._state)
            return self._state.model_copy(deep=True)

    def update_action_state(self, **fields: Any) -> GlobalState:
        with self._lock:
            for key, value in fields.items():
                setattr(self._state, key, value)
            self._persist(self._state)
            return self._state.model_copy(deep=True)

    def mark_seen(self, chat_id: int | str, read_through_message_id: int) -> int:
        chat_key = str(chat_id)
        with self._lock:
            previous = self._state.seen_through_by_chat.get(chat_key, 0)
            updated = max(previous, read_through_message_id)
            if updated != previous:
                self._state.seen_through_by_chat[chat_key] = updated
                self._persist(self._state)
            return updated

    def remember_conversation_ignore(
        self,
        *,
        chat_id: int | str,
        sender_id: str,
        sender_name: str | None,
        created_at: datetime,
        ignore_until: datetime | None,
        reason: str | None,
    ) -> ConversationIgnoreRule:
        rule = ConversationIgnoreRule(
            chat_id=chat_id,
            sender_id=sender_id,
            sender_name=sender_name,
            created_at=created_at,
            ignore_until=ignore_until,
            reason=reason,
        )
        with self._lock:
            self._state.conversation_ignore_rules[f"{chat_id}:{sender_id}"] = rule
            self._persist(self._state)
            return rule.model_copy(deep=True)

    def remember_self_message(self, message_id: int) -> None:
        with self._lock:
            ids = [item for item in self._state.recent_self_message_ids if item != message_id]
            ids.append(message_id)
            self._state.recent_self_message_ids = ids[-20:]
            self._persist(self._state)

    def touch_delivery_state(self, payload: dict[str, str | int | float | bool | None]) -> None:
        with self._lock:
            self._state.delivery_state.update(payload)
            self._persist(self._state)

    def remember_pending_interruption(
        self,
        *,
        chat_id: int | str,
        session_id: str | None,
        original_trigger_message_id: int | None,
        original_reply_to_message_id: int | None,
        interrupting_message_id: int,
        reply_target_sender_id: str,
        reply_target_sender_name: str | None,
        sent_reply_chunks: list[str],
        remaining_reply_chunks: list[str],
        created_at: datetime,
    ) -> PendingInterruption:
        interruption = PendingInterruption(
            chat_id=chat_id,
            session_id=session_id,
            original_trigger_message_id=original_trigger_message_id,
            original_reply_to_message_id=original_reply_to_message_id,
            interrupting_message_id=interrupting_message_id,
            reply_target_sender_id=reply_target_sender_id,
            reply_target_sender_name=reply_target_sender_name,
            sent_reply_chunks=list(sent_reply_chunks),
            remaining_reply_chunks=list(remaining_reply_chunks),
            created_at=created_at,
        )
        with self._lock:
            self._state.pending_interruption = interruption
            self._persist(self._state)
            return interruption.model_copy(deep=True)

    def take_pending_interruption(
        self,
        *,
        chat_id: int | str,
        session_id: str | None,
    ) -> PendingInterruption | None:
        with self._lock:
            interruption = self._state.pending_interruption
            if interruption is None:
                return None
            if str(interruption.chat_id) != str(chat_id):
                return None
            if session_id is not None and interruption.session_id is not None and interruption.session_id != session_id:
                return None
            result = interruption.model_copy(deep=True)
            self._state.pending_interruption = None
            self._persist(self._state)
            return result

    def clear_pending_interruption(self) -> None:
        with self._lock:
            if self._state.pending_interruption is None:
                return
            self._state.pending_interruption = None
            self._persist(self._state)

    def remember_open_question(
        self,
        *,
        chat_id: int | str,
        sender_id: str,
        sender_name: str,
        app_server_id: str,
        task_id: str,
        tool_call_id: str,
        questions: list[str],
        task_description: str,
        context: dict[str, str | int | float | bool | None],
        candidate_people: list[OpenQuestionCandidate],
        created_at: datetime,
        expires_at: datetime,
    ) -> OpenQuestion:
        question = OpenQuestion(
            chat_id=chat_id,
            sender_id=sender_id,
            sender_name=sender_name,
            app_server_id=app_server_id,
            task_id=task_id,
            tool_call_id=tool_call_id,
            questions=list(questions),
            task_description=task_description,
            context=dict(context),
            candidate_people=list(candidate_people),
            created_at=created_at,
            expires_at=expires_at,
        )
        with self._lock:
            self._state.open_questions[_codex_question_key(app_server_id, task_id, tool_call_id)] = question
            self._persist(self._state)
            return question.model_copy(deep=True)

    def append_open_question_reply(
        self,
        chat_id: int | str,
        content: str,
        *,
        message_id: int | None = None,
        sender_id: str | None = None,
    ) -> OpenQuestion | None:
        updated = self.append_open_question_replies(
            chat_id=chat_id,
            sender_id=sender_id,
            content=content,
            message_id=message_id,
        )
        return updated[0] if updated else None

    def append_open_question_replies(
        self,
        *,
        chat_id: int | str,
        sender_id: str | None,
        content: str,
        message_id: int | None = None,
    ) -> list[OpenQuestion]:
        with self._lock:
            matches = [
                question
                for question in self._state.open_questions.values()
                if str(question.chat_id) == str(chat_id)
                and (sender_id is None or str(question.sender_id) == str(sender_id))
            ]
            if not matches:
                return []
            changed = False
            for question in matches:
                if message_id is not None and message_id not in question.user_reply_message_ids:
                    question.user_reply_message_ids.append(message_id)
                    changed = True
                if content not in question.user_replies:
                    question.user_replies.append(content)
                    changed = True
            if changed:
                self._persist(self._state)
            return [question.model_copy(deep=True) for question in matches]

    def clear_open_question(self, chat_id: int | str) -> OpenQuestion | None:
        with self._lock:
            match_key = next(
                (key for key, question in self._state.open_questions.items() if str(question.chat_id) == str(chat_id)),
                None,
            )
            question = self._state.open_questions.pop(match_key, None) if match_key is not None else None
            self._persist(self._state)
            return question.model_copy(deep=True) if question is not None else None

    def clear_open_question_by_codex_ids(self, *, app_server_id: str, task_id: str, tool_call_id: str) -> OpenQuestion | None:
        with self._lock:
            match_key: str | None = None
            for key, question in self._state.open_questions.items():
                if (
                    question.app_server_id == app_server_id
                    and question.task_id == task_id
                    and question.tool_call_id == tool_call_id
                ):
                    match_key = key
                    break
            if match_key is None:
                return None
            question = self._state.open_questions.pop(match_key)
            self._persist(self._state)
            return question.model_copy(deep=True)

    def open_questions_for_chat(
        self,
        chat_id: int | str,
        *,
        sender_id: str | None = None,
    ) -> list[OpenQuestion]:
        with self._lock:
            return [
                question.model_copy(deep=True)
                for question in self._state.open_questions.values()
                if str(question.chat_id) == str(chat_id)
                and (sender_id is None or str(question.sender_id) == str(sender_id))
            ]

    def expire_open_questions(self, now: datetime) -> list[OpenQuestion]:
        with self._lock:
            expired_keys = [
                key
                for key, question in self._state.open_questions.items()
                if question.expires_at <= now
            ]
            expired = [self._state.open_questions.pop(key) for key in expired_keys]
            if expired:
                self._persist(self._state)
            return [question.model_copy(deep=True) for question in expired]

    def sync_linear_queue(self, task_payloads: list[dict[str, Any]], *, seen_at: datetime) -> None:
        incoming_by_id = {str(item.get("issue_id") or ""): dict(item) for item in task_payloads}
        incoming_by_id.pop("", None)
        with self._lock:
            for issue_id, payload in incoming_by_id.items():
                existing = self._state.linear_tasks.get(issue_id)
                queue_status = existing.queue_status if existing is not None else "available"
                if queue_status in {"out_of_window"}:
                    queue_status = "available"
                task = LinearQueuedTask(
                    issue_id=issue_id,
                    identifier=str(payload.get("identifier") or issue_id),
                    title=str(payload.get("title") or ""),
                    description_preview=_optional_str(payload.get("description_preview")),
                    url=_optional_str(payload.get("url")),
                    due_date=_optional_str(payload.get("due_date")),
                    priority=_optional_int(payload.get("priority")),
                    updated_at=_optional_str(payload.get("updated_at")),
                    team_id=_optional_str(payload.get("team_id")),
                    team_key=_optional_str(payload.get("team_key")),
                    team_name=_optional_str(payload.get("team_name")),
                    status=_optional_str(payload.get("status")),
                    status_type=_optional_str(payload.get("status_type")),
                    project=_optional_str(payload.get("project")),
                    milestone=_optional_str(payload.get("milestone")),
                    cycle=_optional_str(payload.get("cycle")),
                    labels=[str(item) for item in payload.get("labels", []) if str(item)],
                    assignee_name=_optional_str(payload.get("assignee_name")),
                    creator_name=_optional_str(payload.get("creator_name")),
                    queue_status=queue_status,
                    codex_app_server_id=existing.codex_app_server_id if existing is not None else None,
                    codex_task_id=existing.codex_task_id if existing is not None else None,
                    codex_thread_id=existing.codex_thread_id if existing is not None else None,
                    codex_turn_id=existing.codex_turn_id if existing is not None else None,
                    pr_url=existing.pr_url if existing is not None else None,
                    pr_number=existing.pr_number if existing is not None else None,
                    pr_status=existing.pr_status if existing is not None else None,
                    pr_repository=existing.pr_repository if existing is not None else None,
                    pr_branch=existing.pr_branch if existing is not None else None,
                    pr_title=existing.pr_title if existing is not None else None,
                    pr_summary=existing.pr_summary if existing is not None else None,
                    pr_merged_at=existing.pr_merged_at if existing is not None else None,
                    last_error=existing.last_error if existing is not None else None,
                    selected_at=existing.selected_at if existing is not None else None,
                    started_at=existing.started_at if existing is not None else None,
                    completed_at=existing.completed_at if existing is not None else None,
                    last_seen_at=seen_at,
                )
                self._state.linear_tasks[issue_id] = task
            for issue_id, existing in list(self._state.linear_tasks.items()):
                if issue_id in incoming_by_id:
                    continue
                if existing.queue_status in _LINEAR_BUSY_STATUSES:
                    continue
                if existing.queue_status in _LINEAR_TERMINAL_STATUSES:
                    continue
                self._state.linear_tasks[issue_id] = existing.model_copy(update={"queue_status": "out_of_window"})
            self._state.linear_last_poll_at = seen_at
            self._persist(self._state)

    def available_linear_tasks(self) -> list[LinearQueuedTask]:
        with self._lock:
            busy_projects = self._busy_linear_projects_locked()
            tasks = [
                task.model_copy(deep=True)
                for task in self._state.linear_tasks.values()
                if task.queue_status == "available"
                and _is_explicit_project(task.project)
                and task.status == "Planned"
                and _project_key(task.project) not in busy_projects
            ]
        return sorted(tasks, key=_linear_task_sort_key)

    def linear_available_queue_hash(self) -> str:
        tasks = [task.ai_payload() for task in self.available_linear_tasks()]
        body = json.dumps(tasks, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def mark_linear_queue_emitted(self, queue_hash: str) -> None:
        with self._lock:
            self._state.linear_last_emitted_queue_hash = queue_hash
            self._persist(self._state)

    def linear_busy_reason(self) -> str | None:
        with self._lock:
            for task in self._state.linear_tasks.values():
                if task.queue_status in _LINEAR_BUSY_STATUSES:
                    return f"{task.identifier}:{task.queue_status}"
            for question in self._state.open_questions.values():
                if question.context.get("linear_issue_id") or question.context.get("linear_identifier"):
                    return f"{question.task_id}:waiting_for_user"
        return None

    def _busy_linear_projects_locked(self) -> set[str]:
        return {
            _project_key(task.project)
            for task in self._state.linear_tasks.values()
            if task.queue_status in _LINEAR_BUSY_STATUSES and _is_explicit_project(task.project)
        }

    def mark_linear_task_started(
        self,
        *,
        issue_id: str,
        codex_app_server_id: str,
        codex_task_id: str,
        codex_thread_id: str | None = None,
        codex_turn_id: str | None = None,
        started_at: datetime,
    ) -> LinearQueuedTask | None:
        with self._lock:
            task = self._state.linear_tasks.get(issue_id)
            if task is None:
                return None
            updated = task.model_copy(
                update={
                    "queue_status": "codex_running",
                    "codex_app_server_id": codex_app_server_id,
                    "codex_task_id": codex_task_id,
                    "codex_thread_id": codex_thread_id,
                    "codex_turn_id": codex_turn_id,
                    "selected_at": task.selected_at or started_at,
                    "started_at": started_at,
                    "last_error": None,
                }
            )
            self._state.linear_tasks[issue_id] = updated
            self._persist(self._state)
            return updated.model_copy(deep=True)

    def mark_linear_task_codex_turn(
        self,
        *,
        app_server_id: str,
        task_id: str,
        thread_id: str | None,
        turn_id: str | None,
    ) -> LinearQueuedTask | None:
        with self._lock:
            for issue_id, task in self._state.linear_tasks.items():
                if task.codex_app_server_id == app_server_id and task.codex_task_id == task_id:
                    updates: dict[str, Any] = {}
                    if thread_id:
                        updates["codex_thread_id"] = thread_id
                    if turn_id:
                        updates["codex_turn_id"] = turn_id
                    if not updates:
                        return task.model_copy(deep=True)
                    updated = task.model_copy(update=updates)
                    self._state.linear_tasks[issue_id] = updated
                    self._persist(self._state)
                    return updated.model_copy(deep=True)
        return None

    def mark_linear_task_pr_event(
        self,
        *,
        app_server_id: str,
        task_id: str,
        event_type: str,
        pr_url: str,
        repository: str,
        pr_number: int | None,
        branch: str | None,
        title: str | None,
        summary: str | None,
        timestamp: datetime,
    ) -> LinearQueuedTask | None:
        queue_status = "under_review" if event_type == "opened" else "completed"
        with self._lock:
            for issue_id, task in self._state.linear_tasks.items():
                if task.codex_app_server_id != app_server_id or task.codex_task_id != task_id:
                    continue
                updates: dict[str, Any] = {
                    "queue_status": queue_status,
                    "pr_url": pr_url,
                    "pr_number": pr_number,
                    "pr_status": event_type,
                    "pr_repository": repository,
                    "pr_branch": branch,
                    "pr_title": title,
                    "pr_summary": summary,
                    "last_error": None,
                }
                if event_type == "merged":
                    updates["completed_at"] = timestamp
                    updates["pr_merged_at"] = timestamp
                updated = task.model_copy(update=updates)
                self._state.linear_tasks[issue_id] = updated
                self._persist(self._state)
                return updated.model_copy(deep=True)
        return None

    def mark_linear_task_error(self, *, issue_id: str, error: str, timestamp: datetime) -> LinearQueuedTask | None:
        with self._lock:
            task = self._state.linear_tasks.get(issue_id)
            if task is None:
                return None
            updated = task.model_copy(update={"queue_status": "error", "last_error": error, "completed_at": timestamp})
            self._state.linear_tasks[issue_id] = updated
            self._persist(self._state)
            return updated.model_copy(deep=True)

    def mark_linear_task_last_error(self, *, issue_id: str, error: str) -> LinearQueuedTask | None:
        with self._lock:
            task = self._state.linear_tasks.get(issue_id)
            if task is None:
                return None
            updated = task.model_copy(update={"last_error": error})
            self._state.linear_tasks[issue_id] = updated
            self._persist(self._state)
            return updated.model_copy(deep=True)

    def mark_linear_task_waiting_for_user(
        self,
        *,
        issue_id: str,
    ) -> LinearQueuedTask | None:
        return self._mark_linear_task_queue_status(issue_id=issue_id, queue_status="waiting_for_user")

    def mark_linear_task_running_by_codex_ids(
        self,
        *,
        app_server_id: str,
        task_id: str,
    ) -> LinearQueuedTask | None:
        with self._lock:
            for issue_id, task in self._state.linear_tasks.items():
                if task.codex_app_server_id == app_server_id and task.codex_task_id == task_id:
                    updated = task.model_copy(update={"queue_status": "codex_running"})
                    self._state.linear_tasks[issue_id] = updated
                    self._persist(self._state)
                    return updated.model_copy(deep=True)
        return None

    def linear_task_by_codex_ids(self, *, app_server_id: str, task_id: str) -> LinearQueuedTask | None:
        with self._lock:
            for task in self._state.linear_tasks.values():
                if task.codex_app_server_id == app_server_id and task.codex_task_id == task_id:
                    return task.model_copy(deep=True)
        return None

    def mark_linear_task_lifecycle_status(
        self,
        *,
        issue_id: str,
        status_alias: str,
        timestamp: datetime,
    ) -> LinearQueuedTask | None:
        queue_status = {
            "in_progress": "codex_running",
            "under_review": "under_review",
            "completed": "completed",
        }.get(status_alias, status_alias)
        updates: dict[str, Any] = {"queue_status": queue_status, "last_error": None}
        if queue_status == "completed":
            updates["completed_at"] = timestamp
        with self._lock:
            task = self._state.linear_tasks.get(issue_id)
            if task is None:
                return None
            updated = task.model_copy(update=updates)
            self._state.linear_tasks[issue_id] = updated
            self._persist(self._state)
            return updated.model_copy(deep=True)

    def _mark_linear_task_queue_status(self, *, issue_id: str, queue_status: str) -> LinearQueuedTask | None:
        with self._lock:
            task = self._state.linear_tasks.get(issue_id)
            if task is None:
                return None
            updated = task.model_copy(update={"queue_status": queue_status})
            self._state.linear_tasks[issue_id] = updated
            self._persist(self._state)
            return updated.model_copy(deep=True)

    def clear_session(self) -> None:
        with self._lock:
            self._state.active_chat_id = None
            self._state.active_session_id = None
            self._state.pending_chat_id = None
            self._state.pending_session_id = None
            self._state.last_engagement_at = utc_now()
            self._state.conversation_engaged_user_ids = []
            self._state.pending_engaged_user_ids = []
            self._state.pending_interruption = None
            self._persist(self._state)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _codex_question_key(app_server_id: str, task_id: str, tool_call_id: str) -> str:
    return f"{app_server_id}:{task_id}:{tool_call_id}"


def _is_explicit_project(project: str | None) -> bool:
    return bool(str(project or "").strip())


def _project_key(project: str | None) -> str:
    return str(project or "").strip().casefold()


def _linear_priority_sort_value(priority: int | None) -> int:
    if priority in {1, 2, 3, 4}:
        return int(priority)
    return 5


def _linear_task_sort_key(task: LinearQueuedTask) -> tuple[str, int, str]:
    return (
        task.due_date or "",
        _linear_priority_sort_value(task.priority),
        task.identifier,
    )
