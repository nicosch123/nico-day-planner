#!/usr/bin/env python3
"""Read-only Google Calendar client for Nico Day Planner.

This module only reads events from Google Calendar. It never creates, updates,
deletes, moves, or otherwise mutates calendar data.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

CREDENTIALS_ENV_VAR = "GOOGLE_CALENDAR_CREDENTIALS_JSON"
CALENDAR_ID_ENV_VAR = "GOOGLE_CALENDAR_ID"
DEFAULT_CALENDAR_ID = "primary"
READ_ONLY_SCOPES = ("https://www.googleapis.com/auth/calendar.readonly",)


@dataclass(frozen=True)
class GoogleCalendarReadResult:
    """Result of a read-only Google Calendar fetch."""

    events: list[dict[str, Any]]
    status: str
    used_fallback: bool = False
    status_details: tuple[str, ...] = field(default_factory=tuple)


class GoogleCalendarReadError(RuntimeError):
    """Raised when Google Calendar read-only loading cannot continue."""


def _load_credentials_payload(raw_value: str) -> dict[str, Any]:
    """Load credentials from an env var containing JSON or a path to JSON."""
    value = raw_value.strip()
    if not value:
        raise GoogleCalendarReadError(f"{CREDENTIALS_ENV_VAR} ist leer.")

    if value.startswith("{"):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise GoogleCalendarReadError(f"{CREDENTIALS_ENV_VAR} enthält ungültiges JSON.") from exc
    else:
        credentials_path = Path(value).expanduser()
        try:
            with credentials_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except OSError as exc:
            raise GoogleCalendarReadError(
                f"Google-Credentials-Datei konnte nicht gelesen werden: {credentials_path}."
            ) from exc
        except json.JSONDecodeError as exc:
            raise GoogleCalendarReadError(
                f"Google-Credentials-Datei enthält ungültiges JSON: {credentials_path}."
            ) from exc

    if not isinstance(payload, dict):
        raise GoogleCalendarReadError("Google-Credentials müssen ein JSON-Objekt sein.")
    return payload


def _build_read_only_service(credentials_payload: dict[str, Any]) -> Any:
    """Build a Google Calendar API service with read-only scope only."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError as exc:
        raise GoogleCalendarReadError(
            "Google Calendar Python-Abhängigkeiten fehlen "
            "(google-api-python-client und google-auth)."
        ) from exc

    try:
        credentials = service_account.Credentials.from_service_account_info(
            credentials_payload,
            scopes=READ_ONLY_SCOPES,
        )
        return build("calendar", "v3", credentials=credentials, cache_discovery=False), HttpError
    except Exception as exc:  # noqa: BLE001 - surfaced as user-facing read error.
        raise GoogleCalendarReadError("Google Calendar read-only Service konnte nicht erstellt werden.") from exc


def _parse_google_datetime(value: dict[str, str], fallback_date: date, fallback_time: time) -> datetime:
    """Parse a Google Calendar date/dateTime field into a naive local datetime."""
    date_time_value = value.get("dateTime")
    if date_time_value:
        normalized = date_time_value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed

    date_value = value.get("date")
    if date_value:
        return datetime.combine(date.fromisoformat(date_value), fallback_time)

    return datetime.combine(fallback_date, fallback_time)


def _event_to_block(event: dict[str, Any], target_date: date) -> dict[str, Any] | None:
    """Map one Google Calendar event to the planner's neutral block schema."""
    if event.get("status") == "cancelled":
        return None

    start_raw = event.get("start")
    end_raw = event.get("end")
    if not isinstance(start_raw, dict) or not isinstance(end_raw, dict):
        return None

    start = _parse_google_datetime(start_raw, target_date, time(0, 0))
    end = _parse_google_datetime(end_raw, target_date, time(23, 59))
    if end <= start:
        return None

    return {
        "id": str(event.get("id", "google-calendar-unknown")),
        "title": str(event.get("summary") or "Ohne Titel"),
        "start": start.isoformat(timespec="seconds"),
        "end": end.isoformat(timespec="seconds"),
        "location": str(event.get("location") or ""),
        "source": "Google Calendar",
    }


def load_calendar_events_for_date(target_date: date) -> GoogleCalendarReadResult:
    """Load Google Calendar events for one day using read-only API access."""
    raw_credentials = os.environ.get(CREDENTIALS_ENV_VAR)
    if not raw_credentials:
        return GoogleCalendarReadResult(
            events=[],
            status=f"{CREDENTIALS_ENV_VAR} fehlt – verwende lokale JSON-Kalenderdaten.",
            used_fallback=True,
        )

    credentials_payload = _load_credentials_payload(raw_credentials)
    service, http_error_type = _build_read_only_service(credentials_payload)
    calendar_id = os.environ.get(CALENDAR_ID_ENV_VAR, DEFAULT_CALENDAR_ID)
    day_start = datetime.combine(target_date, time.min).replace(tzinfo=timezone.utc)
    day_end = datetime.combine(target_date, time.max).replace(tzinfo=timezone.utc)

    try:
        response = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=day_start.isoformat().replace("+00:00", "Z"),
                timeMax=day_end.isoformat().replace("+00:00", "Z"),
                singleEvents=True,
                orderBy="startTime",
                showDeleted=False,
            )
            .execute()
        )
    except http_error_type as exc:
        raise GoogleCalendarReadError(f"Google Calendar read-only request failed: {exc}.") from exc
    except Exception as exc:  # noqa: BLE001 - surfaced as user-facing read error.
        raise GoogleCalendarReadError("Google Calendar read-only request failed.") from exc

    items = response.get("items", []) if isinstance(response, dict) else []
    events = [block for item in items if isinstance(item, dict) for block in [_event_to_block(item, target_date)] if block]
    return GoogleCalendarReadResult(
        events=events,
        status=f"Google Calendar read-only: {len(events)} Termin(e) geladen.",
        status_details=(f"Google Calendar ID: {calendar_id}.",),
    )
