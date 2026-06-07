#!/usr/bin/env python3
"""Read-only Todoist client for the local dry-run planner.

This module only performs GET requests against Todoist's REST API. It does not
create, update, close, label, comment on, or delete tasks.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any


TODOIST_API_BASE = "https://api.todoist.com/rest/v2"
TODOIST_TOKEN_ENV = "TODOIST_API_TOKEN"
SUPPORTED_CATEGORIES = {
    "Werkstatt",
    "Studio",
    "ALEGRA",
    "Haushalt",
    "Privat",
    "LIVE",
    "Soundwerk",
    "Buchhaltung",
}
CONTEXT_LABELS = {
    "Mengen",
    "Aulendorf",
    "Werkstatt",
    "Studio",
    "Zuhause",
    "Computer",
    "Telefon",
    "Abends",
    "Unterwegs",
}
DURATION_LABEL_RE = re.compile(r"^@?(15|30|45|60|90|120)min$", re.IGNORECASE)
DURATION_DESCRIPTION_RE = re.compile(r"\b(?:dauer|duration)\s*:\s*(15|30|45|60|90|120)\s*(?:min(?:uten)?)?\b", re.IGNORECASE)


class TodoistReadOnlyError(RuntimeError):
    """Raised when read-only Todoist access cannot be completed."""


def get_api_token() -> str | None:
    """Return the Todoist API token from the environment, if configured."""
    token = os.getenv(TODOIST_TOKEN_ENV)
    return token.strip() if token and token.strip() else None


def todoist_get(path: str, token: str) -> Any:
    """Perform a read-only GET request against Todoist REST API."""
    request = urllib.request.Request(
        f"{TODOIST_API_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise TodoistReadOnlyError(f"Todoist GET {path} failed with HTTP {error.code}: {body}") from error
    except urllib.error.URLError as error:
        raise TodoistReadOnlyError(f"Todoist GET {path} failed: {error.reason}") from error


def fetch_open_tasks(token: str) -> list[dict[str, Any]]:
    """Fetch open Todoist tasks without mutating anything."""
    tasks = todoist_get("/tasks", token)
    if not isinstance(tasks, list):
        raise TodoistReadOnlyError("Todoist /tasks response was not a list")
    return tasks


def fetch_projects(token: str) -> dict[str, str]:
    """Fetch Todoist project names keyed by project id."""
    projects = todoist_get("/projects", token)
    if not isinstance(projects, list):
        raise TodoistReadOnlyError("Todoist /projects response was not a list")
    return {str(project["id"]): str(project.get("name", "")) for project in projects if "id" in project}


def priority_to_planner(priority: int) -> str:
    """Map Todoist priority 1..4 to planner P4..P1."""
    return {4: "P1", 3: "P2", 2: "P3", 1: "P4"}.get(priority, "P4")


def duration_from_labels(labels: list[str]) -> int | None:
    """Extract a duration from labels like 30min or @30min."""
    for label in labels:
        match = DURATION_LABEL_RE.match(label)
        if match:
            return int(match.group(1))
    return None


def duration_from_description(description: str) -> int | None:
    """Extract a duration from description text like 'Dauer: 60 min'."""
    match = DURATION_DESCRIPTION_RE.search(description)
    if match:
        return int(match.group(1))
    return None


def extract_context_labels(labels: list[str]) -> list[str]:
    """Return optional context labels used as soft planning hints."""
    normalized = {label.lstrip("@").casefold(): label.lstrip("@") for label in labels}
    contexts = []
    for context in CONTEXT_LABELS:
        if context.casefold() in normalized:
            contexts.append(context)
    return contexts


def project_to_category(project_name: str) -> str:
    """Map Todoist project name to a supported planner category."""
    for category in SUPPORTED_CATEGORIES:
        if project_name.casefold() == category.casefold():
            return category
    return "Privat"


def labels_contain(labels: list[str], *needles: str) -> bool:
    """Return whether labels contain any of the provided labels case-insensitively."""
    normalized = {label.lstrip("@").casefold() for label in labels}
    return any(needle.casefold() in normalized for needle in needles)


def map_todoist_task(task: dict[str, Any], projects: dict[str, str]) -> dict[str, Any]:
    """Map a Todoist task dictionary to the planner fixture-like task shape."""
    labels = [str(label) for label in task.get("labels", [])]
    description = str(task.get("description", ""))
    duration = duration_from_labels(labels)
    if duration is None:
        duration = duration_from_description(description)

    project_name = projects.get(str(task.get("project_id", "")), "Privat")
    due = task.get("due") or {}
    due_date = due.get("date") if isinstance(due, dict) else None

    return {
        "id": f"todoist-{task.get('id')}",
        "title": str(task.get("content", "Ohne Titel")),
        "category": project_to_category(project_name),
        "priority": priority_to_planner(int(task.get("priority", 1))),
        "duration_minutes": duration,
        "notes": description,
        "due": due_date,
        "context_labels": extract_context_labels(labels),
        "customer_waiting": labels_contain(labels, "kunde", "kundenauftrag", "kunde-wartet"),
        "blocks_other_tasks": labels_contain(labels, "blockiert", "blocking"),
        "unclear": labels_contain(labels, "unklar"),
    }


def load_todoist_tasks() -> list[dict[str, Any]]:
    """Load open Todoist tasks as planner-compatible dictionaries, read-only."""
    token = get_api_token()
    if not token:
        raise TodoistReadOnlyError(f"{TODOIST_TOKEN_ENV} is not set")
    projects = fetch_projects(token)
    tasks = fetch_open_tasks(token)
    return [map_todoist_task(task, projects) for task in tasks]
