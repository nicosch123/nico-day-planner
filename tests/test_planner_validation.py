import sys
import unittest
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from dry_run_plan import (  # noqa: E402
    Block,
    PlanResult,
    PlannedBlock,
    Task,
    TimeWindow,
    apply_calendar_write,
    build_plan,
    validate_planned_blocks,
)
import dry_run_plan  # noqa: E402
from google_calendar_client import _event_to_block  # noqa: E402


class PlannerValidationRegressionTest(unittest.TestCase):
    def _collision_plan(self) -> PlanResult:
        target_day = date(2026, 6, 29)
        manual_block = Block(
            id="manual-lesson",
            title="Noah Striegel",
            start=datetime(2026, 6, 29, 14, 0),
            end=datetime(2026, 6, 29, 15, 0),
            source="Google Calendar",
        )
        task = Task(
            id="todoist-1",
            title="Kabelpeitsche Drums",
            category="Werkstatt",
            priority="P2",
            duration_minutes=90,
        )
        plan = PlanResult(
            target_day=target_day,
            source_status="test",
            fixed_blocks=[manual_block],
            free_windows=[TimeWindow(datetime(2026, 6, 29, 14, 30), datetime(2026, 6, 29, 16, 0))],
            planned_blocks=[PlannedBlock(task, datetime(2026, 6, 29, 14, 30), datetime(2026, 6, 29, 16, 0))],
            not_scheduled=[],
            split_suggestions=[],
            capacity_minutes=90,
            planned_minutes=90,
            source="todoist",
            calendar_source="google",
            calendar_status="test",
        )
        return plan

    def test_manual_google_block_collision_fails_validation(self) -> None:
        plan = self._collision_plan()

        errors = validate_planned_blocks(plan)

        self.assertEqual(len(errors), 1)
        self.assertIn("Kabelpeitsche Drums", errors[0])
        self.assertIn("Noah Striegel", errors[0])

    def test_calendar_write_is_blocked_when_validation_finds_manual_collision(self) -> None:
        plan = self._collision_plan()
        original_delete = dry_run_plan.delete_auto_events_for_date
        original_create = dry_run_plan.create_calendar_event
        original_gate = dry_run_plan.os.environ.get("GOOGLE_CALENDAR_WRITE_ENABLED")

        def fail_delete(*_args: object, **_kwargs: object) -> int:
            raise AssertionError("delete_auto_events_for_date must not run when validation fails")

        def fail_create(*_args: object, **_kwargs: object) -> str:
            raise AssertionError("create_calendar_event must not run when validation fails")

        try:
            dry_run_plan.delete_auto_events_for_date = fail_delete
            dry_run_plan.create_calendar_event = fail_create
            dry_run_plan.os.environ["GOOGLE_CALENDAR_WRITE_ENABLED"] = "true"

            apply_calendar_write(plan, write_calendar=True, replace_auto_events=True)
        finally:
            dry_run_plan.delete_auto_events_for_date = original_delete
            dry_run_plan.create_calendar_event = original_create
            if original_gate is None:
                dry_run_plan.os.environ.pop("GOOGLE_CALENDAR_WRITE_ENABLED", None)
            else:
                dry_run_plan.os.environ["GOOGLE_CALENDAR_WRITE_ENABLED"] = original_gate

        self.assertFalse(plan.calendar_write_enabled)
        self.assertEqual(plan.calendar_created_events, 0)
        self.assertEqual(plan.calendar_deleted_events, 0)
        self.assertIn("Schreiben abgebrochen", plan.calendar_write_blocked_warning)

    def test_timed_google_datetime_event_becomes_hard_blocker(self) -> None:
        block, note = _event_to_block(
            {
                "id": "manual-lesson",
                "summary": "Noah Striegel",
                "start": {"dateTime": "2026-06-29T14:00:00+02:00", "timeZone": "Europe/Berlin"},
                "end": {"dateTime": "2026-06-29T15:00:00+02:00", "timeZone": "Europe/Berlin"},
            },
            date(2026, 6, 29),
        )

        self.assertIsNone(note)
        self.assertIsNotNone(block)
        assert block is not None
        self.assertEqual(block["start"], "2026-06-29T14:00:00")
        self.assertEqual(block["end"], "2026-06-29T15:00:00")

    def test_transparent_timed_google_event_is_not_blocker(self) -> None:
        block, note = _event_to_block(
            {
                "id": "transparent-reminder",
                "summary": "Reminder",
                "transparency": "transparent",
                "start": {"dateTime": "2026-06-29T14:00:00+02:00"},
                "end": {"dateTime": "2026-06-29T15:00:00+02:00"},
            },
            date(2026, 6, 29),
        )

        self.assertIsNone(block)
        self.assertIn("transparency=transparent", note or "")

    def test_lunch_break_blocks_midday_werkstatt_task(self) -> None:
        original_load_tasks = dry_run_plan.load_tasks_for_source
        original_load_calendar = dry_run_plan.load_calendar_blocks_for_source
        try:
            dry_run_plan.load_tasks_for_source = lambda _source: (
                [
                    Task("werkstatt-1", "Vormittag Werkstatt", "Werkstatt", "P1", 90),
                    Task("werkstatt-2", "Fehler provozieren Mittag Werkstatt", "Werkstatt", "P2", 90),
                ],
                "test",
                False,
                [],
                (),
            )
            dry_run_plan.load_calendar_blocks_for_source = lambda _calendar_source, _target_day: (
                [
                    Block(
                        id="lesson",
                        title="Noah Striegel",
                        start=datetime(2026, 6, 29, 14, 0),
                        end=datetime(2026, 6, 29, 14, 30),
                        source="Google Calendar",
                    ),
                    Block(
                        id="lesson-2",
                        title="Mia Sophie",
                        start=datetime(2026, 6, 29, 16, 0),
                        end=datetime(2026, 6, 29, 16, 30),
                        source="Google Calendar",
                    ),
                ],
                "test",
                False,
                [],
                (),
            )

            plan = build_plan("todoist", date(2026, 6, 29), "google")
        finally:
            dry_run_plan.load_tasks_for_source = original_load_tasks
            dry_run_plan.load_calendar_blocks_for_source = original_load_calendar

        self.assertEqual(len(plan.planned_blocks), 2)
        self.assertEqual(plan.planned_blocks[0].start, datetime(2026, 6, 29, 9, 0))
        self.assertEqual(plan.planned_blocks[0].end, datetime(2026, 6, 29, 10, 30))
        self.assertEqual(plan.planned_blocks[1].start, datetime(2026, 6, 29, 10, 45))
        self.assertEqual(plan.planned_blocks[1].end, datetime(2026, 6, 29, 11, 45))
        self.assertIn("Teil 1", plan.planned_blocks[1].task.title)

    def test_validation_requires_buffer_before_manual_event(self) -> None:
        target_day = date(2026, 6, 29)
        manual_block = Block(
            id="manual-lesson",
            title="Noah Striegel",
            start=datetime(2026, 6, 29, 14, 0),
            end=datetime(2026, 6, 29, 14, 30),
            source="Google Calendar",
        )
        task = Task("todoist-1", "Admin", "Privat", "P2", 30)
        plan = PlanResult(
            target_day=target_day,
            source_status="test",
            fixed_blocks=[manual_block],
            free_windows=[],
            planned_blocks=[PlannedBlock(task, datetime(2026, 6, 29, 13, 30), datetime(2026, 6, 29, 14, 0))],
            not_scheduled=[],
            split_suggestions=[],
            capacity_minutes=30,
            planned_minutes=30,
            source="todoist",
            calendar_source="google",
            calendar_status="test",
        )

        errors = validate_planned_blocks(plan)

        self.assertEqual(len(errors), 1)
        self.assertIn("Zu wenig Puffer", errors[0])

    def test_lesson_gap_accepts_small_private_task(self) -> None:
        original_load_tasks = dry_run_plan.load_tasks_for_source
        original_load_calendar = dry_run_plan.load_calendar_blocks_for_source
        try:
            dry_run_plan.load_tasks_for_source = lambda _source: (
                [
                    Task("studio-1", "Lange Studioaufgabe", "Studio", "P1", 60),
                    Task("privat-1", "Private Ablage", "Privat", "P4", 30),
                ],
                "test",
                False,
                [],
                (),
            )
            dry_run_plan.load_calendar_blocks_for_source = lambda _calendar_source, _target_day: (
                [
                    Block(
                        id="lesson-1",
                        title="Noah Striegel",
                        start=datetime(2026, 6, 29, 14, 0),
                        end=datetime(2026, 6, 29, 14, 30),
                        source="Google Calendar",
                    ),
                    Block(
                        id="lesson-2",
                        title="Mia Sophie",
                        start=datetime(2026, 6, 29, 16, 0),
                        end=datetime(2026, 6, 29, 16, 30),
                        source="Google Calendar",
                    ),
                ],
                "test",
                False,
                [],
                (),
            )

            plan = build_plan("todoist", date(2026, 6, 29), "google")
        finally:
            dry_run_plan.load_tasks_for_source = original_load_tasks
            dry_run_plan.load_calendar_blocks_for_source = original_load_calendar

        self.assertIn("Private Ablage", [block.task.title for block in plan.planned_blocks])
        self.assertEqual(plan.planned_blocks[0].start, datetime(2026, 6, 29, 14, 30))
        self.assertEqual(plan.planned_blocks[0].end, datetime(2026, 6, 29, 15, 0))

    def test_full_monday_limits_multiple_large_studio_evening_blocks(self) -> None:
        original_load_tasks = dry_run_plan.load_tasks_for_source
        original_load_calendar = dry_run_plan.load_calendar_blocks_for_source
        try:
            dry_run_plan.load_tasks_for_source = lambda _source: (
                [
                    Task("studio-1", "Studio-Website", "Studio", "P2", 90),
                    Task("studio-2", "Aufnahme aufräumen", "Studio", "P3", 90),
                    Task("privat-1", "Private Ablage", "Privat", "P4", 30),
                ],
                "test",
                False,
                [],
                (),
            )
            dry_run_plan.load_calendar_blocks_for_source = lambda _calendar_source, _target_day: (
                [],
                "test",
                False,
                [],
                (),
            )

            plan = build_plan("todoist", date(2026, 6, 29), "google")
        finally:
            dry_run_plan.load_tasks_for_source = original_load_tasks
            dry_run_plan.load_calendar_blocks_for_source = original_load_calendar

        large_evening = [
            block
            for block in plan.planned_blocks
            if block.start >= datetime(2026, 6, 29, 17, 0) and block.task.duration_minutes >= 60
        ]
        self.assertLessEqual(len(large_evening), 1)
        self.assertTrue(any("Große Abendblöcke" in detail for detail in plan.load_diagnostics))

    def test_partial_blocks_are_limited_to_two_per_day(self) -> None:
        original_load_tasks = dry_run_plan.load_tasks_for_source
        original_load_calendar = dry_run_plan.load_calendar_blocks_for_source
        try:
            dry_run_plan.load_tasks_for_source = lambda _source: (
                [
                    Task("werkstatt-1", "Fehler provozieren A", "Werkstatt", "P2", 90),
                    Task("werkstatt-2", "Sichtprüfung B", "Werkstatt", "P2", 90),
                    Task("werkstatt-3", "Messnotizen C", "Werkstatt", "P2", 90),
                ],
                "test",
                False,
                [],
                (),
            )
            dry_run_plan.load_calendar_blocks_for_source = lambda _calendar_source, _target_day: (
                [
                    Block("termin-1", "Termin 1", datetime(2026, 6, 29, 10, 0), datetime(2026, 6, 29, 10, 15), "Google Calendar"),
                    Block("termin-2", "Termin 2", datetime(2026, 6, 29, 11, 15), datetime(2026, 6, 29, 11, 30), "Google Calendar"),
                    Block("termin-3", "Termin 3", datetime(2026, 6, 29, 14, 0), datetime(2026, 6, 29, 14, 30), "Google Calendar"),
                ],
                "test",
                False,
                [],
                (),
            )

            plan = build_plan("todoist", date(2026, 6, 29), "google")
        finally:
            dry_run_plan.load_tasks_for_source = original_load_tasks
            dry_run_plan.load_calendar_blocks_for_source = original_load_calendar

        partials = [block for block in plan.planned_blocks if "Teilblock:" in block.task.notes]
        self.assertLessEqual(len(partials), 2)


if __name__ == "__main__":
    unittest.main()
