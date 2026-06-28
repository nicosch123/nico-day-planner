#!/usr/bin/env python3
"""Local dry-run planner for Nico Day Planner v0.6-calendar.

Default task source is JSON. Todoist can be used as a read-only source with
--source todoist. Calendar source defaults to local JSON; Google Calendar can
be used read-only with --calendar-source google. If external credentials are
missing, the script falls back to local JSON examples.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from google_calendar_client import (
    AUTO_EVENT_MARKER,
    CALENDAR_ID_ENV_VAR,
    DEFAULT_CALENDAR_ID,
    GoogleCalendarReadError,
    create_calendar_event,
    delete_auto_events_for_date,
    load_calendar_events_for_date,
)
from todoist_client import TodoistReadError, load_todoist_tasks_from_env

ROOT = Path(__file__).resolve().parents[1]
TASKS_PATH = ROOT / "data" / "example_tasks.json"
CALENDAR_PATH = ROOT / "data" / "example_calendar.json"

DAY_START = time(9, 0)
DAY_END = time(23, 0)
MAX_PLANNED_PERCENT = 70
LONG_TASK_THRESHOLD_MINUTES = 120
DEFAULT_ESTIMATED_DURATION_MINUTES = 30
DEFAULT_BUFFER_MINUTES = 15
RESET_BUFFER_MINUTES = 15
MANUAL_EVENT_PRE_BUFFER_MINUTES = 15
LUNCH_BREAK_START = time(12, 0)
LUNCH_BREAK_END = time(13, 0)
LESSON_GAP_MINUTES = 45
LESSON_GAP_TASK_MAX_MINUTES = 60
LESSON_GAP_PREFERRED_MAX_MINUTES = 45
LESSON_GAP_CATEGORIES = {"Soundwerk", "Buchhaltung", "Privat", "Haushalt", "ALEGRA"}
MAX_MAIN_TASKS = 6
MAX_MINI_TASKS = 2
MINI_TASK_MAX_MINUTES = 15
WERKSTATT_GAP_MINUTES = 30
WERKSTATT_PARTIAL_BLOCK_MINUTES = 60
MAX_PARTIAL_BLOCKS_PER_DAY = 2
EVENING_START = time(17, 0)
LATE_EVENING_START = time(19, 0)
LARGE_EVENING_TASK_MINUTES = 60
MAX_LARGE_EVENING_BLOCKS = 1
MONDAY_EVENING_AUTO_LATEST_END = time(20, 0)
WERKSTATT_SMALL_KEYWORDS = (
    "diagnose vorbereiten",
    "fehler provozieren",
    "ersatzteile",
    "kundenupdate",
    "sichtprüfung",
    "messnotizen",
    "arbeitsplatz aufräumen",
    "notizen",
    "gerät öffnen",
)
BUCHHALTUNG_LATEST_END = time(21, 0)
DEFAULT_CALENDAR_TIME_ZONE = "Europe/Berlin"
WERKSTATT_DIAGNOSIS_LATEST_END = time(18, 0)
WERKSTATT_WINDOWS: dict[int, tuple[time, time]] = {
    0: (time(9, 0), time(17, 0)),
    1: (time(9, 0), time(14, 0)),
    2: (time(9, 0), time(14, 0)),
    3: (time(9, 0), time(12, 0)),
    4: (time(9, 0), time(17, 0)),
}
PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}

WEEKLY_STRUCTURE: dict[int, list[dict[str, Any]]] = {
    0: [
        {"title": "Werkstatt Mengen", "start": "09:00", "end": "17:00", "location": "Mengen", "categories": ["Werkstatt"]},
    ],
    1: [
        {"title": "Werkstatt", "start": "09:00", "end": "14:00", "location": "Mengen", "categories": ["Werkstatt"]},
        {"title": "Soundwerk Unterricht", "start": "14:00", "end": "16:00", "location": "Aulendorf", "categories": ["Soundwerk"]},
    ],
    2: [
        {"title": "Werkstatt", "start": "09:00", "end": "14:00", "location": "Mengen", "categories": ["Werkstatt"]},
        {"title": "Soundwerk Unterricht", "start": "14:00", "end": "18:30", "location": "Aulendorf", "categories": ["Soundwerk"]},
    ],
    3: [
        {"title": "Werkstatt", "start": "09:00", "end": "12:00", "location": "Mengen", "categories": ["Werkstatt"]},
        {"title": "ALEGRA/Producing Alex/Nico im Studio Aulendorf", "start": "14:00", "end": "18:00", "location": "Aulendorf", "categories": ["ALEGRA", "Studio"]},
        {"title": "ALEGRA/Producing Alex/Nico im Studio Aulendorf", "start": "20:00", "end": "23:00", "location": "Aulendorf", "categories": ["ALEGRA", "Studio"]},
    ],
    4: [
        {"title": "Werkstatt", "start": "09:00", "end": "17:00", "location": "Mengen", "categories": ["Werkstatt"]},
    ],
    5: [],
    6: [
        {"title": "Frei / Haushalt / Büro", "start": "09:00", "end": "23:00", "location": "Zuhause", "categories": ["Haushalt", "Buchhaltung", "Privat"], "soft": True},
    ],
}


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    category: str
    priority: str
    duration_minutes: int
    estimated: bool = False
    duration_source: str = "estimated"
    notes: str = ""


@dataclass(frozen=True)
class Block:
    id: str
    title: str
    start: datetime
    end: datetime
    source: str
    categories: tuple[str, ...] = ()
    location: str = ""
    soft: bool = False

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass(frozen=True)
class TimeWindow:
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass(frozen=True)
class PlannedBlock:
    task: Task
    start: datetime
    end: datetime
    buffer_after_minutes: int = 0


@dataclass(frozen=True)
class RejectedTask:
    task: Task
    reason: str


@dataclass
class PlanResult:
    target_day: date
    source_status: str
    fixed_blocks: list[Block]
    free_windows: list[TimeWindow]
    planned_blocks: list[PlannedBlock]
    not_scheduled: list[RejectedTask]
    split_suggestions: list[RejectedTask]
    capacity_minutes: int
    planned_minutes: int
    source: str
    calendar_source: str
    calendar_status: str
    calendar_fallback_used: bool = False
    fallback_used: bool = False
    warnings: list[str] = field(default_factory=list)
    source_details: tuple[str, ...] = field(default_factory=tuple)
    calendar_details: tuple[str, ...] = field(default_factory=tuple)
    calendar_write_enabled: bool = False
    calendar_write_blocked_warning: str = ""
    calendar_write_target_id: str = DEFAULT_CALENDAR_ID
    calendar_created_events: int = 0
    calendar_deleted_events: int = 0
    validation_errors: list[str] = field(default_factory=list)
    workshop_diagnostics: list[str] = field(default_factory=list)
    load_diagnostics: list[str] = field(default_factory=list)


def parse_hhmm(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def at_day(target_day: date, hhmm: str) -> datetime:
    return datetime.combine(target_day, parse_hhmm(hhmm))


def fmt(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def normalize_task(raw: dict[str, Any]) -> Task:
    raw_duration = raw.get("duration_minutes")
    estimated = raw_duration is None or raw.get("duration_source") == "estimated"
    duration = DEFAULT_ESTIMATED_DURATION_MINUTES if raw_duration is None else int(raw_duration)
    duration_source = str(raw.get("duration_source", "estimated" if estimated else "explicit"))
    return Task(
        id=str(raw.get("id", "unknown")),
        title=str(raw.get("title", "Ohne Titel")),
        category=str(raw.get("category", "Privat")),
        priority=str(raw.get("priority", "P4")),
        duration_minutes=duration,
        estimated=estimated,
        duration_source=duration_source,
        notes=str(raw.get("notes", "")),
    )


def load_json_tasks() -> list[Task]:
    payload = load_json(TASKS_PATH)
    if not isinstance(payload, list):
        raise ValueError(f"{TASKS_PATH} muss eine JSON-Liste enthalten.")
    return [normalize_task(item) for item in payload if isinstance(item, dict)]


def load_json_calendar_blocks(target_day: date) -> list[Block]:
    payload = load_json(CALENDAR_PATH)
    if not isinstance(payload, list):
        raise ValueError(f"{CALENDAR_PATH} muss eine JSON-Liste enthalten.")

    blocks: list[Block] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        blocks.append(
            Block(
                id=str(item.get("id", "calendar-unknown")),
                title=str(item.get("title", "Fester Termin")),
                start=at_day(target_day, str(item["start"])),
                end=at_day(target_day, str(item["end"])),
                source="Lokales JSON-Kalenderbeispiel",
                categories=(str(item.get("calendar", "Privat")),),
            )
        )
    return blocks




def normalize_calendar_event(raw: dict[str, Any]) -> Block:
    return Block(
        id=str(raw.get("id", "google-calendar-unknown")),
        title=str(raw.get("title", "Termin")),
        start=datetime.fromisoformat(str(raw["start"])),
        end=datetime.fromisoformat(str(raw["end"])),
        source=str(raw.get("source", "Google Calendar")),
        location=str(raw.get("location", "")),
    )


def load_calendar_blocks_for_source(calendar_source: str, target_day: date) -> tuple[list[Block], str, bool, list[str], tuple[str, ...]]:
    warnings: list[str] = []
    if calendar_source == "json":
        return load_json_calendar_blocks(target_day), "Kalender JSON: lokale Beispieltermine geladen.", False, warnings, ()

    try:
        result = load_calendar_events_for_date(target_day)
    except GoogleCalendarReadError as exc:
        warnings.append(f"Google Calendar konnte nicht gelesen werden ({exc}) – verwende lokale JSON-Kalenderdaten.")
        return load_json_calendar_blocks(target_day), warnings[-1], True, warnings, ()

    if result.used_fallback:
        return load_json_calendar_blocks(target_day), result.status, True, warnings, result.status_details
    return [normalize_calendar_event(event) for event in result.events], result.status, False, warnings, result.status_details

def weekly_blocks(target_day: date) -> list[Block]:
    blocks: list[Block] = []
    for index, item in enumerate(WEEKLY_STRUCTURE[target_day.weekday()]):
        if item.get("soft"):
            continue
        blocks.append(
            Block(
                id=f"weekly-{target_day.weekday()}-{index}",
                title=item["title"],
                start=at_day(target_day, item["start"]),
                end=at_day(target_day, item["end"]),
                source="Wochenstruktur",
                categories=tuple(item.get("categories", ())),
                location=str(item.get("location", "")),
            )
        )
    return blocks


def lunch_break_block(target_day: date) -> Block:
    return Block(
        id="daily-lunch-break",
        title="Mittagspause",
        start=datetime.combine(target_day, LUNCH_BREAK_START),
        end=datetime.combine(target_day, LUNCH_BREAK_END),
        source="Tagesregel",
        categories=("Pause",),
    )


def travel_blocks(blocks: list[Block]) -> list[Block]:
    sorted_blocks = sorted([block for block in blocks if block.location], key=lambda block: block.start)
    travel: list[Block] = []
    for previous, current in zip(sorted_blocks, sorted_blocks[1:]):
        locations = {previous.location, current.location}
        if locations == {"Mengen", "Aulendorf"}:
            end = current.start
            start = end - timedelta(minutes=60)
            travel.append(
                Block(
                    id=f"travel-{previous.id}-{current.id}",
                    title=f"Fahrt {previous.location} ↔ {current.location}",
                    start=start,
                    end=end,
                    source="Fahrtregel",
                    location="Unterwegs",
                )
            )
    return travel


def merge_overlapping(blocks: list[Block]) -> list[Block]:
    return sorted(blocks, key=lambda block: (block.start, block.end, block.title))


def is_werkstatt_availability_block(block: Block) -> bool:
    return block.source == "Wochenstruktur" and "Werkstatt" in block.categories


def planning_blockers(blocks: list[Block]) -> list[Block]:
    """Return hard blockers for task placement.

    Weekly Werkstatt entries describe the preferred Werkstatt availability window.
    They must restrict Werkstatt placement, but should not force Werkstatt tasks into
    the evening by blocking the whole workshop day. Calendar events and all other
    fixed weekly structure entries remain hard blockers.
    """
    return [block for block in blocks if not is_werkstatt_availability_block(block)]


def buffered_planning_blockers(blocks: list[Block]) -> list[Block]:
    """Return blockers expanded by the required pre-buffer before fixed events."""
    buffered: list[Block] = []
    for block in planning_blockers(blocks):
        start = block.start
        if block.source != "Tagesregel":
            start -= timedelta(minutes=MANUAL_EVENT_PRE_BUFFER_MINUTES)
        buffered.append(
            Block(
                id=f"buffered-{block.id}",
                title=block.title,
                start=start,
                end=block.end,
                source=block.source,
                categories=block.categories,
                location=block.location,
                soft=block.soft,
            )
        )
    return buffered


def werkstatt_window_for_day(target_day: date) -> TimeWindow | None:
    bounds = WERKSTATT_WINDOWS.get(target_day.weekday())
    if bounds is None:
        return None
    start, end = bounds
    return TimeWindow(datetime.combine(target_day, start), datetime.combine(target_day, end))


def overlaps(start: datetime, end: datetime, block_start: datetime, block_end: datetime) -> bool:
    return start < block_end and end > block_start


def find_free_windows(target_day: date, blocks: list[Block]) -> list[TimeWindow]:
    day_start = datetime.combine(target_day, DAY_START)
    day_end = datetime.combine(target_day, DAY_END)
    cursor = day_start
    windows: list[TimeWindow] = []

    for block in merge_overlapping(blocks):
        start = max(block.start, day_start)
        end = min(block.end, day_end)
        if end <= day_start or start >= day_end:
            continue
        if start > cursor:
            windows.append(TimeWindow(cursor, start))
        cursor = max(cursor, end)

    if cursor < day_end:
        windows.append(TimeWindow(cursor, day_end))
    return windows


def is_werkstatt_diagnosis(task: Task) -> bool:
    return task.category == "Werkstatt" and "diagnose" in task.title.lower()


def is_mini_task(task: Task) -> bool:
    return task.duration_minutes <= MINI_TASK_MAX_MINUTES


def soundwerk_lesson_blocks(blocks: list[Block]) -> list[Block]:
    return [block for block in blocks if "Soundwerk" in block.categories]


def is_lesson_gap(start: datetime, end: datetime, blocks: list[Block]) -> bool:
    hard_blocks = sorted(planning_blockers(blocks), key=lambda block: block.start)
    previous_block = next((block for block in reversed(hard_blocks) if block.end <= start), None)
    next_block = next((block for block in hard_blocks if block.start >= end), None)
    if previous_block is None or next_block is None:
        return False
    gap_minutes = int((next_block.start - previous_block.end).total_seconds() // 60)
    return (
        previous_block.source == "Google Calendar"
        and next_block.source == "Google Calendar"
        and gap_minutes >= LESSON_GAP_MINUTES
    )


def is_lesson_gap_task(task: Task) -> bool:
    if task.category not in LESSON_GAP_CATEGORIES:
        return False
    if task.duration_minutes > LESSON_GAP_TASK_MAX_MINUTES:
        return False
    return True


def in_hour_before_soundwerk(task: Task, start: datetime, end: datetime, blocks: list[Block]) -> bool:
    if task.category != "Soundwerk":
        return True
    for lesson in soundwerk_lesson_blocks(blocks):
        prep_start = lesson.start - timedelta(minutes=60)
        if start >= prep_start and end <= lesson.start:
            return True
    return False


def violates_time_rule(task: Task, start: datetime, end: datetime, blocks: list[Block]) -> str | None:
    if task.category == "Werkstatt":
        werkstatt_window = werkstatt_window_for_day(start.date())
        if werkstatt_window is None or start < werkstatt_window.start or end > werkstatt_window.end:
            return "Werkstatt-Aufgaben werden nur in der bevorzugten Werkstattzeit geplant."
    elif (
        werkstatt_window_for_day(start.date())
        and overlaps(start, end, werkstatt_window_for_day(start.date()).start, werkstatt_window_for_day(start.date()).end)
        and not (is_lesson_gap(start, end, blocks) and is_lesson_gap_task(task))
    ):
        return "Nicht-Werkstatt-Aufgaben werden nicht in die bevorzugte Werkstattzeit gestreut."
    if task.category == "Buchhaltung" and end.time() > BUCHHALTUNG_LATEST_END:
        return "Buchhaltung/Admin/Krankenkasse wird nicht nach 21:00 Uhr geplant."
    if is_werkstatt_diagnosis(task) and end.time() > WERKSTATT_DIAGNOSIS_LATEST_END:
        return "Werkstattdiagnosen werden nicht spät abends geplant."
    if not in_hour_before_soundwerk(task, start, end, blocks):
        return "Soundwerk-Planung nur direkt in der Stunde vor Unterricht."
    return None


def is_preferred_small_werkstatt_task(task: Task) -> bool:
    title = task.title.lower()
    return task.category == "Werkstatt" and any(keyword in title for keyword in WERKSTATT_SMALL_KEYWORDS)


def is_safely_partial_werkstatt_task(task: Task) -> bool:
    """Return whether a Werkstatt task is safe to create as a same-day Teilblock."""
    if task.category != "Werkstatt":
        return False
    title = task.title.lower()
    return any(keyword in title for keyword in WERKSTATT_SMALL_KEYWORDS)


def make_werkstatt_partial_task(task: Task, minutes: int) -> Task:
    return Task(
        id=task.id,
        title=f"{task.title} – Teil 1",
        category=task.category,
        priority=task.priority,
        duration_minutes=minutes,
        estimated=task.estimated,
        duration_source=task.duration_source,
        notes=(task.notes + "\n" if task.notes else "") + f"Teilblock: ursprünglich {task.duration_minutes} Minuten.",
    )


def is_large_evening_task(task: Task, start: datetime) -> bool:
    return start.time() >= EVENING_START and task.duration_minutes >= LARGE_EVENING_TASK_MINUTES


def is_evening_light_task(task: Task) -> bool:
    return task.category in {"Buchhaltung", "Privat", "Haushalt"} or task.duration_minutes <= 30


def violates_evening_load_rule(
    task: Task,
    start: datetime,
    end: datetime,
    large_evening_count: int,
    has_full_workshop_day: bool,
) -> str | None:
    if start.time() < EVENING_START:
        return None
    if is_large_evening_task(task, start):
        if large_evening_count >= MAX_LARGE_EVENING_BLOCKS and task.priority != "P1":
            return "Abendlast begrenzt: maximal ein großer Fokusblock nach 17:00."
        if start.time() >= LATE_EVENING_START and task.category in {"Studio", "ALEGRA"} and task.priority not in {"P1", "P2"}:
            return "Lange Studio/ALEGRA-Aufgaben nach 19:00 nur bei hoher Priorität."
        if start.weekday() == 0 and has_full_workshop_day and task.category in {"Studio", "ALEGRA"} and end.time() > MONDAY_EVENING_AUTO_LATEST_END:
            return "Montagabend nach Werkstatt/Unterricht wird zurückhaltend geplant."
    if has_full_workshop_day and not is_evening_light_task(task) and large_evening_count >= MAX_LARGE_EVENING_BLOCKS and task.priority != "P1":
        return "Nach vollem Werkstatt-/Unterrichtstag bevorzugt der Abend kleine Aufgaben."
    return None


def task_sort_key(task: Task, slot_start: datetime) -> tuple[int, int, int, int, int, str]:
    priority_rank = PRIORITY_ORDER.get(task.priority, 99)
    evening_bonus = 0
    if task.category == "Buchhaltung" and slot_start.time() >= time(18, 0):
        evening_bonus = -1
    household_penalty = 1 if task.category == "Haushalt" and task.duration_minutes > 15 else 0
    werkstatt_small_bonus = -1 if is_preferred_small_werkstatt_task(task) else 0
    return (
        priority_rank + evening_bonus,
        household_penalty + werkstatt_small_bonus,
        task.duration_minutes,
        1 if task.estimated else 0,
        0 if task.category == "Werkstatt" else 1,
        task.title,
    )


def choose_task(
    tasks: list[Task],
    slot_start: datetime,
    slot_end: datetime,
    remaining_capacity: int,
    blocks: list[Block],
    main_count: int,
    mini_count: int,
    large_evening_count: int,
    partial_count: int,
    has_full_workshop_day: bool,
) -> Task | None:
    fitting: list[Task] = []
    gap_minutes = int((slot_end - slot_start).total_seconds() // 60)
    lesson_gap = is_lesson_gap(slot_start, slot_end, blocks)
    for task in tasks:
        if is_mini_task(task) and mini_count >= MAX_MINI_TASKS:
            continue
        if not is_mini_task(task) and main_count >= MAX_MAIN_TASKS:
            continue
        candidate = task
        fitting_minutes = gap_minutes
        if task.category == "Werkstatt":
            werkstatt_window = werkstatt_window_for_day(slot_start.date())
            if werkstatt_window is not None and slot_start < werkstatt_window.end:
                fitting_minutes = min(
                    fitting_minutes,
                    int((werkstatt_window.end - slot_start).total_seconds() // 60),
                )
        if task.duration_minutes > fitting_minutes or task.duration_minutes > remaining_capacity:
            if (
                task.category == "Werkstatt"
                and task.duration_minutes >= WERKSTATT_GAP_MINUTES
                and fitting_minutes >= WERKSTATT_GAP_MINUTES
                and remaining_capacity >= WERKSTATT_GAP_MINUTES
                and partial_count < MAX_PARTIAL_BLOCKS_PER_DAY
                and is_safely_partial_werkstatt_task(task)
            ):
                partial_minutes = min(WERKSTATT_PARTIAL_BLOCK_MINUTES, fitting_minutes, remaining_capacity)
                if partial_minutes < WERKSTATT_GAP_MINUTES:
                    continue
                candidate = make_werkstatt_partial_task(task, partial_minutes)
            else:
                continue
        if lesson_gap:
            if not is_lesson_gap_task(candidate):
                continue
            if gap_minutes >= 60 and candidate.duration_minutes > LESSON_GAP_PREFERRED_MAX_MINUTES:
                continue
        end = slot_start + timedelta(minutes=candidate.duration_minutes)
        if violates_time_rule(candidate, slot_start, end, blocks):
            continue
        if violates_evening_load_rule(candidate, slot_start, end, large_evening_count, has_full_workshop_day):
            continue
        fitting.append(candidate)

    if not fitting:
        return None

    if gap_minutes <= 30:
        mini_or_household = [task for task in fitting if is_mini_task(task) or task.category == "Haushalt"]
        if mini_or_household:
            return sorted(mini_or_household, key=lambda task: (task.duration_minutes, task.title))[0]

    return sorted(fitting, key=lambda task: task_sort_key(task, slot_start))[0]


def clipped_werkstatt_windows(target_day: date, blockers: list[Block]) -> list[TimeWindow]:
    werkstatt_window = werkstatt_window_for_day(target_day)
    if werkstatt_window is None:
        return []
    windows: list[TimeWindow] = []
    cursor = werkstatt_window.start
    for block in merge_overlapping(blockers):
        start = max(block.start, werkstatt_window.start)
        end = min(block.end, werkstatt_window.end)
        if end <= werkstatt_window.start or start >= werkstatt_window.end:
            continue
        if start > cursor:
            windows.append(TimeWindow(cursor, start))
        cursor = max(cursor, end)
    if cursor < werkstatt_window.end:
        windows.append(TimeWindow(cursor, werkstatt_window.end))
    return [window for window in windows if window.minutes >= WERKSTATT_GAP_MINUTES]


def count_matching_werkstatt_tasks(tasks: list[Task], minutes: int) -> tuple[int, bool]:
    matching = 0
    duration_miss = False
    for task in tasks:
        if task.category != "Werkstatt":
            continue
        if task.duration_minutes <= minutes:
            matching += 1
        elif task.duration_minutes >= WERKSTATT_GAP_MINUTES and minutes >= WERKSTATT_GAP_MINUTES:
            matching += 1
            duration_miss = True
        else:
            duration_miss = True
    return matching, duration_miss


def build_workshop_diagnostics(
    target_day: date,
    fixed_blocks: list[Block],
    planned_blocks: list[PlannedBlock],
    remaining_tasks: list[Task],
) -> list[str]:
    diagnostics: list[str] = []
    hard_windows = clipped_werkstatt_windows(target_day, buffered_planning_blockers(fixed_blocks))
    if hard_windows:
        rendered = ", ".join(f"{fmt(window.start)}–{fmt(window.end)} ({window.minutes} Min.)" for window in hard_windows)
        diagnostics.append(f"Erkannte freie Werkstattfenster: {rendered}.")
    else:
        diagnostics.append("Erkannte freie Werkstattfenster: keine Lücke ab 30 Minuten.")

    planned_blockers = [
        Block(
            id=f"planned-{index}",
            title=block.task.title,
            start=block.start,
            end=block.end + timedelta(minutes=block.buffer_after_minutes or DEFAULT_BUFFER_MINUTES),
            source="Auto-Plan",
            categories=(block.task.category,),
        )
        for index, block in enumerate(planned_blocks)
    ]
    unused_windows = clipped_werkstatt_windows(target_day, buffered_planning_blockers(fixed_blocks) + planned_blockers)
    for window in unused_windows:
        matching, duration_miss = count_matching_werkstatt_tasks(remaining_tasks, window.minutes)
        if matching:
            diagnostics.append(
                f"Nicht genutztes Werkstattfenster {fmt(window.start)}–{fmt(window.end)}: "
                f"{matching} passende Werkstattaufgabe(n) oder Teilblock möglich; Dauerproblem: {'ja' if duration_miss else 'nein'}."
            )
        else:
            diagnostics.append(
                f"Lücke {fmt(window.start)}–{fmt(window.end)} frei, aber keine passende Werkstatt-Aufgabe ≤{window.minutes} Min gefunden; "
                f"Dauerproblem: {'ja' if duration_miss else 'nein'}."
            )
    if not unused_windows:
        diagnostics.append("Nicht genutzte Werkstattfenster: keine ab 30 Minuten.")
    return diagnostics


def build_load_diagnostics(
    planned_blocks: list[PlannedBlock],
    remaining_tasks: list[Task],
    large_evening_count: int,
    partial_count: int,
    has_full_workshop_day: bool,
) -> list[str]:
    diagnostics: list[str] = []
    if has_full_workshop_day:
        diagnostics.append(
            "Tageslast-Regel aktiv: voller Werkstatt-/Unterrichtstag erkannt; der Abend wird zurückhaltend geplant."
        )
    diagnostics.append(
        f"Große Abendblöcke nach 17:00: {large_evening_count}/{MAX_LARGE_EVENING_BLOCKS} automatisch eingeplant."
    )
    diagnostics.append(f"Teilblock-Limit: {partial_count}/{MAX_PARTIAL_BLOCKS_PER_DAY} Teilblock(en) genutzt.")
    if partial_count >= MAX_PARTIAL_BLOCKS_PER_DAY:
        diagnostics.append("Teilblock-Limit erreicht: weitere passende Aufgaben werden nicht als „Teil 1“ eingeplant.")

    deferred_evening_tasks = [
        task
        for task in remaining_tasks
        if task.category in {"Studio", "ALEGRA"} and task.duration_minutes >= LARGE_EVENING_TASK_MINUTES and task.priority != "P1"
    ]
    if deferred_evening_tasks and (has_full_workshop_day or large_evening_count >= MAX_LARGE_EVENING_BLOCKS):
        rendered = "; ".join(render_task(task) for task in deferred_evening_tasks[:5])
        suffix = " ..." if len(deferred_evening_tasks) > 5 else ""
        diagnostics.append(f"Abendaufgaben wegen Tageslast zurückgestellt: {rendered}{suffix}")
    return diagnostics


def rejection_reason(task: Task, blocks: list[Block]) -> str:
    if task.duration_minutes > LONG_TASK_THRESHOLD_MINUTES:
        return f"Dauer {task.duration_minutes} Minuten ist über {LONG_TASK_THRESHOLD_MINUTES} Minuten."
    if task.category == "Soundwerk" and soundwerk_lesson_blocks(blocks):
        return "Passte nicht in die direkte Stunde vor Soundwerk-Unterricht."
    return "Passte nicht in freie Zeit, Kapazitätslimit, Aufgabenlimit oder Kategorie-Zeitregel."


def load_tasks_for_source(source: str) -> tuple[list[Task], str, bool, list[str], tuple[str, ...]]:
    warnings: list[str] = []
    if source == "json":
        return load_json_tasks(), "JSON: lokale Beispielaufgaben geladen.", False, warnings, ()

    try:
        result = load_todoist_tasks_from_env()
    except TodoistReadError as exc:
        warnings.append(f"Todoist konnte nicht gelesen werden ({exc}) – verwende lokale JSON-Beispieldaten.")
        return load_json_tasks(), warnings[-1], True, warnings, ()

    if result.used_fallback:
        return load_json_tasks(), result.status, True, warnings, result.status_details
    return [normalize_task(task) for task in result.tasks], result.status, False, warnings, result.status_details


def build_plan(source: str, target_day: date, calendar_source: str) -> PlanResult:
    tasks, source_status, fallback_used, warnings, source_details = load_tasks_for_source(source)
    calendar_blocks, calendar_status, calendar_fallback_used, calendar_warnings, calendar_details = load_calendar_blocks_for_source(
        calendar_source, target_day
    )
    warnings.extend(calendar_warnings)
    fixed_blocks = calendar_blocks + weekly_blocks(target_day) + [lunch_break_block(target_day)]
    fixed_blocks += travel_blocks(fixed_blocks)
    fixed_blocks = merge_overlapping(fixed_blocks)
    hard_blockers = buffered_planning_blockers(fixed_blocks)
    free_windows = find_free_windows(target_day, hard_blockers)
    free_minutes = sum(window.minutes for window in free_windows)
    capacity_minutes = int(free_minutes * MAX_PLANNED_PERCENT / 100)

    split_suggestions = [
        RejectedTask(task, rejection_reason(task, fixed_blocks))
        for task in tasks
        if task.duration_minutes > LONG_TASK_THRESHOLD_MINUTES
    ]
    remaining_tasks = [task for task in tasks if task.duration_minutes <= LONG_TASK_THRESHOLD_MINUTES]

    planned_blocks: list[PlannedBlock] = []
    planned_minutes = 0
    main_count = 0
    mini_count = 0
    large_evening_count = 0
    partial_count = 0
    has_full_workshop_day = any(
        is_werkstatt_availability_block(block) and block.start.time() <= time(9, 0) and block.end.time() >= time(17, 0)
        for block in fixed_blocks
    )

    for window in free_windows:
        cursor = window.start
        while cursor < window.end and planned_minutes < capacity_minutes:
            task = choose_task(
                remaining_tasks,
                cursor,
                window.end,
                capacity_minutes - planned_minutes,
                fixed_blocks,
                main_count,
                mini_count,
                large_evening_count,
                partial_count,
                has_full_workshop_day,
            )
            if task is None:
                werkstatt_window = werkstatt_window_for_day(target_day)
                if (
                    werkstatt_window is not None
                    and cursor < werkstatt_window.end
                    and window.end > werkstatt_window.end
                ):
                    cursor = werkstatt_window.end + timedelta(minutes=DEFAULT_BUFFER_MINUTES)
                    continue
                break

            end = cursor + timedelta(minutes=task.duration_minutes)
            buffer_after = RESET_BUFFER_MINUTES if is_werkstatt_diagnosis(task) else 0
            planned_blocks.append(PlannedBlock(task, cursor, end, buffer_after))
            planned_minutes += task.duration_minutes
            if is_mini_task(task):
                mini_count += 1
            else:
                main_count += 1
            if is_large_evening_task(task, cursor):
                large_evening_count += 1
            if "Teilblock:" in task.notes:
                partial_count += 1
            remaining_tasks.remove(next(original for original in remaining_tasks if original.id == task.id))
            cursor = end + timedelta(minutes=buffer_after or DEFAULT_BUFFER_MINUTES)

    not_scheduled = [RejectedTask(task, rejection_reason(task, fixed_blocks)) for task in remaining_tasks]
    workshop_diagnostics = build_workshop_diagnostics(target_day, fixed_blocks, planned_blocks, remaining_tasks)
    load_diagnostics = build_load_diagnostics(planned_blocks, remaining_tasks, large_evening_count, partial_count, has_full_workshop_day)

    return PlanResult(
        target_day=target_day,
        source_status=source_status,
        fixed_blocks=fixed_blocks,
        free_windows=free_windows,
        planned_blocks=planned_blocks,
        not_scheduled=not_scheduled,
        split_suggestions=split_suggestions,
        capacity_minutes=capacity_minutes,
        planned_minutes=planned_minutes,
        source=source,
        calendar_source=calendar_source,
        calendar_status=calendar_status,
        calendar_fallback_used=calendar_fallback_used,
        fallback_used=fallback_used,
        warnings=warnings,
        source_details=source_details,
        calendar_details=calendar_details,
        workshop_diagnostics=workshop_diagnostics,
        load_diagnostics=load_diagnostics,
    )


def _calendar_event_description(block: PlannedBlock) -> str:
    task = block.task
    return "\n".join(
        [
            AUTO_EVENT_MARKER,
            "automatisch erstellt vom Nico Day Planner",
            f"Todoist Task ID: {task.id}",
            f"Kategorie: {task.category}",
            f"Priorität: {task.priority}",
            f"Dauer: {task.duration_minutes} Minuten",
            f"duration_source: {task.duration_source}",
        ]
    )


def _calendar_event_datetime(value: datetime) -> dict[str, str]:
    local_time_zone = ZoneInfo(DEFAULT_CALENDAR_TIME_ZONE)
    if value.tzinfo is None:
        local_value = value.replace(tzinfo=local_time_zone)
    else:
        local_value = value.astimezone(local_time_zone)
    return {
        "dateTime": local_value.isoformat(timespec="seconds"),
        "timeZone": DEFAULT_CALENDAR_TIME_ZONE,
    }


def _calendar_event_body(block: PlannedBlock) -> dict[str, Any]:
    task = block.task
    return {
        "summary": f"[{task.category}] {task.title}",
        "description": _calendar_event_description(block),
        "start": _calendar_event_datetime(block.start),
        "end": _calendar_event_datetime(block.end),
    }


def validate_planned_blocks(plan: PlanResult) -> list[str]:
    errors: list[str] = []
    sorted_planned = sorted(plan.planned_blocks, key=lambda block: (block.start, block.end, block.task.title))

    previous: PlannedBlock | None = None
    for block in sorted_planned:
        if block.end <= block.start:
            errors.append(f"Ungültiger Auto-Block: {render_task(block.task)} {fmt(block.start)}–{fmt(block.end)}.")
        if previous is not None and block.start < previous.end:
            errors.append(
                "Überschneidung zwischen Auto-Blöcken: "
                f"{render_task(previous.task)} {fmt(previous.start)}–{fmt(previous.end)} und "
                f"{render_task(block.task)} {fmt(block.start)}–{fmt(block.end)}."
            )
        previous = block

    blockers = planning_blockers(plan.fixed_blocks)
    for planned in sorted_planned:
        for fixed in blockers:
            if overlaps(planned.start, planned.end, fixed.start, fixed.end):
                errors.append(
                    "Überschneidung mit blockiertem Kalendertermin: "
                    f"{render_task(planned.task)} {fmt(planned.start)}–{fmt(planned.end)} überschneidet "
                    f"{fixed.title} {fmt(fixed.start)}–{fmt(fixed.end)} ({fixed.source})."
                )
            if fixed.source != "Tagesregel" and planned.end <= fixed.start:
                buffer_minutes = int((fixed.start - planned.end).total_seconds() // 60)
                if buffer_minutes < MANUAL_EVENT_PRE_BUFFER_MINUTES:
                    errors.append(
                        "Zu wenig Puffer vor blockiertem Kalendertermin: "
                        f"{render_task(planned.task)} endet {fmt(planned.end)}, "
                        f"{fixed.title} startet {fmt(fixed.start)} ({fixed.source}); "
                        f"mindestens {MANUAL_EVENT_PRE_BUFFER_MINUTES} Minuten erforderlich."
                    )
    return errors


def apply_calendar_write(plan: PlanResult, write_calendar: bool, replace_auto_events: bool) -> None:
    target_calendar_id = os.environ.get(CALENDAR_ID_ENV_VAR, DEFAULT_CALENDAR_ID)
    plan.calendar_write_target_id = target_calendar_id
    plan.calendar_write_enabled = False
    plan.calendar_created_events = 0
    plan.calendar_deleted_events = 0

    if not write_calendar:
        return

    if plan.calendar_source != "google":
        plan.calendar_write_blocked_warning = (
            "Schreiben blockiert: --write-calendar ist nur mit --calendar-source google erlaubt."
        )
        plan.warnings.append(plan.calendar_write_blocked_warning)
        return

    if os.environ.get("GOOGLE_CALENDAR_WRITE_ENABLED") != "true":
        plan.calendar_write_blocked_warning = (
            "Schreiben blockiert: GOOGLE_CALENDAR_WRITE_ENABLED=true ist nicht gesetzt."
        )
        plan.warnings.append(plan.calendar_write_blocked_warning)
        return

    plan.validation_errors = validate_planned_blocks(plan)
    if plan.validation_errors:
        plan.calendar_write_blocked_warning = (
            f"Schreiben abgebrochen: Planvalidierung fand {len(plan.validation_errors)} Überschneidung(en)."
        )
        plan.warnings.append(plan.calendar_write_blocked_warning)
        plan.warnings.extend(plan.validation_errors)
        return

    plan.calendar_write_enabled = True
    try:
        if replace_auto_events:
            plan.calendar_deleted_events = delete_auto_events_for_date(plan.target_day, target_calendar_id)
        for block in plan.planned_blocks:
            create_calendar_event(target_calendar_id, _calendar_event_body(block))
            plan.calendar_created_events += 1
    except GoogleCalendarReadError as exc:
        plan.calendar_write_enabled = False
        warning = f"Google Calendar Schreiben fehlgeschlagen ({exc}) – keine weiteren Events geschrieben."
        plan.calendar_write_blocked_warning = warning
        plan.warnings.append(warning)


def render_task(task: Task) -> str:
    estimated = " (Dauer geschätzt)" if task.estimated else ""
    return f"{task.title} [{task.category}, {task.priority}, {task.duration_minutes} Min.]{estimated}"


def render_source_details(source_details: tuple[str, ...]) -> list[str]:
    if not source_details:
        return []
    return [f"- {detail}" for detail in source_details]


def render_plan(plan: PlanResult) -> str:
    lines: list[str] = []
    weekday = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"][plan.target_day.weekday()]
    free_minutes = sum(window.minutes for window in plan.free_windows)

    lines.append(f"# Nico Day Planner v0.6-calendar – Dry-Run für {weekday}, {plan.target_day.isoformat()}")
    lines.append("")
    lines.append("## Annahmen")
    lines.append("- Standard ist Dry-Run: Kalender-Schreiben nur mit `--write-calendar` und `GOOGLE_CALENDAR_WRITE_ENABLED=true`.")
    lines.append("- Geplant wird nur zwischen 09:00 und 23:00 Uhr.")
    lines.append(f"- Freie Zeit: {free_minutes} Minuten; davon maximal 70 Prozent verplant: {plan.capacity_minutes} Minuten.")
    lines.append("- Maximal 6 Hauptaufgaben und 2 Mini-Tasks werden automatisch eingeplant.")
    lines.append("")

    lines.append("## Quellenstatus")
    lines.append(f"- Gewählte Aufgabenquelle: `{plan.source}`.")
    lines.append(f"- {plan.source_status}")
    if plan.fallback_used:
        lines.append("- Aufgaben-Fallback aktiv: JSON-Beispielaufgaben wurden verwendet.")
    lines.extend(render_source_details(plan.source_details))
    lines.append(f"- Kalenderquelle: `{plan.calendar_source}`.")
    lines.append(f"- {plan.calendar_status}")
    if plan.calendar_fallback_used:
        lines.append("- Kalender-Fallback aktiv: JSON-Kalenderdaten wurden verwendet.")
    lines.extend(render_source_details(plan.calendar_details))
    lines.append("")
    lines.append("## Kalender-Schreibstatus")
    lines.append(f"- Kalender-Schreiben: {'aktiviert' if plan.calendar_write_enabled else 'deaktiviert'}.")
    lines.append(f"- Zielkalender-ID: `{plan.calendar_write_target_id}`.")
    lines.append(f"- Schreibschutz aktiv: nur Events mit Marker `{AUTO_EVENT_MARKER}` werden ersetzt/gelöscht.")
    lines.append(f"- Anzahl erstellter Events: {plan.calendar_created_events}.")
    lines.append(f"- Anzahl gelöschter alter Auto-Events: {plan.calendar_deleted_events}.")
    if plan.calendar_write_blocked_warning:
        lines.append(f"- Warnung: {plan.calendar_write_blocked_warning}")
    for warning in plan.warnings:
        if warning != plan.calendar_write_blocked_warning:
            lines.append(f"- Warnung: {warning}")
    lines.append("")

    lines.append("## Planvalidierung")
    if plan.validation_errors:
        lines.append(f"- WARNUNG: {len(plan.validation_errors)} Überschneidung(en) erkannt; Kalender-Schreiben wird blockiert.")
    else:
        lines.append("- Keine Überschneidungen zwischen Auto-Blöcken oder mit harten Kalenderblockern erkannt.")
    lines.append("")

    lines.append("## Blockierte Zeiten")
    if plan.fixed_blocks:
        for block in plan.fixed_blocks:
            location = f", {block.location}" if block.location else ""
            lines.append(f"- {fmt(block.start)}–{fmt(block.end)} {block.title} ({block.source}{location})")
    else:
        lines.append("- Keine blockierten Zeiten.")
    lines.append("")

    lines.append("## Werkstattfenster-Diagnose")
    lines.extend(f"- {detail}" for detail in plan.workshop_diagnostics)
    lines.append("")

    lines.append("## Tageslast- und Abenddiagnose")
    if plan.load_diagnostics:
        lines.extend(f"- {detail}" for detail in plan.load_diagnostics)
    else:
        lines.append("- Keine zusätzlichen Tageslast-Hinweise.")
    lines.append("")

    lines.append("## Vorgeschlagener Tagesplan")
    if plan.planned_blocks:
        for block in plan.planned_blocks:
            buffer_text = f" + {block.buffer_after_minutes} Min. Reset-Puffer" if block.buffer_after_minutes else ""
            lines.append(f"- {fmt(block.start)}–{fmt(block.end)} {render_task(block.task)}{buffer_text}")
    else:
        lines.append("- Keine Aufgaben automatisch eingeplant.")
    lines.append("")

    lines.append("## Puffer")
    lines.append("- Zwischen automatisch geplanten Aufgaben werden standardmäßig 15 Minuten Puffer gelassen.")
    lines.append("- Werkstattdiagnosen erhalten zusätzlich einen expliziten 15-Minuten-Reset-Puffer.")
    lines.append("")

    lines.append("## Nicht eingeplant")
    combined_not_scheduled = plan.not_scheduled + plan.split_suggestions
    if combined_not_scheduled:
        for item in combined_not_scheduled:
            lines.append(f"- {render_task(item.task)} – {item.reason}")
    else:
        lines.append("- Keine.")
    lines.append("")

    lines.append("## Vorschläge zur Zerlegung")
    if plan.split_suggestions:
        for item in plan.split_suggestions:
            chunk = min(60, max(30, item.task.duration_minutes // 3))
            lines.append(f"- {item.task.title}: in Blöcke von ca. {chunk}–60 Minuten zerlegen; nicht automatisch vollständig eingeplant.")
    else:
        lines.append("- Keine Aufgaben über 120 Minuten gefunden.")

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Nico Day Planner v0.6-calendar Dry-Run")
    parser.add_argument(
        "--source",
        choices=("json", "todoist"),
        default="json",
        help="Aufgabenquelle: lokale JSON-Daten oder Todoist read-only. Default: json.",
    )
    parser.add_argument(
        "--calendar-source",
        choices=("json", "google"),
        default="json",
        help="Kalenderquelle: lokale JSON-Daten oder Google Calendar read-only. Default: json.",
    )
    parser.add_argument(
        "--date",
        help="Optionales Zieldatum im Format YYYY-MM-DD. Default: morgen.",
    )
    parser.add_argument(
        "--write-calendar",
        action="store_true",
        help="Google Calendar Events schreiben, nur wenn zusätzlich GOOGLE_CALENDAR_WRITE_ENABLED=true gesetzt ist.",
    )
    parser.add_argument(
        "--replace-auto-events",
        action="store_true",
        help=f"Alte Planner-Events am Zieltag ersetzen; löscht nur Events mit Marker {AUTO_EVENT_MARKER}.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target_day = date.fromisoformat(args.date) if args.date else date.today() + timedelta(days=1)
    plan = build_plan(args.source, target_day, args.calendar_source)
    plan.validation_errors = validate_planned_blocks(plan)
    apply_calendar_write(plan, args.write_calendar, args.replace_auto_events)
    print(render_plan(plan))


if __name__ == "__main__":
    main()
