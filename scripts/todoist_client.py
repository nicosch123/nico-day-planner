#!/usr/bin/env python3
"""Read-only Todoist client for Nico Day Planner v0.5.

This module intentionally implements only read operations. It never creates,
updates, closes, moves, comments on, labels, or deletes Todoist tasks.
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

TODOIST_API_BASE_URL = "https://api.todoist.com/api/v1"
TODOIST_API_URL = f"{TODOIST_API_BASE_URL}/tasks"
TODOIST_PROJECTS_URL = f"{TODOIST_API_BASE_URL}/projects"
TODOIST_SECTIONS_URL = f"{TODOIST_API_BASE_URL}/sections"
TODOIST_LABELS_URL = f"{TODOIST_API_BASE_URL}/labels"
TOKEN_ENV_VAR = "TODOIST_API_TOKEN"
DEFAULT_DURATION_MINUTES = 30
SUPPORTED_CATEGORIES = (
    "Werkstatt",
    "Studio",
    "ALEGRA",
    "Haushalt",
    "Privat",
    "LIVE",
    "Soundwerk",
    "Buchhaltung",
)
CATEGORY_ALIASES: tuple[tuple[str, str], ...] = (
    ("werkstatt", "Werkstatt"),
    ("studio", "Studio"),
    ("momo", "Studio"),
    ("alegra", "ALEGRA"),
    ("haushalt", "Haushalt"),
    ("privat", "Privat"),
    ("live", "LIVE"),
    ("foh", "LIVE"),
    ("tontechnik", "LIVE"),
    ("soundwerk", "Soundwerk"),
    ("unterricht", "Soundwerk"),
    ("musikschule", "Soundwerk"),
    ("buchhaltung", "Buchhaltung"),
    ("steuer", "Buchhaltung"),
    ("lexware", "Buchhaltung"),
    ("gewerbe", "Buchhaltung"),
)


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


def _category_from_text(value: str) -> str | None:
    """Return the first planner category whose alias appears in the text."""
    haystack = value.lower()
    for needle, category in CATEGORY_ALIASES:
        if needle in haystack:
            return category
    return None


def _category_from_task(
    task: dict[str, Any],
    project_map: dict[str, str] | None = None,
    section_map: dict[str, str] | None = None,
    labels: list[str] | None = None,
) -> str:
    """Infer category, preferring project name, then section name, labels, title, and notes."""
    project_map = project_map or {}
    section_map = section_map or {}
    project_name = project_map.get(str(task.get("project_id", "")), "")
    section_name = section_map.get(str(task.get("section_id", "")), "")
    labels = labels or [str(label) for label in task.get("labels", [])]
    title_and_notes = " ".join([str(task.get("content", "")), str(task.get("description", ""))])

    for value in (project_name, section_name, " ".join(labels), title_and_notes):
        category = _category_from_text(value)
        if category:
            return category
    return "Privat"


SUPPORTED_DURATION_MINUTES = {15, 30, 45, 60, 90, 120}
DURATION_LABEL_RE = re.compile(
    r"^(?P<minutes>\d+)m(?:in)?$|^(?P<hours>\d+(?:[.,]\d+)?)h$",
    re.IGNORECASE,
)
DESCRIPTION_DURATION_RE = re.compile(
    r"(?:^|\b)(?:dauer|duration)\s*:\s*"
    r"(?P<amount>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>m|min|minute|minutes|h|hour|hours)?\b",
    re.IGNORECASE,
)


def _supported_minutes_from_amount(amount: str, unit: str | None) -> int | None:
    """Convert a supported textual duration amount to minutes."""
    normalized_amount = amount.replace(",", ".")
    try:
        value = float(normalized_amount)
    except ValueError:
        return None

    if unit and unit.lower() in {"h", "hour", "hours"}:
        value *= 60

    minutes = int(value)
    if minutes != value:
        return None
    return minutes if minutes in SUPPORTED_DURATION_MINUTES else None


def _native_duration_minutes(task: dict[str, Any]) -> int | None:
    """Read Todoist native duration if present; otherwise return None."""
    duration = task.get("duration")
    if not isinstance(duration, dict):
        return None

    amount = duration.get("amount")
    unit = duration.get("unit")
    try:
        amount_int = int(amount)
    except (TypeError, ValueError):
        return None

    if unit in {"minute", "minutes"}:
        return amount_int
    if unit in {"day", "days"}:
        return amount_int * 8 * 60
    return None


def _duration_from_labels(labels: list[str]) -> int | None:
    """Return duration encoded by a supported Todoist label."""
    for label in labels:
        match = DURATION_LABEL_RE.fullmatch(label.strip())
        if not match:
            continue
        if match.group("minutes"):
            return _supported_minutes_from_amount(match.group("minutes"), "m")
        hours = match.group("hours")
        if hours:
            return _supported_minutes_from_amount(hours, "h")
    return None


def _duration_from_description(description: str) -> int | None:
    """Return duration encoded in the task description."""
    match = DESCRIPTION_DURATION_RE.search(description)
    if not match:
        return None
    return _supported_minutes_from_amount(match.group("amount"), match.group("unit"))


def _duration_minutes(task: dict[str, Any], labels: list[str]) -> tuple[int | None, str]:
    """Resolve duration by priority: native Todoist, label, description, missing."""
    native_minutes = _native_duration_minutes(task)
    if native_minutes is not None:
        return native_minutes, "native"

    label_minutes = _duration_from_labels(labels)
    if label_minutes is not None:
        return label_minutes, "label"

    description_minutes = _duration_from_description(str(task.get("description") or ""))
    if description_minutes is not None:
        return description_minutes, "description"

    return None, "missing"


def normalize_todoist_task(
    task: dict[str, Any],
    project_map: dict[str, str] | None = None,
    section_map: dict[str, str] | None = None,
    label_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Map one Todoist task to the planner's neutral task schema."""
    description = task.get("description") or ""
    project_map = project_map or {}
    section_map = section_map or {}
    label_map = label_map or {}
    labels = [label_map.get(str(label), str(label)) for label in task.get("labels", [])]
    duration_minutes, duration_source = _duration_minutes(task, labels)
    project_id = str(task.get("project_id", ""))
    section_id = str(task.get("section_id", ""))
    project_name = project_map.get(project_id, "")
    section_name = section_map.get(section_id, "")
    normalized: dict[str, Any] = {
        "id": str(task.get("id", "todoist-unknown")),
        "title": str(task.get("content", "Ohne Titel")),
        "category": _category_from_task(task, project_map, section_map, labels),
        "priority": _priority_from_todoist(task.get("priority")),
        "duration_minutes": duration_minutes,
        "duration_source": duration_source,
    }
    if description:
        normalized["notes"] = str(description)
    if project_name:
        normalized["project_name"] = project_name
    if section_name:
        normalized["section_name"] = section_name
    return normalized


def _read_todoist_page(url: str, token: str, cursor: str | None, timeout_seconds: int) -> Any:
    """Read one Todoist API page via GET without mutating Todoist data."""
    page_url = url
    if cursor:
        page_url = f"{url}?{urlencode({'cursor': cursor})}"

    request = Request(
        page_url,
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


def _items_and_next_cursor(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    """Support both list responses and paginated object responses from Todoist."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)], None

    if isinstance(payload, dict):
        results = payload.get("results")
        if not isinstance(results, list):
            raise TodoistReadError("Todoist returned an unexpected response shape.")
        next_cursor = payload.get("next_cursor")
        items = [item for item in results if isinstance(item, dict)]
        return items, str(next_cursor) if next_cursor else None

    raise TodoistReadError("Todoist returned an unexpected response shape.")


def _fetch_collection(url: str, token: str, timeout_seconds: int = 20) -> list[dict[str, Any]]:
    """Fetch all pages from a read-only Todoist collection endpoint."""
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()

    while True:
        payload = _read_todoist_page(url, token, cursor, timeout_seconds)
        page_items, next_cursor = _items_and_next_cursor(payload)
        items.extend(page_items)

        if not next_cursor:
            return items
        if next_cursor in seen_cursors:
            raise TodoistReadError("Todoist pagination returned a repeated cursor.")
        seen_cursors.add(next_cursor)
        cursor = next_cursor


def _id_name_map(items: list[dict[str, Any]]) -> dict[str, str]:
    """Build an id-to-name map from Todoist project, section, or label objects."""
    result: dict[str, str] = {}
    for item in items:
        item_id = item.get("id")
        name = item.get("name")
        if item_id is None or name is None:
            continue
        result[str(item_id)] = str(name)
    return result


def fetch_projects(token: str, timeout_seconds: int = 20) -> dict[str, str]:
    """Fetch Todoist projects via GET and return project_id -> project_name."""
    return _id_name_map(_fetch_collection(TODOIST_PROJECTS_URL, token, timeout_seconds))


def fetch_sections(token: str, timeout_seconds: int = 20) -> dict[str, str]:
    """Fetch Todoist sections via GET and return section_id -> section_name."""
    return _id_name_map(_fetch_collection(TODOIST_SECTIONS_URL, token, timeout_seconds))


def fetch_labels(token: str, timeout_seconds: int = 20) -> dict[str, str]:
    """Fetch Todoist labels via GET and return label_id -> label_name."""
    return _id_name_map(_fetch_collection(TODOIST_LABELS_URL, token, timeout_seconds))


def fetch_open_tasks(
    token: str,
    timeout_seconds: int = 20,
    project_map: dict[str, str] | None = None,
    section_map: dict[str, str] | None = None,
    label_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch all open Todoist tasks via the official read endpoint only."""
    tasks = _fetch_collection(TODOIST_API_URL, token, timeout_seconds)
    return [normalize_todoist_task(task, project_map, section_map, label_map) for task in tasks]


def _analysis_details(
    tasks: list[dict[str, Any]],
    project_count: int,
    section_count: int,
    label_count: int,
) -> tuple[str, ...]:
    category_counts = Counter(str(task.get("category", "Privat")) for task in tasks)
    category_summary = ", ".join(
        f"{category}={category_counts.get(category, 0)}" for category in SUPPORTED_CATEGORIES
    )
    duration_counts = Counter(str(task.get("duration_source", "missing")) for task in tasks)
    missing_duration_count = duration_counts.get("missing", 0)
    missing_titles = [
        str(task.get("title", "Ohne Titel"))
        for task in tasks
        if task.get("duration_source") == "missing"
    ][:20]
    missing_preview = "; ".join(missing_titles) if missing_titles else "Keine"
    return (
        f"Todoist Projekte read-only: {project_count} geladen.",
        f"Todoist Sections read-only: {section_count} geladen.",
        f"Todoist Labels read-only: {label_count} geladen.",
        f"Kategorie-Verteilung nach Projekt/Section/Label/Titel-Mapping: {category_summary}.",
        f"Dauer aus nativer Todoist-Dauer: {duration_counts.get('native', 0)}.",
        f"Dauer aus Label: {duration_counts.get('label', 0)}.",
        f"Dauer aus Beschreibung: {duration_counts.get('description', 0)}.",
        f"Aufgaben ohne erkannte Dauer: {missing_duration_count}.",
        f"Erste 20 Aufgaben ohne Dauer: {missing_preview}.",
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

    project_map = fetch_projects(token)
    section_map = fetch_sections(token)
    label_map = fetch_labels(token)
    tasks = fetch_open_tasks(
        token,
        project_map=project_map,
        section_map=section_map,
        label_map=label_map,
    )
    return TodoistReadResult(
        tasks=tasks,
        status=f"Todoist read-only: {len(tasks)} offene Aufgabe(n) geladen.",
        status_details=_analysis_details(tasks, len(project_map), len(section_map), len(label_map)),
    )
