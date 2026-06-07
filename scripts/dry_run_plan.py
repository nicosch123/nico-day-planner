#!/usr/bin/env python3
"""Local dry-run planner using example JSON data only.

Version 1 intentionally does not read from or write to Google Calendar or
Todoist. This script loads local fixture files, treats calendar entries as
fixed blockers, and prints a proposed plan for tomorrow.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any


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
BUCHHALTUNG_LATEST_END = time(21, 0)
WERKSTATT_DIAGNOSIS_LATEST_END = time(18, 0)
HOUSEHOLD_GAP_FILLER_LIMIT_MINUTES = 30
PRIORITY_ORDER = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}


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


@dataclass(frozen=True)
class CalendarEvent:
    """A fixed local example calendar event loaded from data/example_calendar.json."""

    id: str
    title: str
    calendar: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class TimeWindow:
    """A free time window in the planning day."""

    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass(frozen=True)
class PlannedBlock:
    """A scheduled task block in the dry-run plan."""

    task: Task
    start: datetime
    end: datetime


@dataclass(frozen=True)
class RejectedTask:
    """A task that could not or should not be scheduled."""

    task: Task
    reason: str


@dataclass(frozen=True)
class PlanResult:
    """The complete dry-run planning result."""

    target_day: date
    events: list[CalendarEvent]
    free_windows: list[TimeWindow]
    planned_blocks: list[PlannedBlock]
    not_scheduled: list[RejectedTask]
    split_suggestions: list[RejectedTask]
    free_minutes: int
    capacity_minutes: int
    planned_minutes: int


def load_json(path: Path) -> list[dict[str, Any]]:
    """Load a JSON list from a local fixture file."""
    with path.open(encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON list")
    return data


def parse_clock(target_day: date, value: str) -> datetime:
    """Parse a HH:MM value for the target day."""
    parsed_time = datetime.strptime(value, "%H:%M").time()
    return datetime.combine(target_day, parsed_time)


def load_tasks() -> list[Task]:
    """Load local example tasks and estimate missing durations."""
    tasks: list[Task] = []
    for item in load_json(TASKS_PATH):
        raw_duration = item.get("duration_minutes")
        estimated = raw_duration is None
        duration = DEFAULT_ESTIMATED_DURATION_MINUTES if estimated else int(raw_duration)
        tasks.append(
            Task(
                id=str(item["id"]),
                title=str(item["title"]),
                category=str(item["category"]),
                priority=str(item["priority"]),
                duration_minutes=duration,
                estimated=estimated,
                notes=str(item.get("notes", "")),
            )
        )
    return tasks


def load_calendar_events(target_day: date) -> list[CalendarEvent]:
    """Load local example calendar events for tomorrow."""
    events: list[CalendarEvent] = []
    for item in load_json(CALENDAR_PATH):
        events.append(
            CalendarEvent(
                id=str(item["id"]),
                title=str(item["title"]),
                calendar=str(item["calendar"]),
                start=parse_clock(target_day, str(item["start"])),
                end=parse_clock(target_day, str(item["end"])),
            )
        )
    return sorted(events, key=lambda event: event.start)


def merge_events(events: list[CalendarEvent]) -> list[TimeWindow]:
    """Merge overlapping fixed calendar events into blocker windows."""
    blockers: list[TimeWindow] = []
    for event in events:
        if not blockers or event.start > blockers[-1].end:
            blockers.append(TimeWindow(event.start, event.end))
            continue
        previous = blockers[-1]
        blockers[-1] = TimeWindow(previous.start, max(previous.end, event.end))
    return blockers


def find_free_windows(target_day: date, events: list[CalendarEvent]) -> list[TimeWindow]:
    """Find free windows between 09:00 and 23:00 around fixed events."""
    day_start = datetime.combine(target_day, PLANNING_START)
    day_end = datetime.combine(target_day, PLANNING_END)
    free_windows: list[TimeWindow] = []
    cursor = day_start

    for blocker in merge_events(events):
        blocker_start = max(blocker.start, day_start)
        blocker_end = min(blocker.end, day_end)
        if blocker_end <= day_start or blocker_start >= day_end:
            continue
        if blocker_start > cursor:
            free_windows.append(TimeWindow(cursor, blocker_start))
        cursor = max(cursor, blocker_end)

    if cursor < day_end:
        free_windows.append(TimeWindow(cursor, day_end))

    return free_windows


def is_werkstatt_diagnosis(task: Task) -> bool:
    """Return whether the task is a Werkstatt diagnosis task."""
    return task.category == "Werkstatt" and "diagnose" in task.title.lower()


def violates_time_rule(task: Task, start: datetime, end: datetime) -> str | None:
    """Return a rule violation message for a candidate slot, if any."""
    if task.category == "Buchhaltung" and end.time() > BUCHHALTUNG_LATEST_END:
        return "Buchhaltung wird nicht nach 21:00 Uhr geplant."
    if is_werkstatt_diagnosis(task) and end.time() > WERKSTATT_DIAGNOSIS_LATEST_END:
        return "Werkstatt-Diagnose wird nicht spät abends geplant."
    return None


def task_sort_key(task: Task, remaining_gap_minutes: int) -> tuple[int, int, str]:
    """Sort tasks by priority while nudging Haushalt into small gaps."""
    priority_rank = PRIORITY_ORDER.get(task.priority, 99)
    household_penalty = 0
    if task.category == "Haushalt" and remaining_gap_minutes > HOUSEHOLD_GAP_FILLER_LIMIT_MINUTES:
        household_penalty = 2
    return (priority_rank + household_penalty, task.duration_minutes, task.title)


def choose_task(
    tasks: list[Task],
    slot_start: datetime,
    slot_end: datetime,
    remaining_capacity: int,
) -> Task | None:
    """Choose the best task that fits the current slot and active rules."""
    remaining_gap_minutes = int((slot_end - slot_start).total_seconds() // 60)
    fitting_tasks = [
        task
        for task in tasks
        if task.duration_minutes <= remaining_gap_minutes
        and task.duration_minutes <= remaining_capacity
        and not violates_time_rule(task, slot_start, slot_start + timedelta(minutes=task.duration_minutes))
    ]
    if not fitting_tasks:
        return None

    if remaining_gap_minutes <= HOUSEHOLD_GAP_FILLER_LIMIT_MINUTES:
        household_tasks = [task for task in fitting_tasks if task.category == "Haushalt"]
        if household_tasks:
            return sorted(household_tasks, key=lambda task: (task.duration_minutes, task.title))[0]

    return sorted(fitting_tasks, key=lambda task: task_sort_key(task, remaining_gap_minutes))[0]


def build_plan(tasks: list[Task], events: list[CalendarEvent], target_day: date) -> PlanResult:
    """Build a dry-run day plan from local example tasks and events."""
    free_windows = find_free_windows(target_day, events)
    free_minutes = sum(window.minutes for window in free_windows)
    capacity_minutes = int(free_minutes * MAX_PLANNED_PERCENT / 100)

    split_suggestions = [
        RejectedTask(task, f"Dauer {task.duration_minutes} Minuten ist über {LONG_TASK_THRESHOLD_MINUTES} Minuten.")
        for task in tasks
        if task.duration_minutes > LONG_TASK_THRESHOLD_MINUTES
    ]
    remaining_tasks = [task for task in tasks if task.duration_minutes <= LONG_TASK_THRESHOLD_MINUTES]

    planned_blocks: list[PlannedBlock] = []
    planned_minutes = 0

    for window in free_windows:
        cursor = window.start
        while cursor < window.end and planned_minutes < capacity_minutes:
            remaining_capacity = capacity_minutes - planned_minutes
            task = choose_task(remaining_tasks, cursor, window.end, remaining_capacity)
            if task is None:
                break
            block_end = cursor + timedelta(minutes=task.duration_minutes)
            planned_blocks.append(PlannedBlock(task=task, start=cursor, end=block_end))
            planned_minutes += task.duration_minutes
            remaining_tasks.remove(task)
            cursor = block_end

    not_scheduled = [RejectedTask(task, reason_for_unscheduled(task, free_windows, capacity_minutes - planned_minutes)) for task in remaining_tasks]

    return PlanResult(
        target_day=target_day,
        events=events,
        free_windows=free_windows,
        planned_blocks=planned_blocks,
        not_scheduled=not_scheduled,
        split_suggestions=split_suggestions,
        free_minutes=free_minutes,
        capacity_minutes=capacity_minutes,
        planned_minutes=planned_minutes,
    )


def reason_for_unscheduled(task: Task, free_windows: list[TimeWindow], remaining_capacity: int) -> str:
    """Explain why a task was not scheduled."""
    if task.duration_minutes > remaining_capacity:
        return "Nicht eingeplant, weil das 70-Prozent-Limit der freien Zeit erreicht wurde."

    latest_possible_reasons = []
    for window in free_windows:
        if task.duration_minutes <= window.minutes:
            violation = violates_time_rule(task, window.start, window.start + timedelta(minutes=task.duration_minutes))
            if violation:
                latest_possible_reasons.append(violation)
            else:
                return "Nicht eingeplant, weil passendere Prioritäten zuerst geplant wurden."

    if latest_possible_reasons:
        return latest_possible_reasons[0]
    return "Nicht eingeplant, weil kein ausreichend großes freies Zeitfenster verfügbar ist."


def format_time(value: datetime) -> str:
    """Format a datetime as HH:MM."""
    return value.strftime("%H:%M")


def format_duration(task: Task) -> str:
    """Format task duration and estimation marker."""
    suffix = " (geschätzt)" if task.estimated else ""
    return f"{task.duration_minutes} Min.{suffix}"


def print_plan(result: PlanResult) -> None:
    """Print the dry-run day plan as German Markdown text."""
    print("# Nico Day Planner – lokaler Dry-Run")
    print()
    print("Keine Todoist- oder Google-Kalender-API wurde gelesen oder beschrieben.")
    print(f"Zieldatum: {result.target_day.isoformat()}")
    print(f"Regeln: {RULES_PATH.name} | Prompt: {PROMPT_PATH.name}")
    print()

    print("## Annahmen")
    print(f"- Planungsfenster: {PLANNING_START.strftime('%H:%M')}–{PLANNING_END.strftime('%H:%M')} Uhr.")
    print(f"- Freie Zeit: {result.free_minutes} Minuten; davon maximal {MAX_PLANNED_PERCENT}% = {result.capacity_minutes} Minuten verplant.")
    print(f"- Tatsächlich verplant: {result.planned_minutes} Minuten.")
    print("- Aufgaben ohne Dauer werden mit 30 Minuten geschätzt und markiert.")
    print()

    print("## Feste Termine")
    for event in result.events:
        print(f"- {format_time(event.start)}–{format_time(event.end)}: {event.title} ({event.calendar})")
    print()

    print("## Freie Zeitfenster")
    for window in result.free_windows:
        print(f"- {format_time(window.start)}–{format_time(window.end)} ({window.minutes} Min.)")
    print()

    print("## Vorschlag Tagesplan")
    if not result.planned_blocks:
        print("- Keine Aufgaben eingeplant.")
    for block in result.planned_blocks:
        task = block.task
        print(
            f"- {format_time(block.start)}–{format_time(block.end)}: {task.title} "
            f"[{task.category}, {task.priority}, {format_duration(task)}]"
        )
    print()

    print("## Puffer")
    buffer_minutes = result.free_minutes - result.planned_minutes
    print(f"- Nicht aktiv verplante freie Zeit: {buffer_minutes} Minuten.")
    print("- Diese Zeit bleibt als Puffer, Übergang, Pause oder Reserve frei.")
    print()

    print("## Nicht eingeplant")
    if not result.not_scheduled:
        print("- Keine weiteren Aufgaben.")
    for rejected in result.not_scheduled:
        print(f"- {rejected.task.title} [{rejected.task.category}, {rejected.task.priority}, {format_duration(rejected.task)}]: {rejected.reason}")
    print()

    print("## Vorschläge zur Zerlegung")
    if not result.split_suggestions:
        print("- Keine Aufgaben über 120 Minuten.")
    for rejected in result.split_suggestions:
        task = rejected.task
        print(f"- {task.title} [{task.category}, {task.priority}, {format_duration(task)}]: {rejected.reason}")
        print("  Vorschlag: in mehrere Blöcke von maximal 60–90 Minuten aufteilen.")


def main() -> None:
    """Run the local JSON-only dry-run planner."""
    target_day = date.today() + timedelta(days=1)
    tasks = load_tasks()
    events = load_calendar_events(target_day)
    result = build_plan(tasks, events, target_day)
    print_plan(result)


if __name__ == "__main__":
    main()
