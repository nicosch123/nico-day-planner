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
    RejectedTask,
    Task,
    TimeWindow,
    apply_calendar_write,
    build_plan,
    _calendar_event_body,
    validate_planned_blocks,
)
import dry_run_plan  # noqa: E402
from google_calendar_client import _event_to_block  # noqa: E402
import planner  # noqa: E402


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

    def test_calendar_event_body_sets_category_color_ids(self) -> None:
        start = datetime(2026, 6, 29, 9, 0)
        end = datetime(2026, 6, 29, 10, 0)

        werkstatt_body = _calendar_event_body(
            PlannedBlock(Task("todoist-werkstatt", "Kabel löten", "Werkstatt", "P2", 60), start, end)
        )
        studio_body = _calendar_event_body(
            PlannedBlock(Task("todoist-studio", "Feedback prüfen", "Studio", "P1", 60), start, end)
        )

        self.assertEqual(werkstatt_body["colorId"], "10")
        self.assertEqual(studio_body["colorId"], "2")

    def test_calendar_event_body_omits_unknown_category_color_id(self) -> None:
        body = _calendar_event_body(
            PlannedBlock(
                Task("todoist-unknown", "Unbekannte Aufgabe", "Unbekannt", "P4", 30),
                datetime(2026, 6, 29, 9, 0),
                datetime(2026, 6, 29, 9, 30),
            )
        )

        self.assertNotIn("colorId", body)

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

        self.assertEqual(len(plan.planned_blocks), 3)
        self.assertEqual(plan.planned_blocks[0].start, datetime(2026, 6, 29, 9, 0))
        self.assertEqual(plan.planned_blocks[0].end, datetime(2026, 6, 29, 10, 30))
        self.assertEqual(plan.planned_blocks[1].start, datetime(2026, 6, 29, 10, 45))
        self.assertEqual(plan.planned_blocks[1].end, datetime(2026, 6, 29, 11, 45))
        self.assertIn("Teil 1", plan.planned_blocks[1].task.title)
        self.assertEqual(plan.planned_blocks[2].start, datetime(2026, 6, 29, 13, 0))
        self.assertIn("Teil 2", plan.planned_blocks[2].task.title)

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
        self.assertFalse(
            [
                block
                for block in plan.planned_blocks
                if datetime(2026, 6, 29, 17, 0) <= block.start < datetime(2026, 6, 29, 19, 30)
            ]
        )
        self.assertTrue(
            any(block.title == "Heimfahrt / Essen / Duschen / Pause" for block in plan.fixed_blocks)
        )
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

        partial_task_ids = {block.task.id for block in plan.planned_blocks if "Teilblock:" in block.task.notes}
        self.assertLessEqual(len(partial_task_ids), 2)

    def test_partial_task_continues_after_lunch_before_lower_priority_work(self) -> None:
        original_load_tasks = dry_run_plan.load_tasks_for_source
        original_load_calendar = dry_run_plan.load_calendar_blocks_for_source
        try:
            dry_run_plan.load_tasks_for_source = lambda _source: (
                [
                    Task("echolette", "Echolette NG51 fertigziehen", "Werkstatt", "P1", 120),
                    Task("kabel", "Alexander Kabellöten", "Werkstatt", "P2", 45),
                ],
                "test",
                False,
                [],
                (),
            )
            dry_run_plan.load_calendar_blocks_for_source = lambda _calendar_source, _target_day: (
                [
                    Block(
                        "termin-vormittag",
                        "Termin vor Mittag",
                        datetime(2026, 6, 29, 10, 15),
                        datetime(2026, 6, 29, 12, 0),
                        "Google Calendar",
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

        titles = [block.task.title for block in plan.planned_blocks]
        self.assertIn("Echolette NG51 fertigziehen – Teil 1", titles)
        self.assertIn("Echolette NG51 fertigziehen – Teil 2", titles)
        self.assertLess(
            titles.index("Echolette NG51 fertigziehen – Teil 2"),
            titles.index("Alexander Kabellöten"),
        )
        self.assertTrue(any("Fortsetzung von Teilblock: Echolette NG51 fertigziehen" in detail for detail in plan.load_diagnostics))

    def test_render_plan_card_limits_warnings_and_open_high_priority_tasks(self) -> None:
        planned = PlannedBlock(
            Task("planned", "Kurzaufgabe", "Privat", "P1", 15),
            datetime(2026, 6, 29, 9, 0),
            datetime(2026, 6, 29, 9, 15),
        )
        open_tasks = [
            RejectedTask(Task(f"open-{index}", f"Offen {index}", "Studio", "P1", 30), "Testgrund")
            for index in range(6)
        ]
        plan = PlanResult(
            target_day=date(2026, 6, 29),
            source_status="test",
            fixed_blocks=[],
            free_windows=[],
            planned_blocks=[planned],
            not_scheduled=open_tasks,
            split_suggestions=[],
            capacity_minutes=60,
            planned_minutes=15,
            source="json",
            calendar_source="json",
            calendar_status="test",
            warnings=[f"Warnung {index}" for index in range(6)],
        )
        rendered = dry_run_plan.render_plan(plan)

        self.assertTrue(rendered.startswith("# Plan Card"))
        self.assertIn("- Status: PRÜFEN", rendered)
        self.assertEqual(rendered.count("  - Warnung "), 5)
        self.assertEqual(rendered.count("  - Offen "), 5)

    def test_render_plan_separates_workshop_availability_from_hard_blockers(self) -> None:
        target_day = date(2026, 6, 29)
        weekly = Block(
            "weekly-workshop",
            "Werkstatt Mengen",
            datetime(2026, 6, 29, 9, 0),
            datetime(2026, 6, 29, 17, 0),
            "Wochenstruktur",
            categories=("Werkstatt",),
            location="Mengen",
        )
        manual = Block(
            "manual",
            "Noah Striegel",
            datetime(2026, 6, 29, 14, 0),
            datetime(2026, 6, 29, 14, 30),
            "Google Calendar",
        )
        auto_event = Block(
            "auto",
            "[Studio] Bestehender Auto-Plan",
            datetime(2026, 6, 29, 15, 0),
            datetime(2026, 6, 29, 15, 30),
            "Google Calendar",
        )
        plan = PlanResult(
            target_day=target_day,
            source_status="test",
            fixed_blocks=[weekly, manual],
            free_windows=[TimeWindow(datetime(2026, 6, 29, 15, 0), datetime(2026, 6, 29, 15, 30))],
            planned_blocks=[],
            not_scheduled=[],
            split_suggestions=[],
            capacity_minutes=30,
            planned_minutes=0,
            source="json",
            calendar_source="google",
            calendar_status="test",
            existing_auto_events=[auto_event],
        )
        rendered = dry_run_plan.render_plan(plan)
        hard_section = rendered.split("### Harte Blocker", 1)[1].split("### Verfügbarkeit / Wochenstruktur", 1)[0]
        availability_section = rendered.split("### Verfügbarkeit / Wochenstruktur", 1)[1].split(
            "### Bestehende Planner-Auto-Events", 1
        )[0]
        auto_section = rendered.split("### Bestehende Planner-Auto-Events", 1)[1].split("### Unterrichtslücken / kleine Lücken", 1)[0]

        self.assertIn("Noah Striegel", hard_section)
        self.assertNotIn("Werkstatt Mengen", hard_section)
        self.assertIn("Werkstatt Mengen", availability_section)
        self.assertIn("Bestehender Auto-Plan", auto_section)


class WeeklyPlannerSafetyTest(unittest.TestCase):
    def test_delete_auto_events_for_date_accepts_separate_week_marker(self) -> None:
        import google_calendar_client

        seen: list[tuple[str, str, str | None, dict[str, str] | None]] = []
        original_request = google_calendar_client._google_calendar_rest_request
        original_credentials = google_calendar_client.os.environ.get(google_calendar_client.CREDENTIALS_ENV_VAR)

        def fake_request(_credentials: dict[str, object], _scopes: tuple[str, ...], method: str, url: str, body: dict[str, str] | None = None) -> dict[str, object]:
            seen.append((method, url, body.get("description") if body else None, body))
            if method == "GET":
                return {
                    "items": [
                        {"id": "day", "description": "NICO_DAY_PLANNER_AUTO"},
                        {"id": "week", "description": "NICO_WEEK_PLANNER_AUTO"},
                        {"id": "manual", "description": "manual"},
                    ]
                }
            return {}

        try:
            google_calendar_client.os.environ[google_calendar_client.CREDENTIALS_ENV_VAR] = '{"dummy": true}'
            google_calendar_client._google_calendar_rest_request = fake_request
            deleted = google_calendar_client.delete_auto_events_for_date(
                date(2026, 6, 29),
                "primary",
                marker=google_calendar_client.WEEK_AUTO_EVENT_MARKER,
            )
        finally:
            google_calendar_client._google_calendar_rest_request = original_request
            if original_credentials is None:
                google_calendar_client.os.environ.pop(google_calendar_client.CREDENTIALS_ENV_VAR, None)
            else:
                google_calendar_client.os.environ[google_calendar_client.CREDENTIALS_ENV_VAR] = original_credentials

        self.assertEqual(deleted, 1)
        delete_urls = [url for method, url, _description, _body in seen if method == "DELETE"]
        self.assertEqual(len(delete_urls), 1)
        self.assertIn("/week", delete_urls[0])

    def test_day_write_deletes_only_week_events_for_target_day_before_day_planner(self) -> None:
        calls: list[tuple[date, str]] = []
        original_delete = planner.delete_auto_events_for_date
        original_run = planner.subprocess.run
        original_gate = planner.os.environ.get("GOOGLE_CALENDAR_WRITE_ENABLED")

        def fake_delete(target_day: date, _calendar_id: str, marker: str = "") -> int:
            calls.append((target_day, marker))
            return 2

        class Completed:
            returncode = 0

        try:
            planner.delete_auto_events_for_date = fake_delete
            planner.subprocess.run = lambda *_args, **_kwargs: Completed()
            planner.os.environ["GOOGLE_CALENDAR_WRITE_ENABLED"] = "true"
            args = type("Args", (), {"command": "write", "mode": "normal", "note": None, "from_time": None, "to_time": None})()

            result = planner.run_existing_planner(args, date(2026, 6, 29))
        finally:
            planner.delete_auto_events_for_date = original_delete
            planner.subprocess.run = original_run
            if original_gate is None:
                planner.os.environ.pop("GOOGLE_CALENDAR_WRITE_ENABLED", None)
            else:
                planner.os.environ["GOOGLE_CALENDAR_WRITE_ENABLED"] = original_gate

        self.assertEqual(result, 0)
        self.assertEqual(calls, [(date(2026, 6, 29), "NICO_WEEK_PLANNER_AUTO")])

    def test_week_write_skips_day_with_existing_day_auto_events_and_never_deletes_day_events(self) -> None:
        original_tasks = planner.load_tasks_for_source
        original_blocks = planner.fixed_blocks_for_week_day
        original_delete = planner.delete_auto_events_for_date
        original_create = planner.create_calendar_event
        original_gate = planner.os.environ.get("GOOGLE_CALENDAR_WRITE_ENABLED")
        deleted_markers: list[str] = []
        created: list[str] = []

        def fake_fixed(target_day: date):
            return [], [], target_day == date(2026, 6, 29)

        try:
            planner.load_tasks_for_source = lambda _source: (
                [
                    Task("w1", "Whammy Thilo", "Werkstatt", "P1", 90),
                    Task("w2", "Echolette NG51 fertigziehen", "Werkstatt", "P1", 75),
                ],
                "test",
                False,
                [],
                (),
            )
            planner.fixed_blocks_for_week_day = fake_fixed
            planner.delete_auto_events_for_date = lambda _day, _calendar_id, marker="": deleted_markers.append(marker) or 0
            planner.create_calendar_event = lambda _calendar_id, body: created.append(str(body["summary"])) or "id"
            planner.os.environ["GOOGLE_CALENDAR_WRITE_ENABLED"] = "true"
            args = type("Args", (), {"week_from": "2026-06-29", "week_days": 2, "week_command": "write"})()

            result = planner.run_week(args)
        finally:
            planner.load_tasks_for_source = original_tasks
            planner.fixed_blocks_for_week_day = original_blocks
            planner.delete_auto_events_for_date = original_delete
            planner.create_calendar_event = original_create
            if original_gate is None:
                planner.os.environ.pop("GOOGLE_CALENDAR_WRITE_ENABLED", None)
            else:
                planner.os.environ["GOOGLE_CALENDAR_WRITE_ENABLED"] = original_gate

        self.assertEqual(result, 0)
        self.assertTrue(deleted_markers)
        self.assertEqual(set(deleted_markers), {"NICO_WEEK_PLANNER_AUTO"})
        self.assertTrue(created)
        self.assertNotIn("Whammy Thilo [Werkstatt P1]", created)
        self.assertIn("Echolette NG51 fertigziehen [Werkstatt P1]", created)

    def test_week_preview_prefers_concrete_tasks_and_bundles_communication(self) -> None:
        original_tasks = planner.load_tasks_for_source
        original_blocks = planner.fixed_blocks_for_week_day
        try:
            planner.load_tasks_for_source = lambda _source: (
                [
                    Task("w1", "Whammy Thilo", "Werkstatt", "P1", 90),
                    Task("w2", "Echolette NG51 fertigziehen", "Werkstatt", "P1", 75),
                    Task("s1", "Anna Song1 Feedback nachfragen", "Studio", "P1", 15),
                    Task("s2", "Termine für Alisa Klavieraufnahme checken", "Studio", "P1", 15),
                    Task("a1", "Spotify for Artists Pitch / Canvas / Clips vorbereiten", "ALEGRA", "P1", 60),
                    Task("a2", "WDT Live Session fertig", "ALEGRA", "P1", 90),
                ],
                "test",
                False,
                [],
                (),
            )
            planner.fixed_blocks_for_week_day = lambda _day: ([], [], False)

            plan = planner.build_week_plan(date(2026, 6, 29), 4)
        finally:
            planner.load_tasks_for_source = original_tasks
            planner.fixed_blocks_for_week_day = original_blocks

        titles = [block.task.title for blocks in plan["planned"].values() for block in blocks]
        self.assertIn("Whammy Thilo [Werkstatt P1]", titles)
        self.assertIn("Echolette NG51 fertigziehen [Werkstatt P1]", titles)
        self.assertIn("Spotify for Artists Pitch / Canvas / Clips vorbereiten [ALEGRA P1]", titles)
        self.assertIn("WDT Live Session fertig [ALEGRA P1]", titles)
        self.assertTrue(any(title.startswith("Studio-Kommunikation:") for title in titles))
        self.assertFalse(any(title.startswith("Werkstatt-Fokus") for title in titles))


if __name__ == "__main__":
    unittest.main()
