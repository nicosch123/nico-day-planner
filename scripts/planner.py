#!/usr/bin/env python3
"""Friendly application-layer CLI for the Nico Day Planner.

Phase 1 wraps the existing dry-run planner without changing its safety gates.
Calendar writes are still controlled exclusively by dry_run_plan.py via the
existing --write-calendar flag and GOOGLE_CALENDAR_WRITE_ENABLED environment
variable.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from google_calendar_client import (
    AUTO_EVENT_MARKER,
    WEEK_AUTO_EVENT_MARKER,
    CALENDAR_ID_ENV_VAR,
    DEFAULT_CALENDAR_ID,
    GoogleCalendarReadError,
    create_calendar_event,
    delete_auto_events_for_date,
    load_calendar_events_for_date,
)
from dry_run_plan import (
    Block,
    PlannedBlock,
    Task,
    _calendar_event_datetime,
    google_calendar_color_id_for_category,
    load_calendar_blocks_for_source,
    load_tasks_for_source,
    merge_overlapping,
    planning_blockers,
    render_task,
    weekly_blocks,
    lunch_break_block,
    needs_homecoming_evening_pause,
    homecoming_evening_pause_block,
    manual_calendar_coverage_titles,
    task_is_manually_covered_by_titles,
    normalize_calendar_coverage_title,
    travel_blocks,
)


SUPPORTED_MODES = (
    "normal",
    "light",
    "focus-workshop",
    "admin-evening",
    "no-evening",
    "push",
)
DAY_ENERGY_CHOICES = ("low", "normal", "high")
DAY_OVERALL_CHOICES = ("too_light", "good", "too_full")
EVENING_CHOICES = ("ok", "too_full", "too_late", "not_relevant")

SCRIPT_DIR = Path(__file__).resolve().parent
DRY_RUN_PLAN = SCRIPT_DIR / "dry_run_plan.py"


DATE_FORMAT_HELP = "Erlaubte Formate: yesterday, today, tomorrow oder YYYY-MM-DD."


def target_date_for(value: str) -> date:
    """Return the concrete target date for a supported relative day or ISO date."""
    today = date.today()
    if value == "tomorrow":
        return today + timedelta(days=1)
    if value == "today":
        return today
    if value == "yesterday":
        return today - timedelta(days=1)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Ungültiger Review-Tag: {value}. {DATE_FORMAT_HELP}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Nico Day Planner CLI – friendly wrapper around the safe planner.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_day_options(day_parser: argparse.ArgumentParser) -> None:
        day_parser.add_argument(
            "day",
            help="Zieltag. preview/write unterstützen tomorrow; review unterstützt yesterday, today, tomorrow oder YYYY-MM-DD.",
        )
        day_parser.add_argument(
            "--mode",
            choices=SUPPORTED_MODES,
            default="normal",
            help="Planungsmodus für spätere Phasen. Wird in Phase 1 angezeigt und als NICO_PLANNER_MODE übergeben.",
        )
        day_parser.add_argument(
            "--note",
            help="Freitext-Hinweis für spätere Phasen. Wird in Phase 1 angezeigt und als NICO_PLANNER_NOTE übergeben.",
        )
        day_parser.add_argument(
            "--from-now",
            action="store_true",
            help="Für today ab aktueller Uhrzeit planen (auf den nächsten 15-Minuten-Slot gerundet).",
        )
        day_parser.add_argument(
            "--start-time",
            help="Früheste Planungszeit für today im Format HH:MM, z. B. 12:00.",
        )
        day_parser.add_argument(
            "--from",
            dest="from_time",
            help="Manueller Startzeitpunkt, z. B. 09:00. Wird in Phase 1 angezeigt und als NICO_PLANNER_FROM übergeben.",
        )
        day_parser.add_argument(
            "--to",
            dest="to_time",
            help="Manueller Endzeitpunkt, z. B. 21:00. Wird in Phase 1 angezeigt und als NICO_PLANNER_TO übergeben.",
        )
        day_parser.add_argument("--until", help="Späteste Planungsgrenze im Format HH:MM; keine Auto-Events enden danach.")
        day_parser.add_argument("--push", action="store_true", help="Push-Modus: 90%% Auslastung, späte Planung und gelockerte Abend-/Admin-Regeln.")
        day_parser.add_argument("--allow-late", action="store_true", help="Veraltet: Alias für --push.")
        day_parser.add_argument("--allow-admin-until", help="Admin/Buchhaltung bis zu dieser Uhrzeit im Format HH:MM erlauben.")

    for command in ("preview", "write", "review"):
        day_parser = subparsers.add_parser(command)
        add_day_options(day_parser)
        if command == "review":
            day_parser.add_argument(
                "--non-interactive",
                action="store_true",
                help="Review: Planner-Auto-Events nur anzeigen, kein Feedback abfragen und nichts speichern.",
            )
            day_parser.add_argument(
                "--day-energy",
                choices=DAY_ENERGY_CHOICES,
                help="Review-Tagesfeedback: Energielevel.",
            )
            day_parser.add_argument(
                "--day-overall",
                choices=DAY_OVERALL_CHOICES,
                help="Review-Tagesfeedback: Einschätzung des Gesamtplans.",
            )
            day_parser.add_argument(
                "--evening",
                choices=EVENING_CHOICES,
                help="Review-Tagesfeedback: Einschätzung des Abends.",
            )
            day_parser.add_argument(
                "--day-note",
                default="",
                help="Review-Tagesfeedback: optionale Tagesnotiz.",
            )
            day_parser.add_argument(
                "--quick-day",
                action="store_true",
                help="Review: Tagesfeedback per CLI speichern und einzelne Events neutral bewerten.",
            )

    week_parser = subparsers.add_parser("week", help="Grobe Wochenplanung anzeigen oder sicher gated schreiben.")
    week_parser.add_argument("week_command", choices=("preview", "write"), help="Wochenaktion.")
    week_parser.add_argument("--from", dest="week_from", help="Startdatum der Wochenplanung, z. B. 2026-06-29.")
    week_parser.add_argument("--days", dest="week_days", type=int, default=7, help="Anzahl Tage ab Startdatum (Standard: 7).")
    return parser


def parse_cli_hhmm(value: str) -> datetime.time:
    return datetime.strptime(value, "%H:%M").time()


def rounded_now_slot(step_minutes: int = 15) -> datetime:
    now = datetime.now().astimezone().replace(tzinfo=None, second=0, microsecond=0)
    minutes = ((now.minute + step_minutes - 1) // step_minutes) * step_minutes
    return now.replace(minute=0) + timedelta(minutes=minutes)


def planning_start_for(args: argparse.Namespace, target_day: date) -> datetime | None:
    if getattr(args, "start_time", None):
        return datetime.combine(target_day, parse_cli_hhmm(args.start_time))
    if target_day == date.today() and (getattr(args, "from_now", False) or args.command in {"preview", "write"}):
        now_slot = rounded_now_slot()
        return datetime.combine(target_day, max(now_slot.time(), parse_cli_hhmm("09:00")))
    return None


def print_header(args: argparse.Namespace, target_day: date, planning_start: datetime | None = None) -> None:
    print("Nico Day Planner – Anwendungsschicht Phase 1")
    print("--------------------------------------------")
    print(f"Command: {args.command}")
    print(f"Zieltag: {args.day} ({target_day.isoformat()})")
    print(f"Modus: {args.mode}")
    if args.note:
        print(f"Hinweis: {args.note}")
    if planning_start is not None and planning_start.date() == target_day:
        label = "heute" if target_day == date.today() else target_day.isoformat()
        print(f"Planung für {label} ab {planning_start:%H:%M}. Frühere Zeitfenster werden nicht mehr beplant.")
    if args.from_time or args.to_time:
        start = args.from_time or "nicht gesetzt"
        end = args.to_time or "nicht gesetzt"
        print(f"Manueller Zeitraum: {start}–{end}")
    push_mode = getattr(args, "push", False) or getattr(args, "allow_late", False) or getattr(args, "mode", "") == "push"
    if getattr(args, "until", None):
        print(f"Planung bis {args.until} erlaubt.")
    if push_mode:
        print("Push-Modus aktiv: erhöhte Tageslast erlaubt.")
    if getattr(args, "allow_late", False):
        print("Hinweis: --allow-late ist veraltet. Bitte künftig --push verwenden.")
    if getattr(args, "allow_admin_until", None):
        print(f"Admin/Buchhaltung erlaubt bis {args.allow_admin_until}.")
    print("", flush=True)


def planner_environment(args: argparse.Namespace) -> dict[str, str]:
    env = os.environ.copy()
    env["NICO_PLANNER_MODE"] = args.mode
    if args.note is not None:
        env["NICO_PLANNER_NOTE"] = args.note
    if args.from_time is not None:
        env["NICO_PLANNER_FROM"] = args.from_time
    if args.to_time is not None:
        env["NICO_PLANNER_TO"] = args.to_time
    return env


def run_existing_planner(args: argparse.Namespace, target_day: date, planning_start: datetime | None = None) -> int:
    if args.command == "write" and os.environ.get("GOOGLE_CALENDAR_WRITE_ENABLED") == "true":
        calendar_id = os.environ.get(CALENDAR_ID_ENV_VAR, DEFAULT_CALENDAR_ID)
        try:
            deleted_week_events = delete_auto_events_for_date(
                target_day,
                calendar_id,
                marker=WEEK_AUTO_EVENT_MARKER,
                not_before=planning_start,
                not_after=datetime.combine(target_day, parse_cli_hhmm(args.until)) if getattr(args, "until", None) else None,
            )
        except GoogleCalendarReadError as exc:
            print(f"Tagesplanung gewinnt: Wochenplan-Events konnten nicht entfernt werden ({exc}).")
            return 1
        print(
            f"Tagesplanung gewinnt: {deleted_week_events} Wochenplan-Event(s) "
            f"für {target_day.isoformat()} entfernt."
        )

    command = [
        sys.executable,
        str(DRY_RUN_PLAN),
        "--source",
        "todoist",
        "--calendar-source",
        "google",
        "--date",
        target_day.isoformat(),
    ]

    if planning_start is not None:
        command.extend(["--start-time", planning_start.strftime("%H:%M")])
    if getattr(args, "until", None):
        command.extend(["--until", args.until])
    if getattr(args, "push", False) or args.mode == "push":
        command.append("--push")
    if getattr(args, "allow_late", False):
        command.append("--allow-late")
    if getattr(args, "allow_admin_until", None):
        command.extend(["--allow-admin-until", args.allow_admin_until])

    if args.command == "write":
        command.extend(["--write-calendar", "--replace-auto-events"])

    print("Starte bestehenden sicheren Planner ...")
    print("", flush=True)
    completed = subprocess.run(command, env=planner_environment(args), check=False)
    return completed.returncode


FEEDBACK_PATH = Path("data") / "planner_feedback.jsonl"
CATEGORY_PATTERN = re.compile(r"^\[(?P<category>[^\]]+)\]")


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def format_time_range(event: dict[str, Any]) -> str:
    start = parse_datetime(str(event.get("start", "")))
    end = parse_datetime(str(event.get("end", "")))
    if start and end:
        return f"{start:%H:%M}–{end:%H:%M}"
    return "Zeit unbekannt"


def event_duration_minutes(event: dict[str, Any]) -> int | None:
    start = parse_datetime(str(event.get("start", "")))
    end = parse_datetime(str(event.get("end", "")))
    if not start or not end or end <= start:
        return None
    return int((end - start).total_seconds() // 60)


def event_category(event: dict[str, Any]) -> str:
    title = str(event.get("title") or "")
    match = CATEGORY_PATTERN.match(title)
    if match:
        return match.group("category")
    description = str(event.get("description") or "")
    match = CATEGORY_PATTERN.search(description)
    if match:
        return match.group("category")
    return "unbekannt"


def clean_event_title(event: dict[str, Any]) -> str:
    title = str(event.get("title") or "Ohne Titel")
    return CATEGORY_PATTERN.sub("", title).strip() or title


def print_review_events(auto_events: list[dict[str, Any]]) -> None:
    print("Planner-Auto-Events für Review:")
    for index, event in enumerate(auto_events, start=1):
        duration = event_duration_minutes(event)
        duration_text = f"{duration} Min." if duration is not None else "Dauer unbekannt"
        print(
            f"{index}. {format_time_range(event)} | {clean_event_title(event)} "
            f"| Kategorie: {event_category(event)} | {duration_text}"
        )
    print("")


class ReviewInputUnavailable(RuntimeError):
    """Raised when an interactive review cannot read feedback input."""


def prompt_choice(prompt: str, mapping: dict[str, str], default: str) -> str:
    while True:
        try:
            raw = input(prompt).strip().lower()
        except EOFError:
            raise ReviewInputUnavailable from None
        if not raw:
            return default
        if raw in mapping:
            return mapping[raw]
        print(f"Ungültige Eingabe. Erlaubt: {', '.join(mapping)} oder Enter für {default}.")


def prompt_optional_note(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        raise ReviewInputUnavailable from None


def neutral_event_feedback(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event.get("id"),
        "title": clean_event_title(event),
        "category": event_category(event),
        "start": event.get("start"),
        "end": event.get("end"),
        "duration_minutes": event_duration_minutes(event),
        "feedback": {"status": "unknown", "duration": "unknown", "timing": "unknown", "note": ""},
    }


def collect_event_feedback(event: dict[str, Any], index: int) -> dict[str, Any]:
    print(f"Feedback für {index}. {format_time_range(event)} {clean_event_title(event)}")
    status = prompt_choice(
        "Status [d=done/p=partial/n=not_done/s=skipped] (Enter=skipped): ",
        {"d": "done", "done": "done", "p": "partial", "partial": "partial", "n": "not_done", "not_done": "not_done", "s": "skipped", "skipped": "skipped"},
        "skipped",
    )
    duration = prompt_choice(
        "Dauer [ok/short=too_short/long=too_long/u=unknown] (Enter=unknown): ",
        {"ok": "ok", "short": "too_short", "too_short": "too_short", "long": "too_long", "too_long": "too_long", "u": "unknown", "unknown": "unknown"},
        "unknown",
    )
    timing = prompt_choice(
        "Timing [good/late=too_late/early=too_early/bad/u=unknown] (Enter=unknown): ",
        {"good": "good", "late": "too_late", "too_late": "too_late", "early": "too_early", "too_early": "too_early", "bad": "bad", "u": "unknown", "unknown": "unknown"},
        "unknown",
    )
    note = prompt_optional_note("Notiz (optional): ")
    print("")
    return {
        "event_id": event.get("id"),
        "title": clean_event_title(event),
        "category": event_category(event),
        "start": event.get("start"),
        "end": event.get("end"),
        "duration_minutes": event_duration_minutes(event),
        "feedback": {"status": status, "duration": duration, "timing": timing, "note": note},
    }


def collect_day_feedback() -> dict[str, str]:
    print("Tagesfeedback")
    energy = prompt_choice("Energielevel [low/normal/high] (Enter=normal): ", {"low": "low", "normal": "normal", "high": "high"}, "normal")
    overall = prompt_choice("Plan insgesamt [light=too_light/good/full=too_full] (Enter=good): ", {"light": "too_light", "too_light": "too_light", "good": "good", "full": "too_full", "too_full": "too_full"}, "good")
    evening = prompt_choice("Abend [ok/full=too_full/late=too_late/n=not_relevant] (Enter=ok): ", {"ok": "ok", "full": "too_full", "too_full": "too_full", "late": "too_late", "too_late": "too_late", "n": "not_relevant", "not_relevant": "not_relevant"}, "ok")
    note = prompt_optional_note("Tagesnotiz (optional): ")
    return {"energy_level": energy, "overall_plan": overall, "evening": evening, "note": note}


def cli_day_feedback(args: argparse.Namespace) -> dict[str, str]:
    return {
        "energy_level": args.day_energy or "normal",
        "overall_plan": args.day_overall or "good",
        "evening": args.evening or "ok",
        "note": args.day_note or "",
    }


def has_cli_day_feedback(args: argparse.Namespace) -> bool:
    return bool(args.day_energy or args.day_overall or args.evening or args.day_note)


def save_review_session(session: dict[str, Any]) -> None:
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(session, ensure_ascii=False, sort_keys=True) + "\n")


def build_review_session(
    target_day: date,
    reviewed_events: list[dict[str, Any]],
    day_feedback: dict[str, str],
    source: str,
) -> dict[str, Any]:
    return {
        "date": target_day.isoformat(),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": source,
        "marker": AUTO_EVENT_MARKER,
        "events": reviewed_events,
        "day_feedback": day_feedback,
    }


def save_quick_day_review(args: argparse.Namespace, target_day: date, auto_events: list[dict[str, Any]]) -> None:
    reviewed_events = [neutral_event_feedback(event) for event in auto_events]
    day_feedback = cli_day_feedback(args)
    session = build_review_session(
        target_day,
        reviewed_events,
        day_feedback,
        "google_calendar_read_only_quick_day",
    )
    save_review_session(session)
    print("Quick-Day-Review gespeichert.")
    print(f"Datum: {target_day.isoformat()}")
    print(f"Planner-Auto-Events: {len(auto_events)}")
    print(
        "Tagesfeedback: "
        f"energy={day_feedback['energy_level']}, "
        f"overall={day_feedback['overall_plan']}, "
        f"evening={day_feedback['evening']}"
    )
    if day_feedback["note"]:
        print(f"Tagesnotiz: {day_feedback['note']}")
    print(f"Feedback gespeichert in {FEEDBACK_PATH}.")


def run_review(args: argparse.Namespace, target_day: date) -> int:
    print(f"Review für {target_day.isoformat()}")
    print("")
    print("Lade Google Calendar read-only für Review ...")
    print("Sicherheit: Review verändert weder Todoist noch Google Calendar.")
    print(f"Auto-Event-Marker: {AUTO_EVENT_MARKER}")
    print("")
    try:
        result = load_calendar_events_for_date(target_day)
    except GoogleCalendarReadError as exc:
        print(f"Google Calendar konnte nicht gelesen werden: {exc}")
        return 1

    print(f"- {result.status}")
    for detail in result.status_details:
        print(f"- {detail}")
    print("")

    auto_events = sorted(result.auto_events, key=lambda event: str(event.get("start") or ""))
    if not auto_events:
        print("Keine Planner-Auto-Events für diesen Tag gefunden.")
        if args.non_interactive:
            print("Nicht-interaktiver Review: kein Feedback abgefragt, nichts gespeichert.")
            return 0
        if args.quick_day:
            save_quick_day_review(args, target_day, auto_events)
            return 0
        return 0

    print_review_events(auto_events)
    if args.non_interactive:
        print("Nicht-interaktiver Review: kein Feedback abgefragt, nichts gespeichert.")
        return 0

    if args.quick_day:
        save_quick_day_review(args, target_day, auto_events)
        return 0

    try:
        reviewed_events = [collect_event_feedback(event, index) for index, event in enumerate(auto_events, start=1)]
        day_feedback = cli_day_feedback(args) if has_cli_day_feedback(args) else collect_day_feedback()
    except ReviewInputUnavailable:
        if has_cli_day_feedback(args):
            print("")
            print("Kein interaktives stdin verfügbar; speichere Tagesfeedback im Quick-Day-Modus.")
            save_quick_day_review(args, target_day, auto_events)
            return 0
        print("")
        print("Keine Eingabe verfügbar; Review abgebrochen und kein Feedback gespeichert.")
        print("Tipp: Nutze --non-interactive, um Planner-Auto-Events nur anzuzeigen.")
        return 0
    session = build_review_session(target_day, reviewed_events, day_feedback, "google_calendar_read_only")
    save_review_session(session)
    print(f"Feedback gespeichert in {FEEDBACK_PATH}.")
    return 0


WEEKDAY_NAMES = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
WEEK_CATEGORIES = ("Werkstatt", "Studio", "ALEGRA", "Buchhaltung", "Soundwerk", "Privat", "Haushalt", "LIVE")


def week_start_for(value: str | None) -> date:
    if value:
        return date.fromisoformat(value)
    today = date.today()
    if today.weekday() == 0:
        return today
    return today + timedelta(days=(7 - today.weekday()))


def fmt_week_time(value: datetime) -> str:
    return value.strftime("%H:%M")



def normalize_week_task_title(title: str) -> str:
    return normalize_calendar_coverage_title(title)


def normalize_week_task_title_variants(title: str) -> set[str]:
    normalized = normalize_week_task_title(title)
    variants = {normalized} if normalized else set()
    if ":" in normalized:
        suffix = normalized.split(":", 1)[1].strip()
        if suffix:
            variants.add(suffix)
    return variants


def todoist_id_from_description(description: str) -> str | None:
    match = re.search(r"Todoist[- ]Task[- ]ID\s*:\s*([^\s]+)", description, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


def day_auto_covered_keys(auto_events: list[Block]) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    titles: set[str] = set()
    for event in auto_events:
        if AUTO_EVENT_MARKER not in event.description:
            continue
        task_id = todoist_id_from_description(event.description)
        if task_id:
            ids.add(task_id)
        titles.update(normalize_week_task_title_variants(event.title))
    return ids, titles


def task_is_day_covered(task: Task, covered_ids: set[str], covered_titles: set[str]) -> bool:
    return task.id in covered_ids or task_is_manually_covered_by_titles(task, covered_titles)

def week_task_buckets(tasks: list[Task]) -> dict[str, list[Task]]:
    buckets = {category: [] for category in WEEK_CATEGORIES}
    for task in tasks:
        buckets.setdefault(task.category, []).append(task)
    for category, items in buckets.items():
        items.sort(key=lambda task: (0 if task.priority == "P1" else 1 if task.priority == "P2" else 2 if task.priority == "P3" else 3, task.duration_minutes, task.title))
    return buckets


def week_priority_rank(task: Task) -> tuple[int, int, str]:
    return (0 if task.priority == "P1" else 1 if task.priority == "P2" else 2 if task.priority == "P3" else 3, task.duration_minutes, task.title)


def available_week_tasks(buckets: dict[str, list[Task]], category: str, used_task_ids: set[str], covered_task_ids: set[str] | None = None, covered_titles: set[str] | None = None) -> list[Task]:
    covered_task_ids = covered_task_ids or set()
    covered_titles = covered_titles or set()
    if category == "Studio/ALEGRA":
        tasks = buckets.get("ALEGRA", []) + buckets.get("Studio", [])
        return sorted(
            (task for task in tasks if task.id not in used_task_ids and not task_is_day_covered(task, covered_task_ids, covered_titles)),
            key=lambda task: (week_priority_rank(task)[0], 0 if task.category == "ALEGRA" else 1, week_priority_rank(task)[1], task.title),
        )
    return [task for task in buckets.get(category, []) if task.id not in used_task_ids and not task_is_day_covered(task, covered_task_ids, covered_titles)]


def task_short_name(task: Task) -> str:
    title = task.title
    for separator in (":", "–"):
        title = title.split(separator)[0]
    return title.strip()


def mini_communication_tasks(tasks: list[Task], used_task_ids: set[str], covered_task_ids: set[str] | None = None, covered_titles: set[str] | None = None) -> list[Task]:
    covered_task_ids = covered_task_ids or set()
    covered_titles = covered_titles or set()
    communication_categories = {"Studio", "ALEGRA", "Buchhaltung"}
    return sorted(
        (
            task
            for task in tasks
            if task.id not in used_task_ids
            and not task_is_day_covered(task, covered_task_ids, covered_titles)
            and task.priority in {"P1", "P2"}
            and 15 <= task.duration_minutes <= 30
            and task.category in communication_categories
        ),
        key=week_priority_rank,
    )


def communication_label(tasks: list[Task]) -> str:
    categories = {task.category for task in tasks}
    if categories <= {"Studio"}:
        prefix = "Studio-Kommunikation"
    elif categories <= {"ALEGRA"}:
        prefix = "ALEGRA-Kommunikation"
    elif categories <= {"Buchhaltung"}:
        prefix = "Admin/Buchhaltung kurz"
    else:
        prefix = "Kommunikation/Admin"
    names = " / ".join(task_short_name(task) for task in tasks[:4])
    return f"{prefix}: {names}"


def task_block_title(task: Task, suffix: str = "") -> str:
    suffix_text = f" – {suffix}" if suffix else ""
    return f"{task.title}{suffix_text} [{task.category} {task.priority}]"


def is_week_divisible(task: Task) -> bool:
    text = f"{task.title} {task.notes}".lower()
    if "@nicht_teilbar" in text:
        return False
    return "@teilbar" in text or task.duration_minutes > 120


def week_block_minutes(task: Task) -> int:
    if task.duration_minutes <= 30:
        return max(15, task.duration_minutes)
    if task.duration_minutes <= 120:
        return max(60, task.duration_minutes)
    if is_week_divisible(task):
        return 120
    return task.duration_minutes


def block_as_fixed(block: PlannedBlock) -> Block:
    return Block(block.task.id, block.task.title, block.start, block.end, "Wochenplan", (block.task.category,))


def fixed_blocks_for_week_day(target_day: date) -> tuple[list[Block], list[str], bool, set[str], set[str]]:
    calendar_blocks, _status, _fallback, warnings, _details, auto_events = load_calendar_blocks_for_source("google", target_day)
    fixed = calendar_blocks + weekly_blocks(target_day) + [lunch_break_block(target_day)]
    if needs_homecoming_evening_pause(weekly_blocks(target_day)):
        fixed.append(homecoming_evening_pause_block(target_day))
    fixed += travel_blocks(fixed)
    covered_ids, covered_titles = day_auto_covered_keys(auto_events)
    has_day_plan = bool(covered_ids or covered_titles)
    covered_titles.update(manual_calendar_coverage_titles(calendar_blocks))
    return merge_overlapping(fixed), warnings, has_day_plan, covered_ids, covered_titles


def first_open_slot(target_day: date, blocks: list[Block], start_hhmm: str, end_hhmm: str, minutes: int) -> tuple[datetime, datetime] | None:
    start = datetime.combine(target_day, datetime.strptime(start_hhmm, "%H:%M").time())
    end_limit = datetime.combine(target_day, datetime.strptime(end_hhmm, "%H:%M").time())
    cursor = start
    for block in merge_overlapping(planning_blockers(blocks)):
        if block.end <= cursor or block.start >= end_limit:
            continue
        if block.start > cursor and int((block.start - cursor).total_seconds() // 60) >= minutes:
            return cursor, cursor + timedelta(minutes=minutes)
        cursor = max(cursor, block.end)
    if int((end_limit - cursor).total_seconds() // 60) >= minutes:
        return cursor, cursor + timedelta(minutes=minutes)
    return None


def first_week_structure_slot(target_day: date, blocks: list[Block], category: str, start_hhmm: str, end_hhmm: str, minutes: int) -> tuple[datetime, datetime] | None:
    category_terms = {
        "Werkstatt": ("werkstatt",),
        "Studio": ("studio",),
        "ALEGRA": ("alegra", "producing", "studio"),
        "Studio/ALEGRA": ("alegra", "producing", "studio"),
        "Soundwerk": ("soundwerk", "unterricht"),
        "Buchhaltung": ("admin", "buchhaltung", "rechnungen"),
    }.get(category, (category.lower(),))
    window_start = datetime.combine(target_day, datetime.strptime(start_hhmm, "%H:%M").time())
    window_end = datetime.combine(target_day, datetime.strptime(end_hhmm, "%H:%M").time())
    for block in blocks:
        if block.source != "Wochenstruktur":
            continue
        title = block.title.lower()
        if not any(term in title for term in category_terms):
            continue
        start = max(block.start, window_start)
        end = min(block.end, window_end)
        if int((end - start).total_seconds() // 60) < minutes:
            continue
        slot = first_open_slot(target_day, [other for other in blocks if other.id != block.id], fmt_week_time(start), fmt_week_time(end), minutes)
        if slot:
            return slot
    return None


def first_week_slot(target_day: date, blocks: list[Block], category: str, start_hhmm: str, end_hhmm: str, minutes: int) -> tuple[datetime, datetime] | None:
    return first_open_slot(target_day, blocks, start_hhmm, end_hhmm, minutes) or first_week_structure_slot(
        target_day, blocks, category, start_hhmm, end_hhmm, minutes
    )


def category_label(category: str, tasks: list[Task]) -> str:
    if category == "Werkstatt":
        names = [task.title.split(":")[0].split("–")[0] for task in tasks[:2]]
        return "Werkstatt-Fokus" + (": " + " / ".join(names) if names else ": offene Reparaturen")
    if category in {"Studio", "ALEGRA", "Studio/ALEGRA"}:
        names = [task_short_name(task) for task in tasks[:3]]
        return "Studio/ALEGRA-Fokus" + (": " + " / ".join(names) if names else "")
    if category == "Buchhaltung":
        names = [task_short_name(task) for task in tasks[:2]]
        return "Admin/Buchhaltung" + (": " + " / ".join(names) if names else "")
    if category == "Soundwerk":
        return "Soundwerk/Unterrichtsvorbereitung"
    if category == "Haushalt":
        return "Haushalt leicht"
    return f"{category}-Fokus"


def make_week_block(target_day: date, category: str, title: str, start: datetime, end: datetime) -> PlannedBlock:
    task_category = "ALEGRA" if category == "Studio/ALEGRA" else category
    return PlannedBlock(Task(f"week-{target_day.isoformat()}-{category}-{fmt_week_time(start)}", title, task_category, "P2", int((end-start).total_seconds()//60)), start, end)


def make_task_week_block(task: Task, start: datetime, end: datetime, title: str | None = None) -> PlannedBlock:
    return PlannedBlock(
        Task(
            task.id,
            title or task_block_title(task),
            task.category,
            task.priority,
            int((end - start).total_seconds() // 60),
            task.estimated,
            task.duration_source,
            task.notes,
        ),
        start,
        end,
    )


def build_week_plan(start_day: date, days: int) -> dict[str, Any]:
    tasks, source_status, _fallback, warnings, details = load_tasks_for_source("todoist")
    buckets = week_task_buckets(tasks)
    planned: dict[date, list[PlannedBlock]] = {}
    open_high = [task for task in tasks if task.priority in {"P1", "P2"}]
    estimated_count = sum(1 for task in tasks if task.estimated)
    used_task_ids: set[str] = set()
    day_covered_ids: set[str] = set()
    day_covered_titles: set[str] = set()
    skipped_days: dict[date, str] = {}
    for offset in range(days):
        day = start_day + timedelta(days=offset)
        fixed_result = fixed_blocks_for_week_day(day)
        if len(fixed_result) == 3:  # Backwards-compatible for older tests/mocks.
            fixed, day_warnings, has_day_plan = fixed_result  # type: ignore[misc]
            covered_ids, covered_titles = set(), set()
        else:
            fixed, day_warnings, has_day_plan, covered_ids, covered_titles = fixed_result
        day_covered_ids.update(covered_ids)
        day_covered_titles.update(covered_titles)
        warnings.extend(day_warnings)
        blocks: list[PlannedBlock] = []
        if has_day_plan:
            planned[day] = blocks
            skipped_days[day] = "genauer Tagesplan existiert bereits"
            continue
        templates = {
            0: [("Werkstatt", "09:00", "12:00", 90), ("Werkstatt", "13:00", "16:00", 90)],
            1: [("Werkstatt", "09:00", "12:00", 90), ("Soundwerk", "13:00", "14:00", 60)],
            2: [("Werkstatt", "09:00", "14:00", 90), ("Werkstatt", "09:00", "14:00", 90), ("Werkstatt", "13:00", "14:00", 60)],
            3: [("Werkstatt", "09:00", "12:00", 90), ("Werkstatt", "09:00", "12:00", 90), ("Studio/ALEGRA", "14:00", "17:00", 60), ("Studio/ALEGRA", "14:00", "17:00", 90), ("Studio/ALEGRA", "14:00", "17:00", 30)],
            4: [("Werkstatt", "09:00", "12:00", 90), ("Werkstatt", "09:00", "12:00", 90), ("Buchhaltung", "13:00", "16:00", 90)],
            5: [("Privat", "10:00", "13:00", 90)],
            6: [("Haushalt", "10:00", "13:00", 90), ("Buchhaltung", "15:00", "18:00", 60)],
        }.get(day.weekday(), [])
        for category, start_h, end_h, minutes in templates:
            if len(blocks) >= 4:
                break
            category_tasks = [task for task in available_week_tasks(buckets, category, used_task_ids, day_covered_ids, day_covered_titles) if task.priority in {"P1", "P2"}]
            if not category_tasks:
                continue
            task = category_tasks[0]
            block_minutes = min(minutes, week_block_minutes(task))
            if block_minutes > 120 and not is_week_divisible(task):
                continue
            slot = first_week_slot(day, fixed + [block_as_fixed(b) for b in blocks], category, start_h, end_h, block_minutes)
            if not slot and block_minutes > 30 and (is_week_divisible(task) or task.duration_minutes > block_minutes):
                block_minutes = max(30, min(60, minutes))
                slot = first_week_slot(day, fixed + [block_as_fixed(b) for b in blocks], category, start_h, end_h, block_minutes)
            if not slot:
                continue
            suffix = "Wochenblock" if task.duration_minutes > block_minutes else ""
            block = make_task_week_block(task, slot[0], slot[1], task_block_title(task, suffix))
            blocks.append(block)
            used_task_ids.add(task.id)
        if len(blocks) < 4:
            mini_tasks = mini_communication_tasks(tasks, used_task_ids, day_covered_ids, day_covered_titles)
            if mini_tasks:
                mini_windows = {
                    0: [("15:00", "16:00")],
                    1: [("13:00", "14:00"), ("15:30", "16:30")],
                    2: [("13:00", "14:00"), ("15:30", "16:30")],
                    3: [("17:00", "18:00")],
                    4: [("14:30", "16:00")],
                }.get(day.weekday(), [])
                for start_h, end_h in mini_windows:
                    if len(blocks) >= 4 or not mini_tasks:
                        break
                    bundled: list[Task] = []
                    bundled_minutes = 0
                    for task in mini_tasks:
                        if bundled_minutes + task.duration_minutes > 60:
                            continue
                        bundled.append(task)
                        bundled_minutes += task.duration_minutes
                        if bundled_minutes >= 30:
                            break
                    if not bundled:
                        continue
                    slot_minutes = max(30, bundled_minutes)
                    slot = first_open_slot(day, fixed + [block_as_fixed(b) for b in blocks], start_h, end_h, slot_minutes)
                    if not slot:
                        continue
                    title = f"{communication_label(bundled)} [{bundled[0].category} {bundled[0].priority}]"
                    block = make_week_block(day, "ALEGRA" if any(task.category == "ALEGRA" for task in bundled) else bundled[0].category, title, slot[0], slot[1])
                    blocks.append(block)
                    for task in bundled:
                        used_task_ids.add(task.id)
                    mini_tasks = mini_communication_tasks(tasks, used_task_ids, day_covered_ids, day_covered_titles)
        planned[day] = blocks
    covered_high_tasks = [task for task in open_high if task_is_day_covered(task, day_covered_ids, day_covered_titles)]
    day_covered_count = len(covered_high_tasks)
    unscheduled_high = [task for task in open_high if task.id not in used_task_ids and not task_is_day_covered(task, day_covered_ids, day_covered_titles)]
    week_warnings = list(dict.fromkeys(warnings))
    if unscheduled_high:
        too_large = [task for task in unscheduled_high if task.duration_minutes >= 90]
        no_focus = [task for task in unscheduled_high if task.category not in {"Werkstatt", "Studio", "ALEGRA", "Buchhaltung", "Soundwerk", "Privat", "Haushalt"}]
        day_plan = [task for task in unscheduled_high if task.duration_minutes <= 30]
        if too_large:
            week_warnings.append(f"{len(too_large)} P1/P2-Aufgabe(n) bleiben offen: zu groß für verfügbare Wochenblöcke.")
        if no_focus:
            week_warnings.append(f"{len(no_focus)} P1/P2-Aufgabe(n) bleiben offen: Kategorie hat diese Woche kein passendes Fokusfenster.")
        if day_plan:
            week_warnings.append(f"{len(day_plan)} kleine P1/P2-Aufgabe(n) bewusst für Tagesplanung offen gelassen.")
        other_open = len(unscheduled_high) - len(set(task.id for task in too_large + no_focus + day_plan))
        if other_open:
            week_warnings.append(f"{other_open} P1/P2-Aufgabe(n) passen voraussichtlich nicht in die grobe Woche.")
    if day_covered_count:
        week_warnings.append(f"{day_covered_count} Aufgabe(n) bereits durch Tagesplanung abgedeckt.")
        covered_names = ", ".join(task.title for task in covered_high_tasks[:5])
        suffix = " ..." if len(covered_high_tasks) > 5 else ""
        week_warnings.append(f"{day_covered_count} Aufgabe(n) bereits durch Tagesplanung oder manuelle Kalendertermine abgedeckt: {covered_names}{suffix}")
    if skipped_days:
        week_warnings.append(f"{len(skipped_days)} Tag(e) wegen bestehendem Tagesplan übersprungen.")
    if estimated_count > len(tasks) // 2:
        week_warnings.append("Viele Dauern sind geschätzt.")
    if not any(block.task.category == "Buchhaltung" for day_blocks in planned.values() for block in day_blocks) and buckets.get("Buchhaltung"):
        week_warnings.append("Admin-Aufgaben noch nicht terminiert.")
    if buckets.get("Werkstatt") and not any(block.task.category == "Werkstatt" for day_blocks in planned.values() for block in day_blocks):
        week_warnings.append("Keine passenden Werkstattfenster gefunden.")
    planned_high = sum(1 for day_blocks in planned.values() for block in day_blocks if block.task.priority in {"P1", "P2"})
    quality = max(0, min(10, 7 + min(2, planned_high // 3) + (1 if day_covered_count else 0) - min(4, len(unscheduled_high) // 2) - min(2, len(week_warnings) // 3)))
    status = "PRÜFEN" if week_warnings or quality < 8 else "SCHREIBBAR"
    return {"start": start_day, "days": days, "planned": planned, "warnings": week_warnings, "quality": quality, "status": status, "open_high": unscheduled_high, "source_status": source_status, "source_details": details, "skipped_days": skipped_days, "day_covered_count": day_covered_count}


def week_event_body(block: PlannedBlock) -> dict[str, Any]:
    todoist_id = block.task.id if not block.task.id.startswith("week-") else ""
    description_lines = [
        WEEK_AUTO_EVENT_MARKER,
        "Hinweis: Grobe Wochenplanung",
        f"Kategorie: {block.task.category}",
        f"Priorität: {block.task.priority}",
        "Ursprung: Todoist",
    ]
    if todoist_id:
        description_lines.append(f"Todoist-Task-ID: {todoist_id}")
    if block.task.parent_id:
        description_lines.append(f"Parent-Task-ID: {block.task.parent_id}")
    if block.task.parent_title:
        description_lines.append(f"Parent-Task-Titel: {block.task.parent_title}")
    body = {
        "summary": block.task.title,
        "description": "\n".join(description_lines),
        "start": _calendar_event_datetime(block.start),
        "end": _calendar_event_datetime(block.end),
    }
    color_id = google_calendar_color_id_for_category(block.task.category)
    if color_id:
        body["colorId"] = color_id
    return body


def print_week_card(plan: dict[str, Any]) -> None:
    start = plan["start"]
    end = start + timedelta(days=plan["days"] - 1)
    print("## Wochenplan Card")
    print(f"Woche: {start:%d.%m.}–{end:%d.%m.%Y}")
    print(f"Status: {plan['status']}")
    print(f"Wochenqualität: {plan['quality']}/10")
    print("")
    for day, blocks in plan["planned"].items():
        print(f"{WEEKDAY_NAMES[day.weekday()]} ({day:%d.%m.%Y}):")
        if day in plan.get("skipped_days", {}):
            print(f"- {WEEKDAY_NAMES[day.weekday()]} übersprungen: genauer Tagesplan existiert bereits.")
            print("")
            continue
        if not blocks:
            print("- leicht/frei oder keine passenden groben Fokusblöcke")
        for block in blocks:
            print(f"- {fmt_week_time(block.start)}–{fmt_week_time(block.end)} {block.task.title}")
        print("")
    print("Wichtigste offene P1/P2-Aufgaben:")
    for task in plan["open_high"][:8]:
        print(f"- {render_task(task)}")
    if not plan["open_high"]:
        print("- Keine")
    print("")
    print("Warnungen:")
    for warning in plan["warnings"]:
        print(f"- {warning}")
    if not plan["warnings"]:
        print("- Keine")


def run_week(args: argparse.Namespace) -> int:
    start = week_start_for(args.week_from)
    days = max(1, min(args.week_days, 14))
    plan = build_week_plan(start, days)
    print_week_card(plan)
    if args.week_command != "write":
        print("\nWochenvorschau: keine Events erstellt, gelöscht oder verändert.")
        return 0
    calendar_id = os.environ.get(CALENDAR_ID_ENV_VAR, DEFAULT_CALENDAR_ID)
    if os.environ.get("GOOGLE_CALENDAR_WRITE_ENABLED") != "true":
        print("\nWARNUNG: Schreiben blockiert: GOOGLE_CALENDAR_WRITE_ENABLED=true ist nicht gesetzt.")
        print("Es wurden keine Wochenplan-Events erstellt, gelöscht oder verändert.")
        return 0
    created = 0
    deleted = 0
    try:
        for offset in range(days):
            deleted += delete_auto_events_for_date(start + timedelta(days=offset), calendar_id, marker=WEEK_AUTO_EVENT_MARKER)
        for blocks in plan["planned"].values():
            for block in blocks:
                create_calendar_event(calendar_id, week_event_body(block))
                created += 1
    except GoogleCalendarReadError as exc:
        print(f"\nGoogle Calendar Schreiben fehlgeschlagen ({exc}) – keine weiteren Events geschrieben.")
        return 1
    print(f"\nWochenplan geschrieben: {created} Event(s) erstellt, {deleted} alte Wochenplan-Event(s) ersetzt.")
    print(f"Marker: {WEEK_AUTO_EVENT_MARKER}; Tagesplan-Marker {AUTO_EVENT_MARKER} wurde nicht gelöscht.")
    return 0


def validate_command_day_combination(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.command == "week":
        if args.week_days < 1:
            parser.error("week --days muss mindestens 1 sein.")
        return
    if args.command in {"preview", "write"}:
        if args.day not in {"today", "tomorrow"}:
            parser.error("preview/write unterstützen die Zieltage today oder tomorrow.")
        if args.from_now and args.start_time:
            parser.error("Bitte entweder --from-now oder --start-time verwenden, nicht beides.")
        for attr, label in (("start_time", "--start-time"), ("until", "--until"), ("allow_admin_until", "--allow-admin-until")):
            value = getattr(args, attr, None)
            if value:
                try:
                    parse_cli_hhmm(value)
                except ValueError:
                    parser.error(f"{label} muss im Format HH:MM angegeben werden.")
        if args.until and args.allow_admin_until and parse_cli_hhmm(args.allow_admin_until) > parse_cli_hhmm(args.until):
            parser.error("--allow-admin-until darf nicht später als --until sein.")
    if args.command == "review":
        try:
            target_date_for(args.day)
        except ValueError as exc:
            parser.error(str(exc))


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    validate_command_day_combination(parser, args)

    if args.command == "week":
        print("Nico Day Planner – Wochenplanung Phase 1")
        print("----------------------------------------")
        print(f"Command: week {args.week_command}")
        print(f"Start: {week_start_for(args.week_from).isoformat()}")
        print(f"Tage: {max(1, min(args.week_days, 14))}")
        print("")
        return run_week(args)

    target_day = target_date_for(args.day)

    planning_start = planning_start_for(args, target_day)

    print_header(args, target_day, planning_start)

    if args.command == "review":
        return run_review(args, target_day)

    return run_existing_planner(args, target_day, planning_start)


if __name__ == "__main__":
    raise SystemExit(main())
