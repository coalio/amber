from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any
from urllib import error, request

from src.adapters.linear.models import LinearIssue, LinearWorkflowState


class LinearGraphQLClient:
    def __init__(
        self,
        *,
        api_key: str,
        api_url: str = "https://api.linear.app/graphql",
        timeout_seconds: float = 30.0,
    ) -> None:
        if not api_key:
            raise RuntimeError("Linear API key is required.")
        self._api_key = api_key
        self._api_url = api_url
        self._timeout_seconds = timeout_seconds

    def viewer_id(self) -> str:
        payload = self.execute(
            """
            query Viewer {
              viewer {
                id
              }
            }
            """
        )
        viewer = payload.get("viewer") if isinstance(payload.get("viewer"), dict) else {}
        viewer_id = str(viewer.get("id") or "")
        if not viewer_id:
            raise RuntimeError("Linear viewer query did not return an id.")
        return viewer_id

    def assigned_issues(self, *, assignee_id: str | None = None, page_size: int = 50) -> list[LinearIssue]:
        assignee_id = assignee_id or self.viewer_id()
        issues: list[LinearIssue] = []
        cursor: str | None = None
        while True:
            payload = self.execute(
                """
                query AssignedIssues($assigneeId: ID!, $first: Int!, $after: String) {
                  issues(
                    first: $first
                    after: $after
                    filter: { assignee: { id: { eq: $assigneeId } } }
                  ) {
                    nodes {
                      id
                      identifier
                      title
                      description
                      url
                      dueDate
                      priority
                      updatedAt
                      team {
                        id
                        key
                        name
                      }
                      state {
                        id
                        name
                        type
                      }
                      project {
                        id
                        name
                      }
                      projectMilestone {
                        id
                        name
                      }
                      cycle {
                        id
                        name
                        number
                      }
                      labels {
                        nodes {
                          name
                        }
                      }
                      assignee {
                        id
                        name
                        displayName
                      }
                      creator {
                        id
                        name
                        displayName
                      }
                    }
                    pageInfo {
                      hasNextPage
                      endCursor
                    }
                  }
                }
                """,
                {"assigneeId": assignee_id, "first": page_size, "after": cursor},
            )
            connection = payload.get("issues") if isinstance(payload.get("issues"), dict) else {}
            nodes = connection.get("nodes") if isinstance(connection.get("nodes"), list) else []
            issues.extend(self._issue_from_node(node) for node in nodes if isinstance(node, dict))
            page_info = connection.get("pageInfo") if isinstance(connection.get("pageInfo"), dict) else {}
            if not page_info.get("hasNextPage"):
                return issues
            cursor = str(page_info.get("endCursor") or "")
            if not cursor:
                return issues

    def issue_team_states(self, issue_id: str) -> tuple[str, str, list[LinearWorkflowState]]:
        payload = self.execute(
            """
            query IssueTeamStates($issueId: String!) {
              issue(id: $issueId) {
                id
                identifier
                team {
                  id
                  states {
                    nodes {
                      id
                      name
                      type
                    }
                  }
                }
              }
            }
            """,
            {"issueId": issue_id},
        )
        issue = payload.get("issue") if isinstance(payload.get("issue"), dict) else {}
        if not issue:
            raise RuntimeError(f"Linear issue not found: {issue_id}")
        team = issue.get("team") if isinstance(issue.get("team"), dict) else {}
        states_connection = team.get("states") if isinstance(team.get("states"), dict) else {}
        nodes = states_connection.get("nodes") if isinstance(states_connection.get("nodes"), list) else []
        states = [self._state_from_node(node) for node in nodes if isinstance(node, dict)]
        return str(issue.get("id") or issue_id), str(issue.get("identifier") or issue_id), states

    def update_issue_status(self, *, issue_id: str, status_name: str) -> dict[str, Any]:
        resolved_issue_id, identifier, states = self.issue_team_states(issue_id)
        state = self._state_by_name(states, status_name)
        if state is None:
            available = ", ".join(sorted(item.name for item in states))
            raise RuntimeError(
                f"Linear status {status_name!r} was not found for {identifier}. Available statuses: {available}"
            )
        payload = self.execute(
            """
            mutation IssueUpdate($issueId: String!, $stateId: String!) {
              issueUpdate(id: $issueId, input: { stateId: $stateId }) {
                success
                issue {
                  id
                  identifier
                  title
                  url
                  state {
                    id
                    name
                    type
                  }
                }
              }
            }
            """,
            {"issueId": resolved_issue_id, "stateId": state.id},
        )
        result = payload.get("issueUpdate") if isinstance(payload.get("issueUpdate"), dict) else {}
        if result.get("success") is not True:
            raise RuntimeError(f"Linear did not confirm status update for {identifier}.")
        return result

    def execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        req = request.Request(
            self._api_url,
            data=body,
            headers={
                "Authorization": self._api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self._timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(f"Linear GraphQL request failed: HTTP {exc.code}{suffix}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Linear GraphQL request failed: {exc}") from exc
        parsed = json.loads(raw or "{}")
        if not isinstance(parsed, dict):
            raise RuntimeError("Linear GraphQL returned a non-object JSON response.")
        errors = parsed.get("errors")
        if errors:
            raise RuntimeError(f"Linear GraphQL returned errors: {json.dumps(errors, ensure_ascii=False)}")
        data = parsed.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Linear GraphQL response did not include data.")
        return data

    def _issue_from_node(self, node: dict[str, Any]) -> LinearIssue:
        team = node.get("team") if isinstance(node.get("team"), dict) else {}
        project = node.get("project") if isinstance(node.get("project"), dict) else {}
        milestone = node.get("projectMilestone") if isinstance(node.get("projectMilestone"), dict) else {}
        cycle = node.get("cycle") if isinstance(node.get("cycle"), dict) else {}
        labels = node.get("labels") if isinstance(node.get("labels"), dict) else {}
        label_nodes = labels.get("nodes") if isinstance(labels.get("nodes"), list) else []
        assignee = node.get("assignee") if isinstance(node.get("assignee"), dict) else {}
        creator = node.get("creator") if isinstance(node.get("creator"), dict) else {}
        return LinearIssue(
            id=str(node.get("id") or ""),
            identifier=str(node.get("identifier") or ""),
            title=str(node.get("title") or ""),
            description=self._optional_str(node.get("description")),
            url=self._optional_str(node.get("url")),
            due_date=self._parse_date(node.get("dueDate")),
            priority=self._optional_int(node.get("priority")),
            updated_at=self._parse_datetime(node.get("updatedAt")),
            team_id=self._optional_str(team.get("id")),
            team_key=self._optional_str(team.get("key")),
            team_name=self._optional_str(team.get("name")),
            state=self._state_from_node(node.get("state") if isinstance(node.get("state"), dict) else {}),
            project=self._optional_str(project.get("name")),
            milestone=self._optional_str(milestone.get("name")),
            cycle=self._cycle_name(cycle),
            labels=[str(item.get("name")) for item in label_nodes if isinstance(item, dict) and item.get("name")],
            assignee_name=self._display_name(assignee),
            creator_name=self._display_name(creator),
        )

    def _state_from_node(self, node: dict[str, Any]) -> LinearWorkflowState:
        return LinearWorkflowState(
            id=str(node.get("id") or ""),
            name=str(node.get("name") or ""),
            type=self._optional_str(node.get("type")),
        )

    def _state_by_name(
        self,
        states: list[LinearWorkflowState],
        status_name: str,
    ) -> LinearWorkflowState | None:
        wanted = status_name.casefold()
        for state in states:
            if state.name.casefold() == wanted:
                return state
        return None

    def _cycle_name(self, cycle: dict[str, Any]) -> str | None:
        if not cycle:
            return None
        name = self._optional_str(cycle.get("name"))
        number = self._optional_int(cycle.get("number"))
        if name:
            return name
        if number is not None:
            return f"Cycle {number}"
        return None

    def _display_name(self, payload: dict[str, Any]) -> str | None:
        return self._optional_str(payload.get("displayName")) or self._optional_str(payload.get("name"))

    def _optional_str(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value)
        return text if text else None

    def _optional_int(self, value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _parse_date(self, value: Any) -> date | None:
        if not value:
            return None
        return date.fromisoformat(str(value))

    def _parse_datetime(self, value: Any) -> datetime | None:
        if not value:
            return None
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text)
