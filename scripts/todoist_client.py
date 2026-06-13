#!/usr/bin/env python3
"""Read-only Todoist client for Nico Day Planner v0.5.

This module intentionally implements only read operations. It never creates,
updates, closes, moves, or deletes Todoist tasks.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

TODOIST_API_URL = "https://api.todoist.com/api/v1/tasks"
TOKEN_ENV_VAR = "TODOIST_API_TOKEN"
DEFAULT_DURATION_MINUTES = 30
DURATION_LABEL_PATTERN = re.compile(
    r"^\s*(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<unit>m|min|h|std|stunde|stunden)\s*$",
    re.IGNORECASE,
)
DURATION_DESCRIPTION_PATTERN = re.compile(
    r"(?:^|\b)(?:dauer|duration)\s*:\s*(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<unit>m|min|h|std|stunde|stunden)?\b",
    re.IGNORECASE,
)
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
    status_details: tuple[str, ...] = field(default_factory=tuple)


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


def _amount_to_minutes(amount: str, unit: str | None) -> int | None:
    """Convert a duration amount/unit pair into minutes."""
    try:
        numeric = float(amount.replace(",", "."))
    except ValueError:
        return None

    normalized_unit = (unit or "min").lower()
    if normalized_unit in {"m", "min"}:
        minutes = numeric
    elif normalized_unit in {"h", "std", "stunde", "stunden"}:
        minutes = numeric * 60
    else:
        return None

    if minutes <= 0:
        return None
    return int(round(minutes))


def _native_duration_minutes(task: dict[str, Any]) -> int | None:
    """Read Todoist native duration if present."""
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


def _label_duration_minutes(task: dict[str, Any]) -> int | None:
    """Read duration labels such as 15min, 30m, 1h, 1.5h, or 2h."""
    for label in task.get("labels", []):
        match = DURATION_LABEL_PATTERN.match(str(label))
        if not match:
            continue
        minutes = _amount_to_minutes(match.group("amount"), match.group("unit"))
        if minutes is not None:
            return minutes
    return None


def _description_duration_minutes(task: dict[str, Any]) -> int | None:
    """Read duration from descriptions such as 'Dauer: 45' or 'duration: 1.5h'."""
    description = str(task.get("description") or "")
    match = DURATION_DESCRIPTION_PATTERN.search(description)
    if not match:
        return None
    return _amount_to_minutes(match.group("amount"), match.group("unit"))


def _duration_minutes_with_source(task: dict[str, Any]) -> tuple[int | None, str]:
    """Resolve duration by priority: native Todoist, label, description, then missing."""
    native_duration = _native_duration_minutes(task)
    if native_duration is not None:
        return native_duration, "native"

    label_duration = _label_duration_minutes(task)
    if label_duration is not None:
        return label_duration, "label"

    description_duration = _description_duration_minutes(task)
    if description_duration is not None:
        return description_duration, "description"

    return None, "missing"


def _duration_minutes(task: dict[str, Any]) -> int | None:
    """Resolve Todoist task duration or return None for planner estimation."""
    duration, _source = _duration_minutes_with_source(task)
    return duration


def normalize_todoist_task(task: dict[str, Any]) -> dict[str, Any]:
    """Map one Todoist task to the planner's neutral task schema."""
    description = task.get("description") or ""
    duration_minutes, duration_source = _duration_minutes_with_source(task)
    normalized: dict[str, Any] = {
        "id": str(task.get("id", "todoist-unknown")),
        "title": str(task.get("content", "Ohne Titel")),
        "category": _category_from_task(task),
        "priority": _priority_from_todoist(task.get("priority")),
        "duration_minutes": duration_minutes,
        "duration_source": duration_source,
    }
    if description:
        normalized["notes"] = str(description)
    return normalized


def _read_todoist_page(token: str, cursor: str | None, timeout_seconds: int) -> Any:
    """Read one Todoist tasks page via GET without mutating Todoist data."""
    url = TODOIST_API_URL
    if cursor:
        url = f"{TODOIST_API_URL}?{urlencode({'cursor': cursor})}"

    request = Request(
        url,
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
        return json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise TodoistReadError("Todoist returned invalid JSON.") from exc


def _tasks_and_next_cursor(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    """Support both list responses and paginated object responses from Todoist."""
    if isinstance(payload, list):
        return [task for task in payload if isinstance(task, dict)], None

    if isinstance(payload, dict):
        results = payload.get("results")
        if not isinstance(results, list):
            raise TodoistReadError("Todoist returned an unexpected response shape.")
        next_cursor = payload.get("next_cursor")
        tasks = [task for task in results if isinstance(task, dict)]
        return tasks, str(next_cursor) if next_cursor else None

    raise TodoistReadError("Todoist returned an unexpected response shape.")


def fetch_open_tasks(token: str, timeout_seconds: int = 20) -> list[dict[str, Any]]:
    """Fetch all open Todoist tasks via the official read endpoint only."""
    normalized_tasks: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()

    while True:
        payload = _read_todoist_page(token, cursor, timeout_seconds)
        tasks, next_cursor = _tasks_and_next_cursor(payload)
        normalized_tasks.extend(normalize_todoist_task(task) for task in tasks)

        if not next_cursor:
            return normalized_tasks
        if next_cursor in seen_cursors:
            raise TodoistReadError("Todoist pagination returned a repeated cursor.")
        seen_cursors.add(next_cursor)
        cursor = next_cursor


def _analysis_details(tasks: list[dict[str, Any]]) -> tuple[str, ...]:
    """Return Todoist duration-source diagnostics for dry-run output."""
    duration_source_counts = Counter(str(task.get("duration_source", "missing")) for task in tasks)
    missing_titles = [
        str(task.get("title", "Ohne Titel"))
        for task in tasks
        if task.get("duration_minutes") is None
    ]
    first_missing = "; ".join(missing_titles[:20]) if missing_titles else "Keine"
    return (
        f"Dauer aus nativer Todoist-Dauer: {duration_source_counts.get('native', 0)}.",
        f"Dauer aus Label: {duration_source_counts.get('label', 0)}.",
        f"Dauer aus Beschreibung: {duration_source_counts.get('description', 0)}.",
        f"Aufgaben ohne erkannte Dauer: {duration_source_counts.get('missing', 0)}.",
        f"Erste 20 Aufgaben ohne Dauer: {first_missing}.",
    )


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
        status_details=_analysis_details(tasks),
    )
