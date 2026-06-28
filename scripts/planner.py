#!/usr/bin/env python3
"""Friendly application-layer CLI for the Nico Day Planner.

Phase 1 wraps the existing dry-run planner without changing its safety gates.
Calendar writes are still controlled exclusively by dry_run_plan.py via the
existing --write-calendar flag and GOOGLE_CALENDAR_WRITE_ENABLED environment
variable.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path


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
        help="Aktion: preview zeigt den Plan, write schreibt sicher gated Auto-Events, review ist Phase-1-Platzhalter.",
    )
    parser.add_argument(
        "day",
        choices=("tomorrow", "yesterday"),
        help="Zieltag. Phase 1 unterstützt preview/write für tomorrow und review für yesterday.",
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


def run_review_placeholder(args: argparse.Namespace, target_day: date) -> int:
    print("Review ist in Phase 1 ein sicherer Platzhalter.")
    print("Es werden keine Todoist-Aufgaben verändert.")
    print("Es werden keine Google-Calendar-Termine verändert.")
    print("")
    print(
        "In Phase 2/3 soll Review geplante Auto-Events auswerten und Feedback "
        "für zukünftige Planungen erfassen."
    )
    print(f"Review-Zieltag: {args.day} ({target_day.isoformat()})")
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
        return run_review_placeholder(args, target_day)

    return run_existing_planner(args, target_day)


if __name__ == "__main__":
    raise SystemExit(main())
