#!/usr/bin/env python3
"""Local dry-run planner using example JSON data only.

Version 1 intentionally does not read from or write to Google Calendar or
Todoist. This script loads local fixture files, treats weekly structures,
calendar entries and travel as blockers/contexts, scores tasks according to
rules.yaml, and prints a proposed plan for the selected day.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from todoist_client import TodoistReadOnlyError, load_todoist_tasks


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "rules.yaml"
PROMPT_PATH = ROOT / "planner_prompt.md"
TASKS_PATH = ROOT / "data" / "example_tasks.json"
CALENDAR_PATH = ROOT / "data" / "example_calendar.json"

PLANNING_START = time(9, 0)
PLANNING_END = time(23, 0)
MAX_PLANNED_PERCENT = 70
DEFAULT_ESTIMATED_DURATION_MINUTES = 30
LONG_TASK_THRESHOLD_MINUTES = 120
ADMIN_LATEST_END = time(22, 0)
BUCHHALTUNG_LATEST_END = time(22, 0)
WERKSTATT_DIAGNOSIS_LATEST_END = time(19, 0)
HOUSEHOLD_GAP_FILLER_LIMIT_MINUTES = 30
LARGE_FOCUS_BLOCK_MINUTES = 60
MAX_LARGE_FOCUS_BLOCKS = 3
MAX_MAIN_TASKS_PER_DAY = 6
MAX_MINI_TASKS_PER_DAY = 2
MINI_TASK_MAX_MINUTES = 15
TECHNICAL_BUFFER_MINUTES = 15
TRAVEL_MINUTES_MENGEN_AULENDORF = 60
PRIORITY_SCORE = {"P1": 100, "P2": 60, "P3": 30, "P4": 10}
PROTECTED_SMALL_CATEGORIES = {"Privat", "Haushalt"}
CREATIVE_CATEGORIES = {"ALEGRA", "Studio"}
TECHNICAL_CATEGORIES = {"Werkstatt", "LIVE"}
MENGEN = "Mengen"
AULENDORF = "Aulendorf"
WEEKDAY_KEYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


@dataclass(frozen=True)
class Task:
    """A local example task loaded from data/example_tasks.json."""

    id: str
    title: str
    category: str
    priority: str
    duration_minutes: int
    estimated: bool
    notes: str = ""
    due: str | None = None
    customer_waiting: bool = False
    blocks_other_tasks: bool = False
    unclear: bool = False
    context_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskScore:
    """Computed score and human-readable reasons for a task."""

    value: int
    reasons: list[str]

    @property
    def summary(self) -> str:
        return " + ".join(self.reasons)


@dataclass(frozen=True)
class CalendarEvent:
    """A fixed local example calendar event."""

    id: str
    title: str
    calendar: str
    start: datetime
    end: datetime
    location: str | None = None
    context: str | None = None


@dataclass(frozen=True)
class TimeWindow:
    """A free time window in the planning day, with place/context."""

    start: datetime
    end: datetime
    location: str
    context: str

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass(frozen=True)
class PlannedBlock:
    """A scheduled task block in the dry-run plan."""

    task: Task
    score: TaskScore
    start: datetime
    end: datetime
    location: str
    context: str


@dataclass(frozen=True)
class BufferBlock:
    """A generated transition buffer after demanding technical work."""

    title: str
    start: datetime
    end: datetime
    location: str
    context: str

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass(frozen=True)
class RejectedTask:
    """A task that could not or should not be scheduled."""

    task: Task
    reason: str
    score: TaskScore


@dataclass(frozen=True)
class PlanResult:
    """The complete dry-run planning result."""

    target_day: date
    events: list[CalendarEvent]
    free_windows: list[TimeWindow]
    planned_blocks: list[PlannedBlock]
    planned_buffers: list[BufferBlock]
    not_scheduled: list[RejectedTask]
    split_suggestions: list[RejectedTask]
    free_minutes: int
    capacity_minutes: int
    planned_minutes: int
    day_rating: str
    task_source: str


def load_json(path: Path) -> Any:
    """Load JSON from a local fixture file."""
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def parse_clock(target_day: date, value: str) -> datetime:
    """Parse a HH:MM value for the target day."""
    parsed_time = datetime.strptime(value, "%H:%M").time()
    return datetime.combine(target_day, parsed_time)


def parse_due_date(task: Task, target_day: date) -> date | None:
    """Parse a fixture due-date shorthand or ISO date."""
    if not task.due:
        return None
    if task.due == "today":
        return target_day - timedelta(days=1)
    if task.due == "tomorrow":
        return target_day
    if task.due == "in_3_days":
        return target_day + timedelta(days=3)
    return date.fromisoformat(task.due)


def task_from_mapping(item: dict[str, Any]) -> Task:
    """Convert a fixture/Todoist mapping to the internal Task dataclass."""
    raw_duration = item.get("duration_minutes")
    estimated = raw_duration is None
    duration = DEFAULT_ESTIMATED_DURATION_MINUTES if estimated else int(raw_duration)
    return Task(
        id=str(item["id"]),
        title=str(item["title"]),
        category=str(item["category"]),
        priority=str(item["priority"]),
        duration_minutes=duration,
        estimated=estimated,
        notes=str(item.get("notes", "")),
        due=item.get("due"),
        customer_waiting=bool(item.get("customer_waiting", False)),
        blocks_other_tasks=bool(item.get("blocks_other_tasks", False)),
        unclear=bool(item.get("unclear", False)),
        context_labels=tuple(str(label) for label in item.get("context_labels", [])),
    )


def load_json_tasks() -> list[Task]:
    """Load local example tasks and estimate missing durations."""
    data = load_json(TASKS_PATH)
    if not isinstance(data, list):
        raise ValueError(f"{TASKS_PATH} must contain a JSON list")
    return [task_from_mapping(item) for item in data]


def load_tasks(source: str) -> tuple[list[Task], str]:
    """Load tasks from the selected read-only source and return actual source used."""
    if source == "json":
        return load_json_tasks(), "json"
    try:
        todoist_items = load_todoist_tasks()
    except TodoistReadOnlyError as error:
        print(f"Todoist read-only source unavailable ({error}); falling back to local JSON tasks.")
        return load_json_tasks(), "json"
    return [task_from_mapping(item) for item in todoist_items], "todoist"


def weekday_key(target_day: date) -> str:
    """Return the lowercase English weekday key used by fixtures."""
    return WEEKDAY_KEYS[target_day.weekday()]


def calendar_config() -> dict[str, Any]:
    """Load local weekly structure and fixed-event fixture data."""
    data = load_json(CALENDAR_PATH)
    if isinstance(data, list):
        return {"weekly_structure": {}, "fixed_events": data}
    if not isinstance(data, dict):
        raise ValueError(f"{CALENDAR_PATH} must contain a JSON object or list")
    return data


def load_calendar_events(target_day: date) -> list[CalendarEvent]:
    """Load fixed example calendar events and generated travel blockers."""
    config = calendar_config()
    key = weekday_key(target_day)
    events: list[CalendarEvent] = []

    for item in config.get("fixed_events", []):
        if item.get("weekday") and item["weekday"] != key:
            continue
        events.append(
            CalendarEvent(
                id=str(item["id"]),
                title=str(item["title"]),
                calendar=str(item.get("calendar", "Beispiel")),
                start=parse_clock(target_day, str(item["start"])),
                end=parse_clock(target_day, str(item["end"])),
                location=item.get("location"),
                context=item.get("context"),
            )
        )

    events.extend(generated_travel_events(target_day, config))
    return sorted(events, key=lambda event: event.start)


def weekly_contexts(target_day: date, config: dict[str, Any]) -> list[TimeWindow]:
    """Return the typical weekly structure for the target day as context windows."""
    key = weekday_key(target_day)
    contexts = []
    for item in config.get("weekly_structure", {}).get(key, []):
        contexts.append(
            TimeWindow(
                start=parse_clock(target_day, str(item["start"])),
                end=parse_clock(target_day, str(item["end"])),
                location=str(item["location"]),
                context=str(item["context"]),
            )
        )
    return sorted(contexts, key=lambda window: window.start)


def generated_travel_events(target_day: date, config: dict[str, Any]) -> list[CalendarEvent]:
    """Generate 1-hour car travel blockers for Mengen/Aulendorf transitions."""
    contexts = weekly_contexts(target_day, config)
    if not contexts:
        return []

    events: list[CalendarEvent] = []
    day_end = datetime.combine(target_day, PLANNING_END)
    for index, current in enumerate(contexts):
        next_location = contexts[index + 1].location if index + 1 < len(contexts) else AULENDORF
        next_start = contexts[index + 1].start if index + 1 < len(contexts) else day_end
        if current.location == next_location:
            continue
        travel_start = current.end
        travel_end = min(current.end + timedelta(minutes=TRAVEL_MINUTES_MENGEN_AULENDORF), next_start)
        if travel_start < travel_end:
            events.append(
                CalendarEvent(
                    id=f"travel-{index}",
                    title=f"Fahrtzeit {current.location} → {next_location}",
                    calendar="Ortslogik",
                    start=travel_start,
                    end=travel_end,
                    location="Auto",
                    context="Fahrtzeit",
                )
            )
    return events


def context_at(target_day: date, contexts: list[TimeWindow], cursor: datetime) -> tuple[str, str]:
    """Return location/context active at a given time, defaulting to Aulendorf."""
    for context in contexts:
        if context.start <= cursor < context.end:
            return context.location, context.context
    if target_day.weekday() == 6:
        return AULENDORF, "Sonntag frei / leichte Aufgaben"
    if target_day.weekday() == 5:
        return AULENDORF, "Flex"
    if cursor.time() >= time(18, 0):
        return AULENDORF, "Abend Aulendorf"
    return AULENDORF, "Aulendorf/Home"


def merge_events(events: list[CalendarEvent]) -> list[tuple[datetime, datetime]]:
    """Merge overlapping fixed calendar and travel events into blocker windows."""
    blockers: list[tuple[datetime, datetime]] = []
    for event in events:
        if not blockers or event.start > blockers[-1][1]:
            blockers.append((event.start, event.end))
            continue
        previous_start, previous_end = blockers[-1]
        blockers[-1] = (previous_start, max(previous_end, event.end))
    return blockers


def split_context_window(window: TimeWindow, contexts: list[TimeWindow], target_day: date) -> list[TimeWindow]:
    """Split a window where weekly context boundaries change."""
    boundaries = {window.start, window.end}
    for context in contexts:
        if window.start < context.start < window.end:
            boundaries.add(context.start)
        if window.start < context.end < window.end:
            boundaries.add(context.end)
    sorted_boundaries = sorted(boundaries)

    split_windows: list[TimeWindow] = []
    for start, end in zip(sorted_boundaries, sorted_boundaries[1:]):
        location, context = context_at(target_day, contexts, start)
        split_windows.append(TimeWindow(start=start, end=end, location=location, context=context))
    return split_windows


def find_free_windows(target_day: date, events: list[CalendarEvent], contexts: list[TimeWindow]) -> list[TimeWindow]:
    """Find contextual free windows between 09:00 and 23:00 around blockers."""
    day_start = datetime.combine(target_day, PLANNING_START)
    day_end = datetime.combine(target_day, PLANNING_END)
    raw_windows: list[TimeWindow] = []
    cursor = day_start

    for blocker_start, blocker_end in merge_events(events):
        blocker_start = max(blocker_start, day_start)
        blocker_end = min(blocker_end, day_end)
        if blocker_end <= day_start or blocker_start >= day_end:
            continue
        if blocker_start > cursor:
            raw_windows.append(TimeWindow(cursor, blocker_start, *context_at(target_day, contexts, cursor)))
        cursor = max(cursor, blocker_end)

    if cursor < day_end:
        raw_windows.append(TimeWindow(cursor, day_end, *context_at(target_day, contexts, cursor)))

    free_windows: list[TimeWindow] = []
    for window in raw_windows:
        free_windows.extend(split_context_window(window, contexts, target_day))
    return [window for window in free_windows if window.minutes > 0]


def normalized_text(task: Task) -> str:
    """Return searchable task text."""
    return f"{task.title} {task.notes}".lower()


def has_any_marker(task: Task, markers: tuple[str, ...]) -> bool:
    """Return whether a task title or note contains any marker."""
    text = normalized_text(task)
    return any(marker in text for marker in markers)


def is_werkstatt_diagnosis(task: Task) -> bool:
    """Return whether the task is a Werkstatt diagnosis task."""
    diagnosis_markers = ("diagnose", "fehlersuche", "kurzschluss", "lokalisieren")
    return task.category == "Werkstatt" and has_any_marker(task, diagnosis_markers)


def is_admin_task(task: Task) -> bool:
    """Return whether a task is admin/form/accounting/authority work."""
    admin_markers = (
        "amt",
        "krankenkasse",
        "versicherung",
        "steuer",
        "frist",
        "formular",
        "buchhaltung",
        "lexware",
        "belege",
        "rechnung",
        "reverse-charge",
    )
    return task.category == "Buchhaltung" or has_any_marker(task, admin_markers)


def is_technical_task(task: Task) -> bool:
    """Return whether a task should be treated as technical work."""
    technical_markers = ("qu-24", "usb", "routing", "problem", "diagnose", "kurzschluss", "setup")
    if task.category == "Werkstatt":
        return True
    return has_any_marker(task, technical_markers)


def is_creative_task(task: Task) -> bool:
    """Return whether the task is a creative ALEGRA/Studio task."""
    return task.category in CREATIVE_CATEGORIES


def is_alegra_producing_task(task: Task) -> bool:
    """Return whether ALEGRA work fits Alex/Nico producing studio time."""
    markers = ("producing", "demo", "demos", "song", "songwriting", "recording", "arrangement", "mix", "release")
    return task.category == "ALEGRA" and has_any_marker(task, markers)


def is_general_studio_task(task: Task) -> bool:
    """Return whether the task is general Studio/MOMO rather than ALEGRA producing."""
    return task.category == "Studio" and not has_any_marker(task, ("alegra", "alex", "nico", "producing"))


def is_light_sunday_task(task: Task) -> bool:
    """Return whether a task is acceptable on a mostly-free Sunday."""
    if task.category in {"Haushalt", "Privat", "Buchhaltung", "Soundwerk"}:
        return True
    if task.category in {"ALEGRA", "Studio", "LIVE"}:
        return not is_technical_task(task) or "orga" in normalized_text(task) or "plan" in normalized_text(task)
    return False


def is_small_protected_task(task: Task) -> bool:
    """Return whether this task can satisfy the daily small personal/home block."""
    return task.category in PROTECTED_SMALL_CATEGORIES and task.duration_minutes <= 45


def is_focus_block(task: Task) -> bool:
    """Return whether this task counts as a larger focus block."""
    return task.duration_minutes >= LARGE_FOCUS_BLOCK_MINUTES and task.category != "Haushalt"


def preferred_location(task: Task) -> str:
    """Return the preferred location for a task."""
    if task.category in {"Werkstatt", "Soundwerk"}:
        return MENGEN
    if task.category == "LIVE" and is_technical_task(task):
        return MENGEN
    return AULENDORF


def score_task(task: Task, target_day: date) -> TaskScore:
    """Score a task according to Version 0.2 planning rules."""
    score = PRIORITY_SCORE.get(task.priority, 0)
    reasons = [task.priority]

    if has_any_marker(task, ("amt", "krankenkasse", "versicherung", "steuer", "frist", "formular")):
        score += 50
        reasons.append("Amt/Krankenkasse/Steuer/Frist")

    if task.customer_waiting:
        score += 40
        reasons.append("Kunde wartet/Kundenauftrag")

    if task.blocks_other_tasks:
        score += 30
        reasons.append("blockiert Folgeaufgaben")

    due_date = parse_due_date(task, target_day)
    if due_date and due_date <= target_day:
        score += 50
        reasons.append("Deadline morgen oder früher")
    elif due_date and due_date <= target_day + timedelta(days=3):
        score += 30
        reasons.append("Deadline innerhalb von 3 Tagen")

    if task.estimated:
        score -= 10
        reasons.append("geschätzt")

    if task.unclear:
        score -= 20
        reasons.append("unklar")

    return TaskScore(score, reasons)


def violates_time_rule(task: Task, start: datetime, end: datetime, location: str, context: str) -> str | None:
    """Return a hard rule violation for a candidate slot, if any."""
    if location != preferred_location(task) and not context.startswith("Flex"):
        return f"keine passende Ortslogik: bevorzugt {preferred_location(task)}, Fenster ist {location}."
    if task.category == "Buchhaltung" and end.time() > BUCHHALTUNG_LATEST_END:
        return "kollidiert mit Kategorie-Regeln: Buchhaltung/Admin nicht nach 22:00 Uhr."
    if is_admin_task(task) and end.time() > ADMIN_LATEST_END:
        return "kollidiert mit Kategorie-Regeln: Admin nicht nach 22:00 Uhr."
    if is_werkstatt_diagnosis(task) and end.time() > WERKSTATT_DIAGNOSIS_LATEST_END:
        return "kollidiert mit Kategorie-Regeln: Werkstatt-Diagnose nicht nach 19:00 Uhr."
    if start.date().weekday() == 6 and not is_light_sunday_task(task):
        return "kollidiert mit Kategorie-Regeln: Sonntag nur leichte Aufgaben/Regeneration."
    if (
        is_soundwerk_planning_task(task)
        and is_day_before_teaching(start.date())
        and not is_soundwerk_preparation_window(start, end)
        and not (is_soundwerk_pause_window(start, end) and is_small_soundwerk_pause_task(task))
        and not is_soundwerk_exception(task, start.date())
    ):
        return "Soundwerk-Vortag bewusst vermieden: Unterrichtsplanung bevorzugt direkt vor dem Unterricht."
    if context == "ALEGRA-Producing" and task.category != "ALEGRA":
        return "kollidiert mit Ortslogik: Alex/Nico-Donnerstagssession ist primär ALEGRA-Zeit."
    return None




def has_hard_deadline(task: Task, target_day: date) -> bool:
    """Return whether a task has a deadline today/tomorrow relative to the plan."""
    due_date = parse_due_date(task, target_day)
    return bool(due_date and due_date <= target_day)


def is_mini_task(task: Task) -> bool:
    """Return whether a task is a mini task for daily load limits."""
    return task.duration_minutes <= MINI_TASK_MAX_MINUTES


def count_main_tasks(planned_blocks: list[PlannedBlock]) -> int:
    """Count planned non-mini Todoist tasks."""
    return sum(1 for block in planned_blocks if not is_mini_task(block.task))


def count_mini_tasks(planned_blocks: list[PlannedBlock]) -> int:
    """Count planned mini Todoist tasks."""
    return sum(1 for block in planned_blocks if is_mini_task(block.task))


def needs_technical_buffer(task: Task) -> bool:
    """Return whether a task should get a generated reset/notes buffer."""
    return task.duration_minutes >= LARGE_FOCUS_BLOCK_MINUTES and (
        is_werkstatt_diagnosis(task) or (task.category in {"Werkstatt", "LIVE"} and is_technical_task(task))
    )






def is_soundwerk_task(task: Task) -> bool:
    """Return whether a task belongs to Soundwerk/teaching."""
    return task.category == "Soundwerk"


def is_soundwerk_planning_task(task: Task) -> bool:
    """Return whether a Soundwerk task is planning/prep/follow-up work."""
    markers = ("unterricht", "schüler", "material", "vorbereit", "nachbereit", "plan")
    return is_soundwerk_task(task) and has_any_marker(task, markers)


def is_soundwerk_exception(task: Task, target_day: date) -> bool:
    """Return whether Soundwerk work may be pulled to the previous day."""
    exception_markers = ("materialdruck", "dringend", "deadline", "frist")
    return (
        has_hard_deadline(task, target_day)
        or has_any_marker(task, exception_markers)
        or task.duration_minutes >= 90
    )


def is_day_before_teaching(target_day: date) -> bool:
    """Return whether the day is usually the day before teaching."""
    return target_day.weekday() in {0, 1}


def is_soundwerk_preparation_window(start: datetime, end: datetime) -> bool:
    """Return whether a slot is directly before Tuesday/Wednesday teaching."""
    return start.date().weekday() in {1, 2} and time(13, 0) <= start.time() and end.time() <= time(14, 0)


def is_soundwerk_pause_window(start: datetime, end: datetime) -> bool:
    """Return whether a slot is a Wednesday teaching pause for small notes/follow-up."""
    if start.date().weekday() != 2:
        return False
    return (time(15, 0) <= start.time() and end.time() <= time(15, 30)) or (
        time(16, 0) <= start.time() and end.time() <= time(17, 0)
    )


def is_small_soundwerk_pause_task(task: Task) -> bool:
    """Return whether a Soundwerk task is small enough for a teaching pause."""
    return is_soundwerk_planning_task(task) and task.duration_minutes <= 30

def estimated_planning_load(task: Task) -> int:
    """Return task minutes plus any generated technical buffer minutes."""
    if needs_technical_buffer(task):
        return task.duration_minutes + TECHNICAL_BUFFER_MINUTES
    return task.duration_minutes


def pending_urgent_admin_minutes(tasks: list[Task], target_day: date) -> int:
    """Return load that should be reserved for urgent evening admin work."""
    return sum(task.duration_minutes for task in tasks if is_admin_task(task) and has_hard_deadline(task, target_day))


def pending_urgent_admin_main_count(tasks: list[Task], target_day: date) -> int:
    """Return count of urgent admin main tasks still waiting."""
    return sum(
        1
        for task in tasks
        if is_admin_task(task) and has_hard_deadline(task, target_day) and not is_mini_task(task)
    )

def is_buchhaltung_main_task(task: Task) -> bool:
    """Return whether a bookkeeping task is broad enough to be the main task."""
    return task.category == "Buchhaltung" and has_any_marker(task, ("lexware", "ausgaben", "kategorisieren", "monatsabschluss"))


def is_buchhaltung_side_task(task: Task) -> bool:
    """Return whether a bookkeeping task is a smaller specialist side task."""
    return task.category == "Buchhaltung" and not is_buchhaltung_main_task(task)



def has_context_label(task: Task, label: str) -> bool:
    """Return whether a task has a Todoist context label hint."""
    return any(context.casefold() == label.casefold() for context in task.context_labels)


def context_label_bonus(task: Task, start: datetime, location: str, context: str) -> int:
    """Return soft bonuses from optional Todoist context labels."""
    bonus = 0
    if has_context_label(task, location):
        bonus += 15
    if location == AULENDORF and has_context_label(task, "Zuhause"):
        bonus += 10
    if has_context_label(task, "Abends") and start.time() >= time(18, 0):
        bonus += 15
    if has_context_label(task, "Werkstatt") and context == "Werkstatt":
        bonus += 10
    if has_context_label(task, "Studio") and "Studio" in context:
        bonus += 10
    return bonus

def context_preference_bonus(task: Task, start: datetime, location: str, context: str) -> int:
    """Return soft score bonus/penalty for weekly structure and place fit."""
    weekday = start.date().weekday()
    bonus = context_label_bonus(task, start, location, context)

    if location == preferred_location(task):
        bonus += 10
    if task.category == "Werkstatt" and context == "Werkstatt":
        bonus += 35
    if context == "Werkstatt" and (task.customer_waiting or is_werkstatt_diagnosis(task)):
        bonus += 25
    if task.category == "Soundwerk" and location == MENGEN:
        if is_soundwerk_preparation_window(start, start + timedelta(minutes=task.duration_minutes)):
            bonus += 60
        elif is_soundwerk_pause_window(start, start + timedelta(minutes=task.duration_minutes)) and is_small_soundwerk_pause_task(task):
            bonus += 35
        elif weekday in {1, 2}:
            bonus += 10
        elif is_day_before_teaching(start.date()) and is_soundwerk_planning_task(task) and not is_soundwerk_exception(task, start.date()):
            bonus -= 45
    if task.category == "ALEGRA" and (context == "ALEGRA-Producing" or weekday == 5):
        bonus += 35 if is_alegra_producing_task(task) else 15
    if is_general_studio_task(task) and (start.time() >= time(18, 0) or weekday in {5, 6}):
        bonus += 25
    if is_admin_task(task):
        if time(18, 0) <= start.time() <= time(21, 30) or weekday == 6:
            bonus += 60
        elif parse_due_date(task, start.date()) and parse_due_date(task, start.date()) <= start.date():
            bonus += 5
        else:
            bonus -= 20
    if is_buchhaltung_main_task(task):
        bonus += 35
    if is_buchhaltung_side_task(task):
        bonus -= 15
    if task.category == "Privat" and not is_admin_task(task) and time(18, 0) <= start.time() <= time(21, 30):
        bonus -= 20
    if task.category == "Haushalt" and (start.time() >= time(18, 0) or weekday in {5, 6}):
        bonus += 25
    if task.category == "LIVE" and is_technical_task(task) and context == "Werkstatt":
        bonus += 25
    if is_creative_task(task) and not (time(13, 0) <= start.time() <= time(21, 0) or weekday in {5, 6}):
        bonus -= 15
    return bonus


def task_sort_key(task: Task, score: TaskScore, window: TimeWindow) -> tuple[int, int, int, str]:
    """Sort candidate tasks by adjusted score, fit and title."""
    adjusted_score = score.value + context_preference_bonus(task, window.start, window.location, window.context)
    if task.category == "Haushalt" and window.minutes > HOUSEHOLD_GAP_FILLER_LIMIT_MINUTES:
        adjusted_score -= 30
    if window.minutes <= HOUSEHOLD_GAP_FILLER_LIMIT_MINUTES and task.category == "Haushalt":
        adjusted_score += 20
    return (-adjusted_score, -task.duration_minutes, PRIORITY_SCORE.get(task.priority, 0), task.title)


def choose_task(
    tasks: list[Task],
    scores: dict[str, TaskScore],
    window: TimeWindow,
    cursor: datetime,
    remaining_capacity: int,
    focus_blocks_used: int,
    needs_small_protected_block: bool,
    planned_blocks: list[PlannedBlock],
    target_day: date,
) -> Task | None:
    """Choose the best task that fits the current contextual slot and active rules."""
    active_window = TimeWindow(cursor, window.end, window.location, window.context)
    fitting_tasks = []
    for task in tasks:
        block_end = cursor + timedelta(minutes=task.duration_minutes)
        if task.duration_minutes > active_window.minutes:
            continue
        if task.duration_minutes > remaining_capacity:
            continue
        if is_focus_block(task) and focus_blocks_used >= MAX_LARGE_FOCUS_BLOCKS and not has_hard_deadline(task, target_day):
            continue
        urgent_admin_reserve = pending_urgent_admin_minutes(tasks, target_day)
        before_evening_home = window.location != AULENDORF or cursor.time() < time(18, 0)
        if urgent_admin_reserve and before_evening_home and not is_admin_task(task):
            if remaining_capacity - estimated_planning_load(task) < urgent_admin_reserve:
                continue
            projected_main_tasks = count_main_tasks(planned_blocks)
            if not is_mini_task(task):
                projected_main_tasks += 1
            projected_main_tasks += pending_urgent_admin_main_count(tasks, target_day)
            if projected_main_tasks > MAX_MAIN_TASKS_PER_DAY:
                continue
        if count_main_tasks(planned_blocks) >= MAX_MAIN_TASKS_PER_DAY and not is_mini_task(task) and not has_hard_deadline(task, target_day):
            continue
        if is_mini_task(task) and count_mini_tasks(planned_blocks) >= MAX_MINI_TASKS_PER_DAY:
            continue
        if is_mini_task(task) and remaining_capacity < 60:
            continue
        pending_main_bookkeeping = [candidate for candidate in tasks if is_buchhaltung_main_task(candidate)]
        if is_buchhaltung_side_task(task) and pending_main_bookkeeping:
            main_minutes = min(candidate.duration_minutes for candidate in pending_main_bookkeeping)
            if remaining_capacity < task.duration_minutes + main_minutes:
                continue
        if violates_time_rule(task, cursor, block_end, window.location, window.context):
            continue
        fitting_tasks.append(task)

    if not fitting_tasks:
        return None

    protected_candidates = [task for task in fitting_tasks if is_small_protected_task(task)]
    urgent_admin_candidates = [
        task for task in fitting_tasks if is_admin_task(task) and has_hard_deadline(task, target_day)
    ]
    if (
        needs_small_protected_block
        and protected_candidates
        and not urgent_admin_candidates
        and (active_window.minutes <= HOUSEHOLD_GAP_FILLER_LIMIT_MINUTES or cursor.time() >= time(16, 0))
    ):
        return sorted(protected_candidates, key=lambda task: task_sort_key(task, scores[task.id], active_window))[0]

    if active_window.minutes <= HOUSEHOLD_GAP_FILLER_LIMIT_MINUTES:
        household_tasks = [task for task in fitting_tasks if task.category == "Haushalt"]
        if household_tasks:
            return sorted(household_tasks, key=lambda task: (-scores[task.id].value, task.duration_minutes))[0]

    return sorted(fitting_tasks, key=lambda task: task_sort_key(task, scores[task.id], active_window))[0]


def build_plan(tasks: list[Task], events: list[CalendarEvent], contexts: list[TimeWindow], target_day: date, task_source: str) -> PlanResult:
    """Build a dry-run day plan from local example tasks, contexts and events."""
    free_windows = find_free_windows(target_day, events, contexts)
    free_minutes = sum(window.minutes for window in free_windows)
    capacity_minutes = int(free_minutes * MAX_PLANNED_PERCENT / 100)
    scores = {task.id: score_task(task, target_day) for task in tasks}

    split_suggestions = [
        RejectedTask(
            task,
            f"zu groß, bitte zerlegen: Dauer {task.duration_minutes} Minuten ist über {LONG_TASK_THRESHOLD_MINUTES} Minuten.",
            scores[task.id],
        )
        for task in tasks
        if task.duration_minutes > LONG_TASK_THRESHOLD_MINUTES
    ]
    remaining_tasks = [task for task in tasks if task.duration_minutes <= LONG_TASK_THRESHOLD_MINUTES]

    planned_blocks: list[PlannedBlock] = []
    planned_buffers: list[BufferBlock] = []
    planned_minutes = 0
    focus_blocks_used = 0

    for window in free_windows:
        cursor = window.start
        while cursor < window.end and planned_minutes < capacity_minutes:
            remaining_capacity = capacity_minutes - planned_minutes
            needs_small_protected_block = has_unplanned_small_protected_task(remaining_tasks) and not has_planned_small_protected_block(planned_blocks)
            task = choose_task(
                remaining_tasks,
                scores,
                window,
                cursor,
                remaining_capacity,
                focus_blocks_used,
                needs_small_protected_block,
                planned_blocks,
                target_day,
            )
            if task is None:
                break
            block_end = cursor + timedelta(minutes=task.duration_minutes)
            planned_blocks.append(
                PlannedBlock(
                    task=task,
                    score=scores[task.id],
                    start=cursor,
                    end=block_end,
                    location=window.location,
                    context=window.context,
                )
            )
            planned_minutes += task.duration_minutes
            if is_focus_block(task):
                focus_blocks_used += 1
            remaining_tasks.remove(task)

            buffer_end = block_end + timedelta(minutes=TECHNICAL_BUFFER_MINUTES)
            if (
                needs_technical_buffer(task)
                and buffer_end <= window.end
                and planned_minutes + TECHNICAL_BUFFER_MINUTES <= capacity_minutes
            ):
                planned_buffers.append(
                    BufferBlock(
                        title="Puffer / Notizen / Arbeitsplatz resetten",
                        start=block_end,
                        end=buffer_end,
                        location=window.location,
                        context=window.context,
                    )
                )
                planned_minutes += TECHNICAL_BUFFER_MINUTES
                cursor = buffer_end
            else:
                cursor = block_end

    not_scheduled = [
        RejectedTask(
            task,
            reason_for_unscheduled(
                task,
                free_windows,
                capacity_minutes - planned_minutes,
                focus_blocks_used,
                scores,
                planned_blocks,
            ),
            scores[task.id],
        )
        for task in remaining_tasks
    ]

    return PlanResult(
        target_day=target_day,
        events=events,
        free_windows=free_windows,
        planned_blocks=planned_blocks,
        planned_buffers=planned_buffers,
        not_scheduled=not_scheduled,
        split_suggestions=split_suggestions,
        free_minutes=free_minutes,
        capacity_minutes=capacity_minutes,
        planned_minutes=planned_minutes,
        day_rating=rate_day(planned_minutes, capacity_minutes, planned_blocks),
        task_source=task_source,
    )


def rate_day(planned_minutes: int, capacity_minutes: int, planned_blocks: list[PlannedBlock]) -> str:
    """Return a simple day load rating."""
    if capacity_minutes == 0:
        return "realistisch"
    load_ratio = planned_minutes / capacity_minutes
    main_tasks = count_main_tasks(planned_blocks)
    if load_ratio <= 0.78 and main_tasks <= 5:
        return "realistisch"
    if load_ratio <= 0.95 and main_tasks <= MAX_MAIN_TASKS_PER_DAY + 1:
        return "sportlich"
    return "zu voll"


def has_unplanned_small_protected_task(tasks: list[Task]) -> bool:
    """Return whether a small private/health/household task is still available."""
    return any(is_small_protected_task(task) for task in tasks)


def has_planned_small_protected_block(planned_blocks: list[PlannedBlock]) -> bool:
    """Return whether a small private/health/household block has been planned."""
    return any(is_small_protected_task(block.task) for block in planned_blocks)


def reason_for_unscheduled(
    task: Task,
    free_windows: list[TimeWindow],
    remaining_capacity: int,
    focus_blocks_used: int,
    scores: dict[str, TaskScore],
    planned_blocks: list[PlannedBlock],
) -> str:
    """Explain why a task was not scheduled."""
    possible_violations = []
    has_large_enough_window = False
    has_location_match = False
    for window in free_windows:
        if task.duration_minutes <= window.minutes:
            has_large_enough_window = True
            if window.location == preferred_location(task) or window.context.startswith("Flex"):
                has_location_match = True
            violation = violates_time_rule(task, window.start, window.start + timedelta(minutes=task.duration_minutes), window.location, window.context)
            if violation:
                possible_violations.append(violation)

    if possible_violations and not has_valid_time_window(task, free_windows):
        return possible_violations[0]
    if not has_location_match:
        return f"nicht passender Ort/Kontext: bevorzugt {preferred_location(task)}."
    if is_buchhaltung_side_task(task) and any(is_buchhaltung_main_task(block.task) for block in planned_blocks):
        return "Nebenaufgabe, Hauptaufgabe hat Vorrang."
    if count_main_tasks(planned_blocks) >= MAX_MAIN_TASKS_PER_DAY and not is_mini_task(task):
        return "Hauptaufgabenlimit erreicht; bewusst auf Folgetag verschoben."
    if is_focus_block(task) and focus_blocks_used >= MAX_LARGE_FOCUS_BLOCKS:
        return "kollidiert mit Kategorie-Regeln: maximal 3 größere Fokusblöcke pro Tag."

    lowest_planned_score = min((block.score.value for block in planned_blocks), default=0)
    if scores[task.id].value < lowest_planned_score:
        return f"niedrigerer Score als geplante Aufgabe: Score {scores[task.id].value}."
    if task.duration_minutes > remaining_capacity:
        return "Puffergrenze erreicht: Das 70-Prozent-Limit der freien Zeit ist ausgeschöpft."
    if not has_large_enough_window:
        return "keine passende Tageszeit: kein ausreichend großes freies Zeitfenster verfügbar."
    return "bewusst auf Folgetag verschoben."


def has_valid_time_window(task: Task, free_windows: list[TimeWindow]) -> bool:
    """Return whether a task has any free window that does not violate hard rules."""
    for window in free_windows:
        if task.duration_minutes <= window.minutes:
            end = window.start + timedelta(minutes=task.duration_minutes)
            if not violates_time_rule(task, window.start, end, window.location, window.context):
                return True
    return False


def format_time(value: datetime) -> str:
    """Format a datetime as HH:MM."""
    return value.strftime("%H:%M")


def format_duration(task: Task) -> str:
    """Format task duration and estimation marker."""
    suffix = " (geschätzt)" if task.estimated else ""
    return f"{task.duration_minutes} Min.{suffix}"


def format_scored_task(task: Task, score: TaskScore, location: str | None = None) -> str:
    """Format task metadata including location, score and score reasons."""
    location_part = f", {location}" if location else ""
    return f"{task.category}{location_part}, {task.priority}, {format_duration(task)}, Score {score.value} – {score.summary}"


def print_plan(result: PlanResult) -> None:
    """Print the dry-run day plan as German Markdown text."""
    print("# Nico Day Planner – lokaler Dry-Run")
    print()
    if result.task_source == "todoist":
        print("Todoist wurde read-only als Aufgabenquelle gelesen; Google-Kalender-API wurde nicht verwendet.")
    else:
        print("Keine Todoist- oder Google-Kalender-API wurde gelesen oder beschrieben.")
    print(f"Zieldatum: {result.target_day.isoformat()} ({weekday_key(result.target_day)})")
    print(f"Regeln: {RULES_PATH.name} | Prompt: {PROMPT_PATH.name}")
    print()

    print("## Annahmen")
    print(f"- Planungsfenster: {PLANNING_START.strftime('%H:%M')}–{PLANNING_END.strftime('%H:%M')} Uhr.")
    print(f"- Freie Zeit: {result.free_minutes} Minuten; davon maximal {MAX_PLANNED_PERCENT}% = {result.capacity_minutes} Minuten verplant.")
    print(f"- Tatsächlich verplant: {result.planned_minutes} Minuten.")
    print(f"- Fahrtzeit Mengen ↔ Aulendorf: {TRAVEL_MINUTES_MENGEN_AULENDORF} Minuten, standardmäßig blockiert wegen Auto.")
    print(f"- Maximal {MAX_MAIN_TASKS_PER_DAY} Hauptaufgaben und {MAX_MINI_TASKS_PER_DAY} Mini-Tasks pro Tag.")
    print(f"- Maximal {MAX_LARGE_FOCUS_BLOCKS} größere Fokusblöcke pro Tag.")
    print(f"- Nach technischen Diagnose-/Werkstattblöcken ab 60 Minuten werden {TECHNICAL_BUFFER_MINUTES} Minuten Reset-Puffer eingeplant.")
    print("- Aufgaben ohne Dauer werden mit 30 Minuten geschätzt und mit -10 Score bewertet.")
    print()

    print("## Feste Termine und Fahrtzeiten")
    if not result.events:
        print("- Keine festen Termine oder Fahrtzeiten im Planungsfenster.")
    for event in result.events:
        print(f"- {format_time(event.start)}–{format_time(event.end)}: {event.title} ({event.calendar})")
    print()

    print("## Freie Zeitfenster")
    for window in result.free_windows:
        print(f"- {format_time(window.start)}–{format_time(window.end)} ({window.minutes} Min., {window.location}, {window.context})")
    print()

    print("## Vorschlag Tagesplan")
    schedule_items = sorted(
        [(block.start, "task", block) for block in result.planned_blocks]
        + [(buffer.start, "buffer", buffer) for buffer in result.planned_buffers],
        key=lambda item: item[0],
    )
    if not schedule_items:
        print("- Keine Aufgaben eingeplant.")
    for _, item_type, item in schedule_items:
        if item_type == "buffer":
            print(
                f"- {format_time(item.start)}–{format_time(item.end)}: {item.title} "
                f"[{item.location}; Kontext: {item.context}]"
            )
            continue
        task = item.task
        print(
            f"- {format_time(item.start)}–{format_time(item.end)}: {task.title} "
            f"[{format_scored_task(task, item.score, item.location)}; Kontext: {item.context}]"
        )
    print()

    print("## Tagesbewertung")
    print(f"- {result.day_rating}")
    print()

    print("## Puffer")
    buffer_minutes = result.free_minutes - result.planned_minutes
    generated_buffer_minutes = sum(buffer.minutes for buffer in result.planned_buffers)
    print(f"- Generierte Diagnose-/Reset-Puffer: {generated_buffer_minutes} Minuten.")
    print(f"- Nicht aktiv verplante freie Zeit: {buffer_minutes} Minuten.")
    print("- Diese Zeit bleibt als Puffer, Übergang, Pause oder Reserve frei.")
    print()

    print("## Nicht eingeplant")
    if not result.not_scheduled:
        print("- Keine weiteren Aufgaben.")
    for rejected in sorted(result.not_scheduled, key=lambda rejected: (-rejected.score.value, rejected.task.title)):
        print(
            f"- {rejected.task.title} [{format_scored_task(rejected.task, rejected.score)}]: "
            f"{rejected.reason}"
        )
    print()

    print("## Vorschläge zur Zerlegung")
    if not result.split_suggestions:
        print("- Keine Aufgaben über 120 Minuten.")
    for rejected in sorted(result.split_suggestions, key=lambda rejected: (-rejected.score.value, rejected.task.title)):
        task = rejected.task
        print(f"- {task.title} [{format_scored_task(task, rejected.score)}]: {rejected.reason}")
        print("  Vorschlag: in mehrere Blöcke von maximal 60–90 Minuten aufteilen.")


def target_day_from_env() -> date:
    """Return NICO_PLAN_DATE or tomorrow for local testing."""
    configured = os.getenv("NICO_PLAN_DATE")
    if configured:
        return date.fromisoformat(configured)
    return date.today() + timedelta(days=1)


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the dry-run planner."""
    parser = argparse.ArgumentParser(description="Run the Nico Day Planner dry-run.")
    parser.add_argument(
        "--source",
        choices=("json", "todoist"),
        default="json",
        help="Task source to read. Default: json. Todoist is read-only and falls back to JSON if unavailable.",
    )
    return parser.parse_args()


def main() -> None:
    """Run the local dry-run planner."""
    args = parse_args()
    target_day = target_day_from_env()
    config = calendar_config()
    tasks, actual_source = load_tasks(args.source)
    contexts = weekly_contexts(target_day, config)
    events = load_calendar_events(target_day)
    result = build_plan(tasks, events, contexts, target_day, actual_source)
    print_plan(result)


if __name__ == "__main__":
    main()
