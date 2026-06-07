from __future__ import annotations

from uuid import uuid4


def new_event_id() -> str:
    return f"evt_{uuid4().hex}"


def new_correlation_id() -> str:
    return f"corr_{uuid4().hex}"


def new_session_id() -> str:
    return f"session_{uuid4().hex}"


def new_memory_id() -> str:
    return f"mem_{uuid4().hex}"

