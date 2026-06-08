#!/usr/bin/env python3
"""Read-only Todoist client for Nico Day Planner v0.5.

This module intentionally implements only read operations. It never creates,
updates, closes, moves, or deletes Todoist tasks.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TODOIST_API_URL = "https://api.todoist.com/rest/v2/tasks"
TOKEN_ENV_VAR = "TODOIST_API_TOKEN"
DEFAULT_DURATION_MINUTES = 30
CATEGORY_LABELS = {
    "werkstatt": "Werkstatt",
    "studio": "Studio",
    "alegra": "ALEGRA",
    "haushalt": "Haushalt",
    "privat": "Privat",
    "live": "LIVE",
    "soundwerk": "Soundwerk",
    "buchhaltung": "Buchhaltung",
}


@dataclass(frozen=True)
class TodoistReadResult:
    """Result of a read-only Todoist fetch."""

    tasks: list[dict[str, Any]]
    status: str
    used_fallback: bool = False


class TodoistReadError(RuntimeError):
    """Raised when Todoist read-only loading cannot continue."""


def _priority_from_todoist(value: int | str | None) -> str:
    """Convert Todoist priority 1-4 to planner P1-P4.

    Todoist stores priority in reverse from the UI labels: API priority 4 is
    the highest priority. The planner uses P1 as highest priority.
    """
    try:
        numeric = int(value or 1)
    except (TypeError, ValueError):
        numeric = 1
    return {4: "P1", 3: "P2", 2: "P3", 1: "P4"}.get(numeric, "P4")


def _category_from_task(task: dict[str, Any]) -> str:
    """Infer the planner category from Todoist labels, section, project, or title."""
    labels = [str(label).lower() for label in task.get("labels", [])]
    searchable = " ".join(
        labels
        + [
            str(task.get("section_id", "")).lower(),
            str(task.get("project_id", "")).lower(),
            str(task.get("content", "")).lower(),
            str(task.get("description", "")).lower(),
        ]
    )
    for needle, category in CATEGORY_LABELS.items():
        if needle in searchable:
            return category
    return "Privat"


def _duration_minutes(task: dict[str, Any]) -> int | None:
    """Read Todoist duration if present; otherwise return None for estimation."""
    duration = task.get("duration")
    if not isinstance(duration, dict):
        return None

    amount = duration.get("amount")
    unit = duration.get("unit")
    try:
        amount_int = int(amount)
    except (TypeError, ValueError):
        return None

    if unit == "minute":
        return amount_int
    if unit == "day":
        return amount_int * 8 * 60
    return None


def normalize_todoist_task(task: dict[str, Any]) -> dict[str, Any]:
    """Map one Todoist REST task to the planner's neutral task schema."""
    description = task.get("description") or ""
    normalized: dict[str, Any] = {
        "id": str(task.get("id", "todoist-unknown")),
        "title": str(task.get("content", "Ohne Titel")),
        "category": _category_from_task(task),
        "priority": _priority_from_todoist(task.get("priority")),
        "duration_minutes": _duration_minutes(task),
    }
    if description:
        normalized["notes"] = str(description)
    return normalized


def fetch_open_tasks(token: str, timeout_seconds: int = 20) -> list[dict[str, Any]]:
    """Fetch open Todoist tasks via the official read endpoint only."""
    request = Request(
        TODOIST_API_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw_body = response.read().decode("utf-8")
    except HTTPError as exc:
        raise TodoistReadError(f"Todoist read-only request failed with HTTP {exc.code}.") from exc
    except URLError as exc:
        raise TodoistReadError(f"Todoist read-only request failed: {exc.reason}.") from exc
    except TimeoutError as exc:
        raise TodoistReadError("Todoist read-only request timed out.") from exc

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise TodoistReadError("Todoist returned invalid JSON.") from exc

    if not isinstance(payload, list):
        raise TodoistReadError("Todoist returned an unexpected response shape.")

    return [normalize_todoist_task(task) for task in payload if isinstance(task, dict)]


def load_todoist_tasks_from_env() -> TodoistReadResult:
    """Load Todoist tasks using TODOIST_API_TOKEN, or report that fallback is needed."""
    token = os.environ.get(TOKEN_ENV_VAR)
    if not token:
        return TodoistReadResult(
            tasks=[],
            status=f"{TOKEN_ENV_VAR} fehlt – verwende lokale JSON-Beispieldaten.",
            used_fallback=True,
        )

    tasks = fetch_open_tasks(token)
    return TodoistReadResult(
        tasks=tasks,
        status=f"Todoist read-only: {len(tasks)} offene Aufgabe(n) geladen.",
        used_fallback=False,
    )
