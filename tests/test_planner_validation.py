import argparse
import sys
import unittest
from datetime import date, datetime, timedelta
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
    buffered_planning_blockers,
    find_free_windows,
    lunch_break_block,
    overlaps,
    weekly_blocks,
)
import dry_run_plan  # noqa: E402
from google_calendar_client import _event_to_block  # noqa: E402
import planner  # noqa: E402
from todoist_client import enrich_subtask_titles  # noqa: E402


class PlannerValidationRegressionTest(unittest.TestCase):
    def test_push_mode_uses_expanded_structural_limits(self) -> None:
        normal = dry_run_plan.PlanOptions()
        push = dry_run_plan.PlanOptions(push=True)

        self.assertEqual((normal.max_planned_percent, normal.max_main_tasks, normal.max_mini_tasks, normal.max_partial_blocks), (75, 6, 2, 2))
        self.assertEqual((push.max_planned_percent, push.max_main_tasks, push.max_mini_tasks, push.max_partial_blocks), (90, 8, 3, 4))

    def test_parent_id_and_title_prefix_share_one_project_focus(self) -> None:
        by_id = Task("child-1", "Heizkreis", "Werkstatt", "P1", 30, parent_id="parent-1")
        by_same_id = Task("child-4", "Trafo", "Werkstatt", "P1", 30, parent_id="parent-1")
        by_title = Task("child-2", "Heizkreis", "Werkstatt", "P1", 30, parent_title="Echolette NG51")
        by_prefix = Task("child-3", "Echolette NG51: Dokumentation", "Werkstatt", "P1", 30)

        self.assertEqual(dry_run_plan.task_focus_key(by_id), dry_run_plan.task_focus_key(by_same_id))
        self.assertEqual(dry_run_plan.task_focus_key(by_title), dry_run_plan.task_focus_key(by_prefix))

    def test_parent_project_blocks_count_minutes_but_only_one_focus(self) -> None:
        target = date(2026, 7, 19)
        tasks = [
            Task("child-1", "Echolette NG51: Dokumentation", "Privat", "P1", 30, parent_id="parent-1", parent_title="Echolette NG51"),
            Task("child-2", "Echolette NG51: Heizkreis", "Privat", "P1", 30, parent_id="parent-1", parent_title="Echolette NG51"),
        ]
        original_tasks = dry_run_plan.load_tasks_for_source
        original_calendar = dry_run_plan.load_calendar_blocks_for_source
        try:
            dry_run_plan.load_tasks_for_source = lambda _source: (tasks, "test", False, [], ())
            dry_run_plan.load_calendar_blocks_for_source = lambda _source, _day: ([], "test", False, [], (), [])
            plan = build_plan("todoist", target, "google")
        finally:
            dry_run_plan.load_tasks_for_source = original_tasks
            dry_run_plan.load_calendar_blocks_for_source = original_calendar

        self.assertEqual(plan.planned_minutes, 60)
        self.assertEqual(plan.main_focus_count, 1)
        self.assertIn("2 Blöcke zählen als 1 Hauptfokus", "\n".join(plan.load_diagnostics))

    def test_due_p3_can_precede_undated_p1(self) -> None:
        target = date(2026, 7, 20)
        chosen = dry_run_plan.choose_task(
            [Task("p1", "Wichtig ohne Frist", "Privat", "P1", 30), Task("p3", "Heute fällig", "Privat", "P3", 30, due_date=target)],
            datetime(2026, 7, 20, 19, 30), datetime(2026, 7, 20, 20, 0), 30, [], set(), 0, 0, 0, set(), {}, False, 0, 0,
            dry_run_plan.PlanOptions(push=True),
        )
        self.assertEqual(chosen.id, "p3")

    def test_push_prefers_p1_partial_over_undated_p3(self) -> None:
        chosen = dry_run_plan.choose_task(
            [Task("p3", "Niedriger Füller", "Werkstatt", "P3", 30), Task("p1", "Echolette Fehlersuche", "Werkstatt", "P1", 90)],
            datetime(2026, 7, 20, 10, 0), datetime(2026, 7, 20, 11, 0), 60, dry_run_plan.weekly_blocks(date(2026, 7, 20)),
            set(), 0, 0, 0, set(), {}, True, 0, 0, dry_run_plan.PlanOptions(push=True),
        )
        self.assertEqual(chosen.id, "p1")
        self.assertTrue(dry_run_plan.is_partial_task(chosen))

    def test_p4_mini_task_remains_a_filler_when_high_priority_task_cannot_fit(self) -> None:
        chosen = dry_run_plan.choose_task(
            [Task("p1", "Wichtiger großer Block", "Privat", "P1", 30), Task("p4", "Müll rausbringen", "Haushalt", "P4", 15)],
            datetime(2026, 7, 20, 20, 0), datetime(2026, 7, 20, 20, 15), 15, [], set(), 0, 0, 0, set(), {}, False, 0, 0,
            dry_run_plan.PlanOptions(push=True),
        )
        self.assertEqual(chosen.id, "p4")

    def test_push_capacity_is_a_ceiling_not_a_fill_target(self) -> None:
        target = date(2026, 7, 19)
        original_tasks = dry_run_plan.load_tasks_for_source
        original_calendar = dry_run_plan.load_calendar_blocks_for_source
        try:
            dry_run_plan.load_tasks_for_source = lambda _source: ([Task("only", "Einzige Aufgabe", "Privat", "P1", 30)], "test", False, [], ())
            dry_run_plan.load_calendar_blocks_for_source = lambda _source, _day: ([], "test", False, [], (), [])
            plan = build_plan("todoist", target, "google", options=dry_run_plan.PlanOptions(push=True))
        finally:
            dry_run_plan.load_tasks_for_source = original_tasks
            dry_run_plan.load_calendar_blocks_for_source = original_calendar

        self.assertEqual(plan.planned_minutes, 30)
        self.assertGreater(plan.capacity_minutes, plan.planned_minutes)

    def test_push_preview_and_quality_explain_unused_actionable_capacity(self) -> None:
        target = date(2026, 7, 20)
        open_task = Task("open", "Passende wichtige Aufgabe", "Privat", "P1", 30)
        plan = PlanResult(
            target_day=target, source_status="test", fixed_blocks=[],
            free_windows=[TimeWindow(datetime(2026, 7, 20, 19, 30), datetime(2026, 7, 20, 21, 30))],
            planned_blocks=[], not_scheduled=[RejectedTask(open_task, "offen")], split_suggestions=[],
            capacity_minutes=108, planned_minutes=0, source="test", calendar_source="json", calendar_status="test",
            plan_options=dry_run_plan.PlanOptions(push=True), main_focus_count=0, mini_task_count=0, partial_block_count=0,
        )

        rendered = dry_run_plan.render_plan(plan)
        _score, reasons = dry_run_plan.plan_quality_details(plan)
        self.assertIn("Hauptaufgabenlimit: 8", rendered)
        self.assertIn("Mini-Task-Limit: 3", rendered)
        self.assertIn("Teilblocklimit: 4", rendered)
        self.assertIn("Aktive Aufgabenzeit: 0/108 Min.", rendered)
        self.assertIn("Push-Kapazität nicht ausgeschöpft", "\n".join(reasons))

    def test_review_target_date_supports_relative_days_and_iso_dates(self) -> None:
        today = date.today()

        self.assertEqual(planner.target_date_for("yesterday"), today - timedelta(days=1))
        self.assertEqual(planner.target_date_for("today"), today)
        self.assertEqual(planner.target_date_for("tomorrow"), today + timedelta(days=1))
        self.assertEqual(planner.target_date_for("2026-06-29"), date(2026, 6, 29))

    def test_review_target_date_rejects_invalid_formats_with_help(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "Erlaubte Formate: yesterday, today, tomorrow oder YYYY-MM-DD",
        ):
            planner.target_date_for("29.06.2026")

    def test_quick_day_cli_feedback_defaults_missing_day_values(self) -> None:
        args = argparse.Namespace(day_energy="low", day_overall=None, evening="ok", day_note="Testnotiz")

        feedback = planner.cli_day_feedback(args)

        self.assertEqual(
            feedback,
            {
                "energy_level": "low",
                "overall_plan": "good",
                "evening": "ok",
                "note": "Testnotiz",
            },
        )

    def test_quick_day_neutral_event_feedback_does_not_fake_event_status(self) -> None:
        event = {
            "id": "auto-1",
            "title": "[Werkstatt] Whammy Thilo",
            "start": "2026-06-29T09:00:00",
            "end": "2026-06-29T10:00:00",
        }

        feedback = planner.neutral_event_feedback(event)

        self.assertEqual(feedback["title"], "Whammy Thilo")
        self.assertEqual(feedback["category"], "Werkstatt")
        self.assertEqual(feedback["duration_minutes"], 60)
        self.assertEqual(
            feedback["feedback"],
            {"status": "unknown", "duration": "unknown", "timing": "unknown", "note": ""},
        )

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
        buchhaltung_body = _calendar_event_body(
            PlannedBlock(Task("todoist-buchhaltung", "Rechnungen prüfen", "Buchhaltung", "P1", 60), start, end)
        )
        haushalt_body = _calendar_event_body(
            PlannedBlock(Task("todoist-haushalt", "Küche aufräumen", "Haushalt", "P3", 30), start, end)
        )

        self.assertEqual(werkstatt_body["colorId"], "10")
        self.assertEqual(studio_body["colorId"], "2")
        self.assertEqual(buchhaltung_body["colorId"], "7")
        self.assertEqual(haushalt_body["colorId"], "9")

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

    def test_today_start_time_prevents_planning_before_noon(self) -> None:
        original_load_tasks = dry_run_plan.load_tasks_for_source
        original_load_calendar = dry_run_plan.load_calendar_blocks_for_source
        try:
            dry_run_plan.load_tasks_for_source = lambda _source: (
                [Task("privat-1", "Mittagsplanung", "Privat", "P1", 30)],
                "test",
                False,
                [],
                (),
            )
            dry_run_plan.load_calendar_blocks_for_source = lambda _calendar_source, _target_day: ([], "test", False, [], (), [])

            plan = build_plan("todoist", date(2026, 7, 18), "google", planning_start=datetime(2026, 7, 18, 12, 0))
        finally:
            dry_run_plan.load_tasks_for_source = original_load_tasks
            dry_run_plan.load_calendar_blocks_for_source = original_load_calendar

        self.assertTrue(plan.planned_blocks)
        self.assertTrue(all(block.start >= datetime(2026, 7, 18, 12, 0) for block in plan.planned_blocks))
        self.assertTrue(all(window.start >= datetime(2026, 7, 18, 12, 0) for window in plan.free_windows))
        self.assertTrue(any("Planung für heute ab 12:00" in warning for warning in plan.warnings))

    def test_from_now_uses_rounded_now_slot_for_today(self) -> None:
        original_rounded = planner.rounded_now_slot
        try:
            planner.rounded_now_slot = lambda: datetime.combine(date.today(), datetime.strptime("12:07", "%H:%M").time())
            args = argparse.Namespace(command="preview", start_time=None, from_now=True)
            start = planner.planning_start_for(args, date.today())
        finally:
            planner.rounded_now_slot = original_rounded

        self.assertIsNotNone(start)
        assert start is not None
        self.assertEqual(start.minute, 7)
        # rounded_now_slot is responsible for rounding; planner uses it as minimum start.

    def test_restday_quality_treats_workshop_backlog_as_context(self) -> None:
        target_day = date(2026, 7, 18)  # Saturday, no Werkstatt window.
        planned = Task("studio-1", "Studio Resttag", "Studio", "P1", 60)
        workshop = Task("werkstatt-1", "Echolette NG51", "Werkstatt", "P1", 90)
        plan = PlanResult(
            target_day=target_day,
            source_status="test",
            fixed_blocks=[],
            free_windows=[TimeWindow(datetime(2026, 7, 18, 14, 0), datetime(2026, 7, 18, 23, 0))],
            planned_blocks=[PlannedBlock(planned, datetime(2026, 7, 18, 14, 0), datetime(2026, 7, 18, 15, 0))],
            not_scheduled=[RejectedTask(workshop, "kein ausreichend langes Werkstattfenster mehr frei.")],
            split_suggestions=[],
            capacity_minutes=378,
            planned_minutes=60,
            source="todoist",
            calendar_source="google",
            calendar_status="test",
            warnings=["Planung für heute ab 14:00. Frühere Zeitfenster werden nicht mehr beplant."],
            planning_start=datetime(2026, 7, 18, 14, 0),
            plan_options=dry_run_plan.PlanOptions(),
            open_task_context=("Offen, aber heute nicht passend: 1 P1/P2-Aufgabe(n) – kein passendes Werkstattfenster im betrachteten Planungszeitraum.",),
        )

        quality, reasons = dry_run_plan.plan_quality_details(plan)
        rendered = dry_run_plan.render_plan(plan)

        self.assertGreaterEqual(quality, 8)
        self.assertIn("Werkstattaufgaben offen, aber kein passendes Werkstattfenster", "\n".join(reasons))
        self.assertIn("Offen, aber heute nicht passend", rendered)
        self.assertIn("kein passendes Werkstattfenster im betrachteten Planungszeitraum", rendered)

    def test_until_limits_free_windows_and_planned_blocks(self) -> None:
        original_load_tasks = dry_run_plan.load_tasks_for_source
        original_load_calendar = dry_run_plan.load_calendar_blocks_for_source
        try:
            dry_run_plan.load_tasks_for_source = lambda _source: (
                [Task("studio-1", "Abendblock", "Studio", "P1", 120)],
                "test",
                False,
                [],
                (),
            )
            dry_run_plan.load_calendar_blocks_for_source = lambda _calendar_source, _target_day: ([], "test", False, [], (), [])
            plan = build_plan(
                "todoist",
                date(2026, 7, 18),
                "google",
                planning_start=datetime(2026, 7, 18, 14, 0),
                options=dry_run_plan.PlanOptions(day_end=planner.parse_cli_hhmm("23:00")),
            )
        finally:
            dry_run_plan.load_tasks_for_source = original_load_tasks
            dry_run_plan.load_calendar_blocks_for_source = original_load_calendar

        self.assertTrue(plan.planned_blocks)
        self.assertTrue(all(block.end <= datetime(2026, 7, 18, 23, 0) for block in plan.planned_blocks))
        self.assertTrue(all(window.end <= datetime(2026, 7, 18, 23, 0) for window in plan.free_windows))

    def test_allow_late_permits_additional_evening_block(self) -> None:
        tasks = [
            Task("studio-1", "Erster Studioabend", "Studio", "P1", 90),
            Task("admin-1", "Rechnung hochladen", "Buchhaltung", "P2", 60),
        ]
        fixed = []
        normal = dry_run_plan.choose_task(
            tasks[1:],
            datetime(2026, 7, 18, 20, 45),
            datetime(2026, 7, 18, 23, 0),
            120,
            fixed,
            1,
            0,
            1,
            0,
            set(),
            {},
            False,
            0,
            0,
            dry_run_plan.PlanOptions(),
        )
        late = dry_run_plan.choose_task(
            tasks[1:],
            datetime(2026, 7, 18, 21, 15),
            datetime(2026, 7, 18, 23, 0),
            120,
            fixed,
            1,
            0,
            1,
            0,
            set(),
            {},
            False,
            0,
            0,
            dry_run_plan.PlanOptions(allow_late=True, admin_until=planner.parse_cli_hhmm("23:00")),
        )

        self.assertIsNone(normal)
        self.assertIsNotNone(late)
        assert late is not None
        self.assertEqual(late.id, "admin-1")


    def test_normal_day_planning_uses_75_percent_capacity(self) -> None:
        original_load_tasks = dry_run_plan.load_tasks_for_source
        original_load_calendar = dry_run_plan.load_calendar_blocks_for_source
        try:
            dry_run_plan.load_tasks_for_source = lambda _source: (
                [Task("t1", "Normalblock", "Privat", "P1", 30)],
                "test",
                False,
                [],
                (),
            )
            dry_run_plan.load_calendar_blocks_for_source = lambda _calendar_source, _target_day: ([], "test", False, [], (), [])
            plan = build_plan(
                "todoist",
                date(2026, 7, 18),
                "google",
                planning_start=datetime(2026, 7, 18, 16, 0),
                options=dry_run_plan.PlanOptions(day_end=planner.parse_cli_hhmm("23:00")),
            )
        finally:
            dry_run_plan.load_tasks_for_source = original_load_tasks
            dry_run_plan.load_calendar_blocks_for_source = original_load_calendar

        self.assertEqual(plan.capacity_minutes, 315)
        self.assertIn("Auslastungslimit: 75%", dry_run_plan.render_plan(plan))

    def test_push_day_planning_uses_90_percent_capacity(self) -> None:
        original_load_tasks = dry_run_plan.load_tasks_for_source
        original_load_calendar = dry_run_plan.load_calendar_blocks_for_source
        try:
            dry_run_plan.load_tasks_for_source = lambda _source: (
                [Task("t1", "Pushblock", "Privat", "P1", 30)],
                "test",
                False,
                [],
                (),
            )
            dry_run_plan.load_calendar_blocks_for_source = lambda _calendar_source, _target_day: ([], "test", False, [], (), [])
            plan = build_plan(
                "todoist",
                date(2026, 7, 18),
                "google",
                planning_start=datetime(2026, 7, 18, 16, 0),
                options=dry_run_plan.PlanOptions(day_end=planner.parse_cli_hhmm("23:00"), push=True),
            )
        finally:
            dry_run_plan.load_tasks_for_source = original_load_tasks
            dry_run_plan.load_calendar_blocks_for_source = original_load_calendar

        rendered = dry_run_plan.render_plan(plan)
        self.assertEqual(plan.capacity_minutes, 378)
        self.assertIn("Push-Modus aktiv: erhöhte Tageslast erlaubt.", rendered)
        self.assertIn("Auslastungslimit: 90%", rendered)
        self.assertIn("Planung bis 23:00 erlaubt.", rendered)

    def test_push_until_23_allows_late_tasks_without_passing_until(self) -> None:
        original_load_tasks = dry_run_plan.load_tasks_for_source
        original_load_calendar = dry_run_plan.load_calendar_blocks_for_source
        try:
            dry_run_plan.load_tasks_for_source = lambda _source: (
                [
                    Task("a", "Erster Abendblock", "Privat", "P1", 120),
                    Task("b", "Späte Buchhaltung", "Buchhaltung", "P2", 60),
                    Task("c", "Späte Kleinigkeit", "Haushalt", "P4", 15),
                ],
                "test",
                False,
                [],
                (),
            )
            dry_run_plan.load_calendar_blocks_for_source = lambda _calendar_source, _target_day: ([], "test", False, [], (), [])
            plan = build_plan(
                "todoist",
                date(2026, 7, 18),
                "google",
                planning_start=datetime(2026, 7, 18, 19, 30),
                options=dry_run_plan.PlanOptions(day_end=planner.parse_cli_hhmm("23:00"), push=True),
            )
        finally:
            dry_run_plan.load_tasks_for_source = original_load_tasks
            dry_run_plan.load_calendar_blocks_for_source = original_load_calendar

        titles = [block.task.title for block in plan.planned_blocks]
        self.assertIn("Späte Buchhaltung", titles)
        self.assertTrue(all(block.end <= datetime(2026, 7, 18, 23, 0) for block in plan.planned_blocks))

    def test_allow_late_alias_enables_push_and_deprecation_message(self) -> None:
        options = dry_run_plan.PlanOptions(day_end=planner.parse_cli_hhmm("23:00"), allow_late=True)
        self.assertTrue(options.push_mode)
        self.assertEqual(options.max_planned_percent, 90)
        parser = planner.build_parser()
        args = parser.parse_args(["preview", "today", "--until", "23:00", "--push", "--allow-late"])
        planner.validate_command_day_combination(parser, args)
        self.assertTrue(args.push)
        self.assertTrue(args.allow_late)


    def test_quality_uses_push_capacity_without_density_penalty(self) -> None:
        target = date(2026, 7, 18)
        plan = PlanResult(
            target_day=target,
            source_status="test",
            fixed_blocks=[],
            free_windows=[TimeWindow(datetime(2026, 7, 18, 16, 0), datetime(2026, 7, 18, 23, 0))],
            planned_blocks=[PlannedBlock(Task("p", "Dichter Pushblock", "Privat", "P1", 360), datetime(2026, 7, 18, 16, 0), datetime(2026, 7, 18, 22, 0))],
            not_scheduled=[],
            split_suggestions=[],
            capacity_minutes=378,
            planned_minutes=360,
            source="todoist",
            calendar_source="google",
            calendar_status="test",
            warnings=["Push-Modus aktiv: erhöhte Tageslast erlaubt."],
            plan_options=dry_run_plan.PlanOptions(day_end=planner.parse_cli_hhmm("23:00"), push=True),
        )

        quality, reasons = dry_run_plan.plan_quality_details(plan)

        self.assertGreaterEqual(quality, 8)
        self.assertIn("Push-Modus aktiv: erhöhte Tageslast erlaubt.", reasons)
        self.assertNotIn("Geplante Zeit überschreitet Kapazitätslimit", reasons)

    def test_cli_rejects_invalid_until_and_admin_until(self) -> None:
        parser = planner.build_parser()
        with self.assertRaises(SystemExit):
            args = parser.parse_args(["preview", "today", "--until", "25:00"])
            planner.validate_command_day_combination(parser, args)
        with self.assertRaises(SystemExit):
            args = parser.parse_args(["preview", "today", "--allow-admin-until", "99:99"])
            planner.validate_command_day_combination(parser, args)

    def test_weekly_soundwerk_blocks_no_longer_block_tuesday_afternoon(self) -> None:
        target_day = date(2026, 6, 30)  # Tuesday
        weekly = weekly_blocks(target_day)

        self.assertFalse(any("Soundwerk Unterricht" in block.title for block in weekly))
        free = find_free_windows(target_day, buffered_planning_blockers(weekly + [lunch_break_block(target_day)]))
        self.assertTrue(any(window.start <= datetime(2026, 6, 30, 14, 0) and window.end >= datetime(2026, 6, 30, 18, 0) for window in free))

    def test_google_lesson_blocks_remain_hard_blockers_and_gaps_are_used(self) -> None:
        original_load_tasks = dry_run_plan.load_tasks_for_source
        original_load_calendar = dry_run_plan.load_calendar_blocks_for_source
        try:
            dry_run_plan.load_tasks_for_source = lambda _source: (
                [
                    Task("privat-1", "Private Ablage", "Privat", "P1", 30),
                    Task("werkstatt-1", "Große Diagnose", "Werkstatt", "P1", 60),
                ],
                "test",
                False,
                [],
                (),
            )
            dry_run_plan.load_calendar_blocks_for_source = lambda _calendar_source, _target_day: (
                [
                    Block("lesson-1", "Soundwerk Unterricht Noah", datetime(2026, 6, 30, 14, 0), datetime(2026, 6, 30, 14, 30), "Google Calendar"),
                    Block("lesson-2", "Soundwerk Unterricht Mia", datetime(2026, 6, 30, 15, 15), datetime(2026, 6, 30, 15, 45), "Google Calendar"),
                ],
                "test",
                False,
                [],
                (),
                [],
            )

            plan = build_plan("todoist", date(2026, 6, 30), "google")
        finally:
            dry_run_plan.load_tasks_for_source = original_load_tasks
            dry_run_plan.load_calendar_blocks_for_source = original_load_calendar

        self.assertTrue(any(block.task.title == "Private Ablage" and block.start == datetime(2026, 6, 30, 14, 30) for block in plan.planned_blocks))
        self.assertFalse(any(overlaps(block.start, block.end, datetime(2026, 6, 30, 14, 0), datetime(2026, 6, 30, 14, 30)) for block in plan.planned_blocks))
        self.assertFalse(any(overlaps(block.start, block.end, datetime(2026, 6, 30, 15, 15), datetime(2026, 6, 30, 15, 45)) for block in plan.planned_blocks))

    def test_day_write_start_time_deletes_only_future_day_auto_events(self) -> None:
        calls: list[str] = []
        original_delete = dry_run_plan.delete_auto_events_for_date
        original_create = dry_run_plan.create_calendar_event
        original_gate = dry_run_plan.os.environ.get("GOOGLE_CALENDAR_WRITE_ENABLED")

        plan = PlanResult(
            target_day=date(2026, 7, 18),
            source_status="test",
            fixed_blocks=[],
            free_windows=[],
            planned_blocks=[PlannedBlock(Task("new", "Neue Aufgabe", "Privat", "P1", 30), datetime(2026, 7, 18, 12, 0), datetime(2026, 7, 18, 12, 30))],
            not_scheduled=[],
            split_suggestions=[],
            capacity_minutes=30,
            planned_minutes=30,
            source="todoist",
            calendar_source="google",
            calendar_status="test",
            planning_start=datetime(2026, 7, 18, 12, 0),
        )

        def fake_delete(_day: date, _calendar_id: str, marker: str = dry_run_plan.AUTO_EVENT_MARKER, not_before: datetime | None = None, not_after: datetime | None = None) -> int:
            calls.append(f"delete:{marker}:{not_before:%H:%M}" if not_before else f"delete:{marker}:none")
            return 1

        def fake_create(_calendar_id: str, _body: dict[str, object]) -> str:
            calls.append("create")
            return "created"

        try:
            dry_run_plan.delete_auto_events_for_date = fake_delete
            dry_run_plan.create_calendar_event = fake_create
            dry_run_plan.os.environ["GOOGLE_CALENDAR_WRITE_ENABLED"] = "true"
            apply_calendar_write(plan, write_calendar=True, replace_auto_events=True)
        finally:
            dry_run_plan.delete_auto_events_for_date = original_delete
            dry_run_plan.create_calendar_event = original_create
            if original_gate is None:
                dry_run_plan.os.environ.pop("GOOGLE_CALENDAR_WRITE_ENABLED", None)
            else:
                dry_run_plan.os.environ["GOOGLE_CALENDAR_WRITE_ENABLED"] = original_gate

        self.assertEqual(calls, ["delete:NICO_DAY_PLANNER_AUTO:12:00", "create"])


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

        def fake_delete(target_day: date, _calendar_id: str, marker: str = "", not_before: datetime | None = None, not_after: datetime | None = None) -> int:
            calls.append((target_day, marker))
            return 2

        class Completed:
            returncode = 0

        try:
            planner.delete_auto_events_for_date = fake_delete
            planner.subprocess.run = lambda *_args, **_kwargs: Completed()
            planner.os.environ["GOOGLE_CALENDAR_WRITE_ENABLED"] = "true"
            args = type("Args", (), {"command": "write", "mode": "normal", "note": None, "from_time": None, "to_time": None, "until": None, "allow_late": False, "allow_admin_until": None})()

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


    def test_week_plan_excludes_day_auto_task_by_todoist_id(self) -> None:
        original_tasks = planner.load_tasks_for_source
        original_blocks = planner.fixed_blocks_for_week_day
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
            planner.fixed_blocks_for_week_day = lambda day: ([], [], day == date(2026, 6, 29), {"w1"} if day == date(2026, 6, 29) else set(), set())

            plan = planner.build_week_plan(date(2026, 6, 29), 3)
        finally:
            planner.load_tasks_for_source = original_tasks
            planner.fixed_blocks_for_week_day = original_blocks

        titles = [block.task.title for blocks in plan["planned"].values() for block in blocks]
        self.assertFalse(any("Whammy Thilo" in title for title in titles))
        self.assertFalse(any(task.id == "w1" for task in plan["open_high"]))
        self.assertIn("1 Aufgabe(n) bereits durch Tagesplanung abgedeckt.", plan["warnings"])

    def test_week_plan_excludes_day_auto_task_by_normalized_title(self) -> None:
        original_tasks = planner.load_tasks_for_source
        original_blocks = planner.fixed_blocks_for_week_day
        try:
            planner.load_tasks_for_source = lambda _source: (
                [
                    Task("a1", "Wenn du tanzt Release finalisieren", "ALEGRA", "P1", 90),
                    Task("a2", "Tim Finanzen", "ALEGRA", "P2", 30),
                ],
                "test",
                False,
                [],
                (),
            )
            covered_title = planner.normalize_week_task_title("Wenn du tanzt Release finalisieren – Teil 1 [ALEGRA P1]")
            planner.fixed_blocks_for_week_day = lambda day: ([], [], day == date(2026, 6, 29), set(), {covered_title} if day == date(2026, 6, 29) else set())

            plan = planner.build_week_plan(date(2026, 6, 29), 4)
        finally:
            planner.load_tasks_for_source = original_tasks
            planner.fixed_blocks_for_week_day = original_blocks

        titles = [block.task.title for blocks in plan["planned"].values() for block in blocks]
        self.assertFalse(any("Wenn du tanzt" in title for title in titles))
        self.assertTrue(any("Tim Finanzen" in title for title in titles))
        self.assertFalse(any(task.id == "a1" for task in plan["open_high"]))

    def test_week_plan_communication_bundle_omits_day_auto_tasks(self) -> None:
        original_tasks = planner.load_tasks_for_source
        original_blocks = planner.fixed_blocks_for_week_day
        try:
            planner.load_tasks_for_source = lambda _source: (
                [
                    Task("a1", "Tim Finanzen", "ALEGRA", "P2", 30),
                    Task("s1", "Anna Song1 Feedback nachfragen", "Studio", "P1", 15),
                    Task("s2", "Termine für Alisa Klavieraufnahme checken", "Studio", "P1", 15),
                ],
                "test",
                False,
                [],
                (),
            )
            planner.fixed_blocks_for_week_day = lambda day: ([], [], day == date(2026, 6, 29), {"a1"} if day == date(2026, 6, 29) else set(), set())

            plan = planner.build_week_plan(date(2026, 6, 29), 5)
        finally:
            planner.load_tasks_for_source = original_tasks
            planner.fixed_blocks_for_week_day = original_blocks

        titles = [block.task.title for blocks in plan["planned"].values() for block in blocks]
        communication_titles = [title for title in titles if "Kommunikation" in title]
        self.assertTrue(communication_titles)
        self.assertFalse(any("Tim Finanzen" in title for title in communication_titles))

    def test_week_plan_uses_multiple_workshop_and_alegra_blocks(self) -> None:
        original_tasks = planner.load_tasks_for_source
        original_blocks = planner.fixed_blocks_for_week_day
        try:
            planner.load_tasks_for_source = lambda _source: (
                [
                    Task("w1", "Echolette NG51 fertigziehen", "Werkstatt", "P1", 90),
                    Task("w2", "SPL Transient Designer", "Werkstatt", "P2", 90),
                    Task("w3", "HK Audio Lucas Impact", "Werkstatt", "P2", 120),
                    Task("a1", "Spotify for Artists Pitch / Canvas / Clips vorbereiten", "ALEGRA", "P1", 60),
                    Task("a2", "WDT Live Session fertig", "ALEGRA", "P1", 75),
                    Task("a3", "Wenn du tanzt Release finalisieren", "ALEGRA", "P1", 90),
                ],
                "test",
                False,
                [],
                (),
            )
            planner.fixed_blocks_for_week_day = lambda _day: ([], [], False, set(), set())

            plan = planner.build_week_plan(date(2026, 7, 1), 2)
        finally:
            planner.load_tasks_for_source = original_tasks
            planner.fixed_blocks_for_week_day = original_blocks

        wednesday = plan["planned"][date(2026, 7, 1)]
        thursday = plan["planned"][date(2026, 7, 2)]
        self.assertGreaterEqual(sum(1 for block in wednesday if block.task.category == "Werkstatt"), 2)
        self.assertGreaterEqual(sum(1 for block in thursday if block.task.category == "ALEGRA"), 3)
        self.assertTrue(any("Wenn du tanzt" in block.task.title for block in thursday))

    def test_day_auto_covered_keys_extracts_id_and_normalized_title(self) -> None:
        event = Block(
            "day-1",
            "Whammy Thilo – Teil 1 [Werkstatt P1]",
            datetime(2026, 6, 29, 9, 0),
            datetime(2026, 6, 29, 10, 0),
            "Google Calendar",
            description="NICO_DAY_PLANNER_AUTO\nTodoist Task ID: w1",
        )
        ids, titles = planner.day_auto_covered_keys([event])
        self.assertEqual(ids, {"w1"})
        self.assertIn("whammy thilo", titles)

    def test_todoist_subtask_title_gets_parent_prefix(self) -> None:
        tasks = enrich_subtask_titles(
            [
                {"id": "parent-1", "title": "Echolette NG51", "category": "Werkstatt", "priority": "P1", "duration_minutes": 120},
                {
                    "id": "child-1",
                    "parent_id": "parent-1",
                    "title": "Heizkreis und Spannungsversorgung prüfen",
                    "category": "Werkstatt",
                    "priority": "P1",
                    "duration_minutes": 60,
                },
            ]
        )
        child = dry_run_plan.normalize_task(tasks[1])

        self.assertEqual(child.title, "Echolette NG51: Heizkreis und Spannungsversorgung prüfen")
        self.assertEqual(child.parent_id, "parent-1")
        self.assertEqual(child.parent_title, "Echolette NG51")

    def test_todoist_subtask_title_does_not_double_parent_prefix(self) -> None:
        tasks = enrich_subtask_titles(
            [
                {"id": "parent-1", "title": "Echolette NG51", "category": "Werkstatt", "priority": "P1", "duration_minutes": 120},
                {
                    "id": "child-1",
                    "parent_id": "parent-1",
                    "title": "Echolette NG51: Heizkreis prüfen",
                    "category": "Werkstatt",
                    "priority": "P1",
                    "duration_minutes": 60,
                },
            ]
        )
        child = dry_run_plan.normalize_task(tasks[1])

        self.assertEqual(child.title, "Echolette NG51: Heizkreis prüfen")

    def test_day_title_dedupe_matches_parent_prefixed_auto_event(self) -> None:
        event = Block(
            "day-1",
            "[Werkstatt] Echolette NG51: Heizkreis und Spannungsversorgung prüfen",
            datetime(2026, 6, 29, 9, 0),
            datetime(2026, 6, 29, 10, 0),
            "Google Calendar",
            description="NICO_DAY_PLANNER_AUTO",
        )
        _ids, titles = planner.day_auto_covered_keys([event])
        task = Task(
            id="child-1",
            title="Heizkreis und Spannungsversorgung prüfen",
            category="Werkstatt",
            priority="P1",
            duration_minutes=60,
        )

        self.assertTrue(planner.task_is_day_covered(task, set(), titles))

    def test_day_plan_excludes_task_with_exact_manual_calendar_title(self) -> None:
        original_tasks = dry_run_plan.load_tasks_for_source
        original_calendar = dry_run_plan.load_calendar_blocks_for_source
        target = date(2026, 6, 29)
        manual = Block("manual-1", "Versicherung Ratenzahlung anfragen", datetime(2026, 6, 29, 10), datetime(2026, 6, 29, 11), "Google Calendar")
        try:
            dry_run_plan.load_tasks_for_source = lambda _source: (
                [Task("t1", "Versicherung Ratenzahlung anfragen", "Buchhaltung", "P1", 60)],
                "test",
                False,
                [],
                (),
            )
            dry_run_plan.load_calendar_blocks_for_source = lambda _calendar_source, _target_day: (
                [manual],
                "calendar",
                False,
                [],
                (),
                [],
            )

            plan = dry_run_plan.build_plan("todoist", target, "google")
        finally:
            dry_run_plan.load_tasks_for_source = original_tasks
            dry_run_plan.load_calendar_blocks_for_source = original_calendar

        self.assertEqual([task.title for task in plan.manually_covered_tasks], ["Versicherung Ratenzahlung anfragen"])
        self.assertFalse(plan.planned_blocks)
        self.assertFalse(plan.not_scheduled)
        self.assertFalse(dry_run_plan.important_open_tasks(plan))
        self.assertIn("1 Aufgabe(n) durch manuelle Kalendertermine abgedeckt", "\n".join(plan.calendar_details))

    def test_manual_parent_prefixed_calendar_title_covers_subtask(self) -> None:
        titles = {dry_run_plan.normalize_calendar_coverage_title("Echolette NG51: Heizkreis prüfen")}
        task = Task("child-1", "Heizkreis prüfen", "Werkstatt", "P1", 30, parent_id="parent-1", parent_title="Echolette NG51")

        self.assertTrue(dry_run_plan.task_is_manually_covered_by_titles(task, titles))

    def test_generic_manual_calendar_title_does_not_cover_category_tasks(self) -> None:
        titles = {dry_run_plan.normalize_calendar_coverage_title("Werkstatt")}
        task = Task("w1", "Echolette NG51", "Werkstatt", "P1", 60)

        self.assertFalse(dry_run_plan.task_is_manually_covered_by_titles(task, titles))

    def test_week_manual_covered_tasks_omitted_from_bundle_and_open_high(self) -> None:
        original_tasks = planner.load_tasks_for_source
        original_blocks = planner.fixed_blocks_for_week_day
        try:
            planner.load_tasks_for_source = lambda _source: (
                [
                    Task("a1", "Tim Finanzen", "ALEGRA", "P2", 30),
                    Task("s1", "Anna Song1 Feedback nachfragen", "Studio", "P1", 15),
                    Task("s2", "Termine für Alisa Klavieraufnahme checken", "Studio", "P1", 15),
                ],
                "test",
                False,
                [],
                (),
            )
            covered_title = planner.normalize_week_task_title("Tim Finanzen")
            planner.fixed_blocks_for_week_day = lambda _day: ([], [], False, set(), {covered_title})

            plan = planner.build_week_plan(date(2026, 6, 29), 5)
        finally:
            planner.load_tasks_for_source = original_tasks
            planner.fixed_blocks_for_week_day = original_blocks

        titles = [block.task.title for blocks in plan["planned"].values() for block in blocks]
        self.assertFalse(any("Tim Finanzen" in title for title in titles))
        self.assertFalse(any(task.title == "Tim Finanzen" for task in plan["open_high"]))
        self.assertTrue(any("manuelle Kalendertermine abgedeckt" in warning for warning in plan["warnings"]))

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
