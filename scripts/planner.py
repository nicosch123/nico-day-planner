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

from google_calendar_client import AUTO_EVENT_MARKER, GoogleCalendarReadError, load_calendar_events_for_date


SUPPORTED_MODES = (
    "normal",
    "light",
    "focus-workshop",
    "admin-evening",
    "no-evening",
    "push",
)

SCRIPT_DIR = Path(__file__).resolve().parent
DRY_RUN_PLAN = SCRIPT_DIR / "dry_run_plan.py"


def target_date_for(value: str) -> date:
    """Return the concrete target date for a supported relative day."""
    today = date.today()
    if value == "tomorrow":
        return today + timedelta(days=1)
    if value == "yesterday":
        return today - timedelta(days=1)
    raise ValueError(f"Unsupported target day: {value}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Nico Day Planner CLI – friendly wrapper around the safe planner.",
    )
    parser.add_argument(
        "command",
        choices=("preview", "write", "review"),
        help="Aktion: preview zeigt den Plan, write schreibt sicher gated Auto-Events, review erfasst Feedback zu Planner-Auto-Events.",
    )
    parser.add_argument(
        "day",
        choices=("tomorrow", "yesterday"),
        help="Zieltag. preview/write unterstützen tomorrow, review unterstützt yesterday.",
    )
    parser.add_argument(
        "--mode",
        choices=SUPPORTED_MODES,
        default="normal",
        help="Planungsmodus für spätere Phasen. Wird in Phase 1 angezeigt und als NICO_PLANNER_MODE übergeben.",
    )
    parser.add_argument(
        "--note",
        help="Freitext-Hinweis für spätere Phasen. Wird in Phase 1 angezeigt und als NICO_PLANNER_NOTE übergeben.",
    )
    parser.add_argument(
        "--from",
        dest="from_time",
        help="Manueller Startzeitpunkt, z. B. 09:00. Wird in Phase 1 angezeigt und als NICO_PLANNER_FROM übergeben.",
    )
    parser.add_argument(
        "--to",
        dest="to_time",
        help="Manueller Endzeitpunkt, z. B. 21:00. Wird in Phase 1 angezeigt und als NICO_PLANNER_TO übergeben.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Review: Planner-Auto-Events nur anzeigen, kein Feedback abfragen und nichts speichern.",
    )
    return parser


def print_header(args: argparse.Namespace, target_day: date) -> None:
    print("Nico Day Planner – Anwendungsschicht Phase 1")
    print("--------------------------------------------")
    print(f"Command: {args.command}")
    print(f"Zieltag: {args.day} ({target_day.isoformat()})")
    print(f"Modus: {args.mode}")
    if args.note:
        print(f"Hinweis: {args.note}")
    if args.from_time or args.to_time:
        start = args.from_time or "nicht gesetzt"
        end = args.to_time or "nicht gesetzt"
        print(f"Manueller Zeitraum: {start}–{end}")
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


def run_existing_planner(args: argparse.Namespace, target_day: date) -> int:
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


def prompt_choice(prompt: str, mapping: dict[str, str], default: str) -> str:
    while True:
        raw = input(prompt).strip().lower()
        if not raw:
            return default
        if raw in mapping:
            return mapping[raw]
        print(f"Ungültige Eingabe. Erlaubt: {', '.join(mapping)} oder Enter für {default}.")


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
    note = input("Notiz (optional): ").strip()
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
    note = input("Tagesnotiz (optional): ").strip()
    return {"energy_level": energy, "overall_plan": overall, "evening": evening, "note": note}


def save_review_session(session: dict[str, Any]) -> None:
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(session, ensure_ascii=False, sort_keys=True) + "\n")


def run_review(args: argparse.Namespace, target_day: date) -> int:
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
        return 0

    print_review_events(auto_events)
    if args.non_interactive:
        print("Nicht-interaktiver Review: kein Feedback abgefragt, nichts gespeichert.")
        return 0

    reviewed_events = [collect_event_feedback(event, index) for index, event in enumerate(auto_events, start=1)]
    day_feedback = collect_day_feedback()
    session = {
        "date": target_day.isoformat(),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "google_calendar_read_only",
        "marker": AUTO_EVENT_MARKER,
        "events": reviewed_events,
        "day_feedback": day_feedback,
    }
    save_review_session(session)
    print(f"Feedback gespeichert in {FEEDBACK_PATH}.")
    return 0


def validate_command_day_combination(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.command in {"preview", "write"} and args.day != "tomorrow":
        parser.error("preview/write unterstützen in Phase 1 nur den Zieltag 'tomorrow'.")
    if args.command == "review" and args.day != "yesterday":
        parser.error("review unterstützt in Phase 1 nur den Zieltag 'yesterday'.")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    validate_command_day_combination(parser, args)
    target_day = target_date_for(args.day)

    print_header(args, target_day)

    if args.command == "review":
        return run_review(args, target_day)

    return run_existing_planner(args, target_day)


if __name__ == "__main__":
    raise SystemExit(main())
