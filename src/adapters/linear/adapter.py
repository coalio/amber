from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.adapters.base import BaseAdapter
from src.adapters.linear.client import LinearGraphQLClient


class LinearAdapter(BaseAdapter):
    name = "linear"

    def __init__(
        self,
        *,
        api_key: str | None,
        api_url: str = "https://api.linear.app/graphql",
        status_names: Mapping[str, str] | None = None,
        client: LinearGraphQLClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_url = api_url
        self._client = client
        self._status_names = {
            str(alias): str(name)
            for alias, name in (status_names or {}).items()
        }

    def preflight(self) -> None:
        if not self._api_key and self._client is None:
            raise RuntimeError("Linear adapter requires AMBER_LINEAR_API_KEY.")

    def set_task_status(self, *, issue_id: str, status: str, note: str | None = None) -> dict[str, Any]:
        self.preflight()
        status_name = self._status_names.get(status)
        if status_name is None:
            allowed = ", ".join(sorted(self._status_names))
            raise RuntimeError(f"Unsupported Linear status alias {status!r}. Allowed: {allowed}")
        result = self._linear_client().update_issue_status(issue_id=issue_id, status_name=status_name)
        issue = result.get("issue") if isinstance(result.get("issue"), dict) else {}
        state = issue.get("state") if isinstance(issue.get("state"), dict) else {}
        return {
            "success": result.get("success") is True,
            "issue_id": issue.get("id") or issue_id,
            "identifier": issue.get("identifier"),
            "url": issue.get("url"),
            "status": state.get("name") or status_name,
            "status_alias": status,
            "note": note,
        }

    def _linear_client(self) -> LinearGraphQLClient:
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise RuntimeError("Linear adapter requires AMBER_LINEAR_API_KEY.")
        self._client = LinearGraphQLClient(api_key=self._api_key, api_url=self._api_url)
        return self._client
