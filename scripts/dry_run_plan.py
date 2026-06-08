#!/usr/bin/env python3
"""Local dry-run planner for Nico Day Planner v0.5.

Default source is JSON. Todoist can be used as a read-only source with
--source todoist. If TODOIST_API_TOKEN is missing, the script falls back to the
local JSON examples. There is deliberately no Google Calendar access.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

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
MAX_MAIN_TASKS = 6
MAX_MINI_TASKS = 2
MINI_TASK_MAX_MINUTES = 15
BUCHHALTUNG_LATEST_END = time(21, 0)
WERKSTATT_DIAGNOSIS_LATEST_END = time(18, 0)
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
    fallback_used: bool = False
    warnings: list[str] = field(default_factory=list)
    source_details: tuple[str, ...] = field(default_factory=tuple)


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
    estimated = raw_duration is None
    duration = DEFAULT_ESTIMATED_DURATION_MINUTES if estimated else int(raw_duration)
    return Task(
        id=str(raw.get("id", "unknown")),
        title=str(raw.get("title", "Ohne Titel")),
        category=str(raw.get("category", "Privat")),
        priority=str(raw.get("priority", "P4")),
        duration_minutes=duration,
        estimated=estimated,
        notes=str(raw.get("notes", "")),
    )


def load_json_tasks() -> list[Task]:
    payload = load_json(TASKS_PATH)
    if not isinstance(payload, list):
        raise ValueError(f"{TASKS_PATH} muss eine JSON-Liste enthalten.")
    return [normalize_task(item) for item in payload if isinstance(item, dict)]


def load_calendar_blocks(target_day: date) -> list[Block]:
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


def in_hour_before_soundwerk(task: Task, start: datetime, end: datetime, blocks: list[Block]) -> bool:
    if task.category != "Soundwerk":
        return True
    for lesson in soundwerk_lesson_blocks(blocks):
        prep_start = lesson.start - timedelta(minutes=60)
        if start >= prep_start and end <= lesson.start:
            return True
    return False


def violates_time_rule(task: Task, start: datetime, end: datetime, blocks: list[Block]) -> str | None:
    if task.category == "Buchhaltung" and end.time() > BUCHHALTUNG_LATEST_END:
        return "Buchhaltung/Admin/Krankenkasse wird nicht nach 21:00 Uhr geplant."
    if is_werkstatt_diagnosis(task) and end.time() > WERKSTATT_DIAGNOSIS_LATEST_END:
        return "Werkstattdiagnosen werden nicht spät abends geplant."
    if not in_hour_before_soundwerk(task, start, end, blocks):
        return "Soundwerk-Planung nur direkt in der Stunde vor Unterricht."
    return None


def task_sort_key(task: Task, slot_start: datetime) -> tuple[int, int, int, str]:
    priority_rank = PRIORITY_ORDER.get(task.priority, 99)
    evening_bonus = 0
    if task.category == "Buchhaltung" and slot_start.time() >= time(18, 0):
        evening_bonus = -1
    household_penalty = 1 if task.category == "Haushalt" and task.duration_minutes > 15 else 0
    return (priority_rank + evening_bonus + household_penalty, task.duration_minutes, 1 if task.estimated else 0, task.title)


def choose_task(
    tasks: list[Task],
    slot_start: datetime,
    slot_end: datetime,
    remaining_capacity: int,
    blocks: list[Block],
    main_count: int,
    mini_count: int,
) -> Task | None:
    fitting: list[Task] = []
    gap_minutes = int((slot_end - slot_start).total_seconds() // 60)
    for task in tasks:
        if is_mini_task(task) and mini_count >= MAX_MINI_TASKS:
            continue
        if not is_mini_task(task) and main_count >= MAX_MAIN_TASKS:
            continue
        if task.duration_minutes > gap_minutes or task.duration_minutes > remaining_capacity:
            continue
        end = slot_start + timedelta(minutes=task.duration_minutes)
        if violates_time_rule(task, slot_start, end, blocks):
            continue
        fitting.append(task)

    if not fitting:
        return None

    if gap_minutes <= 30:
        mini_or_household = [task for task in fitting if is_mini_task(task) or task.category == "Haushalt"]
        if mini_or_household:
            return sorted(mini_or_household, key=lambda task: (task.duration_minutes, task.title))[0]

    return sorted(fitting, key=lambda task: task_sort_key(task, slot_start))[0]


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


def build_plan(source: str, target_day: date) -> PlanResult:
    tasks, source_status, fallback_used, warnings, source_details = load_tasks_for_source(source)
    fixed_blocks = load_calendar_blocks(target_day) + weekly_blocks(target_day)
    fixed_blocks += travel_blocks(fixed_blocks)
    fixed_blocks = merge_overlapping(fixed_blocks)
    free_windows = find_free_windows(target_day, fixed_blocks)
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
            )
            if task is None:
                break

            end = cursor + timedelta(minutes=task.duration_minutes)
            buffer_after = RESET_BUFFER_MINUTES if is_werkstatt_diagnosis(task) else 0
            planned_blocks.append(PlannedBlock(task, cursor, end, buffer_after))
            planned_minutes += task.duration_minutes
            if is_mini_task(task):
                mini_count += 1
            else:
                main_count += 1
            remaining_tasks.remove(task)
            cursor = end + timedelta(minutes=buffer_after or DEFAULT_BUFFER_MINUTES)

    not_scheduled = [RejectedTask(task, rejection_reason(task, fixed_blocks)) for task in remaining_tasks]

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
        fallback_used=fallback_used,
        warnings=warnings,
        source_details=source_details,
    )


def render_task(task: Task) -> str:
    estimated = " (Dauer geschätzt)" if task.estimated else ""
    return f"{task.title} [{task.category}, {task.priority}, {task.duration_minutes} Min.]{estimated}"


def render_plan(plan: PlanResult) -> str:
    lines: list[str] = []
    weekday = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"][plan.target_day.weekday()]
    free_minutes = sum(window.minutes for window in plan.free_windows)

    lines.append(f"# Nico Day Planner v0.5 – Dry-Run für {weekday}, {plan.target_day.isoformat()}")
    lines.append("")
    lines.append("## Annahmen")
    lines.append("- Version 0.5 ist ein lokaler Dry-Run: keine Google-Kalender-Abfrage und keine Schreibzugriffe.")
    lines.append("- Geplant wird nur zwischen 09:00 und 23:00 Uhr.")
    lines.append(f"- Freie Zeit: {free_minutes} Minuten; davon maximal 70 Prozent verplant: {plan.capacity_minutes} Minuten.")
    lines.append("- Maximal 6 Hauptaufgaben und 2 Mini-Tasks werden automatisch eingeplant.")
    lines.append("")

    lines.append("## Quellenstatus")
    lines.append(f"- Gewählte Quelle: `{plan.source}`.")
    lines.append(f"- {plan.source_status}")
    if plan.fallback_used:
        lines.append("- Fallback aktiv: JSON-Beispieldaten wurden verwendet.")
    for detail in plan.source_details:
        lines.append(f"- {detail}")
    for warning in plan.warnings:
        lines.append(f"- Warnung: {warning}")
    lines.append("")

    lines.append("## Blockierte Zeiten")
    if plan.fixed_blocks:
        for block in plan.fixed_blocks:
            location = f", {block.location}" if block.location else ""
            lines.append(f"- {fmt(block.start)}–{fmt(block.end)} {block.title} ({block.source}{location})")
    else:
        lines.append("- Keine blockierten Zeiten.")
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
    parser = argparse.ArgumentParser(description="Nico Day Planner v0.5 Dry-Run")
    parser.add_argument(
        "--source",
        choices=("json", "todoist"),
        default="json",
        help="Aufgabenquelle: lokale JSON-Daten oder Todoist read-only. Default: json.",
    )
    parser.add_argument(
        "--date",
        help="Optionales Zieldatum im Format YYYY-MM-DD. Default: morgen.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target_day = date.fromisoformat(args.date) if args.date else date.today() + timedelta(days=1)
    plan = build_plan(args.source, target_day)
    print(render_plan(plan))


if __name__ == "__main__":
    main()
