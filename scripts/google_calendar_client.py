#!/usr/bin/env python3
"""Google Calendar client for Nico Day Planner.

Reads events by default. Calendar writes are guarded by the CLI and environment
safety checks in ``dry_run_plan.py`` and only operate on planner-owned events.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

CREDENTIALS_ENV_VAR = "GOOGLE_CALENDAR_CREDENTIALS_JSON"
CALENDAR_ID_ENV_VAR = "GOOGLE_CALENDAR_ID"
DEFAULT_CALENDAR_ID = "primary"
READ_ONLY_SCOPES = ("https://www.googleapis.com/auth/calendar.readonly",)
WRITE_SCOPES = ("https://www.googleapis.com/auth/calendar.events",)
AUTO_EVENT_MARKER = "NICO_DAY_PLANNER_AUTO"
WEEK_AUTO_EVENT_MARKER = "NICO_WEEK_PLANNER_AUTO"
PLANNER_AUTO_EVENT_MARKERS = (AUTO_EVENT_MARKER, WEEK_AUTO_EVENT_MARKER)
ABSENCE_ALL_DAY_KEYWORDS = ("urlaub", "frei", "krank", "abwesend", "reise", "block", "nico_block_day")
DEFAULT_CALENDAR_TIME_ZONE = "Europe/Berlin"


@dataclass(frozen=True)
class GoogleCalendarReadResult:
    """Result of a read-only Google Calendar fetch.

    ``events`` contains only hard blocking Google Calendar events. Existing
    planner-owned events are reported separately so they can be replaced without
    reducing the free planning windows.
    """

    events: list[dict[str, Any]]
    status: str
    used_fallback: bool = False
    status_details: tuple[str, ...] = field(default_factory=tuple)
    auto_events: list[dict[str, Any]] = field(default_factory=list)


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


def _access_token(credentials_payload: dict[str, Any], scopes: tuple[str, ...]) -> str:
    """Create and refresh a service-account access token for Google Calendar REST calls."""
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    try:
        credentials = service_account.Credentials.from_service_account_info(
            credentials_payload,
            scopes=scopes,
        )
        credentials.refresh(Request())
    except Exception as exc:  # noqa: BLE001 - surfaced as user-facing Google Calendar error.
        raise GoogleCalendarReadError("Google Calendar OAuth-Token konnte nicht erstellt werden.") from exc

    if not credentials.token:
        raise GoogleCalendarReadError("Google Calendar OAuth-Token ist leer.")
    return str(credentials.token)


def _calendar_events_url(calendar_id: str, event_id: str | None = None, query: dict[str, str] | None = None) -> str:
    """Build a Google Calendar REST events URL with URL-encoded calendar and event ids."""
    encoded_calendar_id = urllib.parse.quote(calendar_id, safe="")
    url = f"https://www.googleapis.com/calendar/v3/calendars/{encoded_calendar_id}/events"
    if event_id is not None:
        url = f"{url}/{urllib.parse.quote(event_id, safe='')}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    return url


def _google_calendar_rest_request(
    credentials_payload: dict[str, Any],
    scopes: tuple[str, ...],
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one authorized Google Calendar REST request via urllib."""
    token = _access_token(credentials_payload, scopes)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response_body = response.read()
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")[:1000]
        raise GoogleCalendarReadError(
            f"Google Calendar REST request failed: HTTP {exc.code} {exc.reason}: {error_body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise GoogleCalendarReadError(f"Google Calendar REST request failed: {exc.reason}.") from exc

    if not response_body:
        return {}
    try:
        decoded = json.loads(response_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise GoogleCalendarReadError("Google Calendar REST response enthielt ungültiges JSON.") from exc
    if not isinstance(decoded, dict):
        raise GoogleCalendarReadError("Google Calendar REST response war kein JSON-Objekt.")
    return decoded


def _parse_google_datetime(value: dict[str, str], fallback_date: date, fallback_time: time) -> datetime:
    """Parse a Google Calendar date/dateTime field into a naive planner-local datetime."""
    date_time_value = value.get("dateTime")
    if date_time_value:
        normalized = date_time_value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(ZoneInfo(DEFAULT_CALENDAR_TIME_ZONE)).replace(tzinfo=None)
        return parsed

    date_value = value.get("date")
    if date_value:
        return datetime.combine(date.fromisoformat(date_value), fallback_time)

    return datetime.combine(fallback_date, fallback_time)


def _is_absence_all_day_title(title: str) -> bool:
    """Return whether an all-day-style title should still block the day."""
    normalized = title.lower()
    return any(keyword in normalized for keyword in ABSENCE_ALL_DAY_KEYWORDS)


def _is_google_all_day_event(start_raw: dict[str, str], end_raw: dict[str, str]) -> bool:
    """Return whether Google supplied an all-day date/date event."""
    return bool(
        start_raw.get("date")
        and end_raw.get("date")
        and not start_raw.get("dateTime")
        and not end_raw.get("dateTime")
    )


def _event_to_block(event: dict[str, Any], target_date: date) -> tuple[dict[str, Any] | None, str | None]:
    """Map one Google Calendar event to the planner's neutral block schema.

    Transparent events, existing planner-owned auto events, and true Google
    all-day date/date reminder-style events are ignored as blockers unless the
    all-day title clearly indicates absence or an intentional block. Timed
    ``dateTime`` events are always hard blockers unless they are explicitly
    transparent or planner-owned auto events. The optional note explains why a
    loaded Google event was not returned as a blocking planner block.
    """
    if event.get("status") == "cancelled":
        return None, None

    title = str(event.get("summary") or "Ohne Titel")
    if _contains_auto_marker(event):
        return None, f"Bestehendes Planner-Auto-Event nicht als Blocker gewertet: {title}."
    if event.get("transparency") == "transparent":
        return None, f"Nicht blockierend wegen transparency=transparent: {title}."

    start_raw = event.get("start")
    end_raw = event.get("end")
    if not isinstance(start_raw, dict) or not isinstance(end_raw, dict):
        return None, None

    start = _parse_google_datetime(start_raw, target_date, time(0, 0))
    end = _parse_google_datetime(end_raw, target_date, time(23, 59))
    if end <= start:
        return None, None

    all_day_style = _is_google_all_day_event(start_raw, end_raw)
    if all_day_style and not _is_absence_all_day_title(title):
        return None, f"Ganztagstermin nicht als Blocker gewertet: {title}."

    return {
        "id": str(event.get("id", "google-calendar-unknown")),
        "title": title,
        "start": start.isoformat(timespec="seconds"),
        "end": end.isoformat(timespec="seconds"),
        "location": str(event.get("location") or ""),
        "description": str(event.get("description") or ""),
        "source": "Google Calendar",
    }, None



def _event_summary(event: dict[str, Any], target_date: date) -> dict[str, Any]:
    """Return a minimal neutral summary for reporting existing planner-owned events."""
    title = str(event.get("summary") or "Ohne Titel")
    start_raw = event.get("start")
    end_raw = event.get("end")
    if isinstance(start_raw, dict) and isinstance(end_raw, dict):
        start = _parse_google_datetime(start_raw, target_date, time(0, 0)).isoformat(timespec="seconds")
        end = _parse_google_datetime(end_raw, target_date, time(23, 59)).isoformat(timespec="seconds")
    else:
        start = ""
        end = ""
    return {
        "id": str(event.get("id", "google-calendar-unknown")),
        "title": title,
        "description": str(event.get("description") or ""),
        "start": start,
        "end": end,
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
    calendar_id = os.environ.get(CALENDAR_ID_ENV_VAR, DEFAULT_CALENDAR_ID)
    day_start = datetime.combine(target_date, time.min).replace(tzinfo=timezone.utc)
    day_end = datetime.combine(target_date, time.max).replace(tzinfo=timezone.utc)
    response = _google_calendar_rest_request(
        credentials_payload,
        READ_ONLY_SCOPES,
        "GET",
        _calendar_events_url(
            calendar_id,
            query={
                "timeMin": day_start.isoformat().replace("+00:00", "Z"),
                "timeMax": day_end.isoformat().replace("+00:00", "Z"),
                "singleEvents": "true",
                "orderBy": "startTime",
                "showDeleted": "false",
            },
        ),
    )

    items = response.get("items", [])
    events: list[dict[str, Any]] = []
    auto_events: list[dict[str, Any]] = []
    non_blocking_notes: list[str] = []
    loaded_count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        loaded_count += 1
        if _contains_auto_marker(item):
            auto_events.append(_event_summary(item, target_date))
        block, note = _event_to_block(item, target_date)
        if block:
            events.append(block)
        if note:
            non_blocking_notes.append(note)

    status_details = [
        f"Google Calendar ID: {calendar_id}.",
        f"Google Calendar blockierend: {len(events)} von {loaded_count} geladenen Termin(en).",
        f"Bestehende Planner-Auto-Events gefunden: {len(auto_events)}; diese blockieren die Neuplanung nicht.",
    ]
    status_details.extend(non_blocking_notes[:5])
    if len(non_blocking_notes) > 5:
        status_details.append(f"Weitere nicht blockierende Google-Termine: {len(non_blocking_notes) - 5}.")

    return GoogleCalendarReadResult(
        events=events,
        status=f"Google Calendar read-only: {loaded_count} Termin(e) geladen.",
        status_details=tuple(status_details),
        auto_events=auto_events,
    )


def _day_bounds_utc(target_date: date) -> tuple[str, str]:
    day_start = datetime.combine(target_date, time.min).replace(tzinfo=timezone.utc)
    day_end = datetime.combine(target_date, time.max).replace(tzinfo=timezone.utc)
    return day_start.isoformat().replace("+00:00", "Z"), day_end.isoformat().replace("+00:00", "Z")


def _contains_auto_marker(event: dict[str, Any], marker: str | None = None) -> bool:
    description = event.get("description")
    if not isinstance(description, str):
        return False
    if marker is not None:
        return marker in description
    return any(auto_marker in description for auto_marker in PLANNER_AUTO_EVENT_MARKERS)


def delete_auto_events_for_date(
    target_date: date,
    calendar_id: str,
    marker: str = AUTO_EVENT_MARKER,
    not_before: datetime | None = None,
) -> int:
    """Delete only planner-owned events for the target date.

    The ownership check is intentionally narrow: an event is planner-owned only
    if its description contains the requested planner marker. Events without that
    marker are never deleted or changed by this function. When ``not_before`` is
    set, matching auto-events that started earlier are preserved as history.
    """
    raw_credentials = os.environ.get(CREDENTIALS_ENV_VAR)
    if not raw_credentials:
        raise GoogleCalendarReadError(f"{CREDENTIALS_ENV_VAR} fehlt – Google Calendar Schreiben nicht möglich.")

    credentials_payload = _load_credentials_payload(raw_credentials)
    time_min, time_max = _day_bounds_utc(target_date)

    response = _google_calendar_rest_request(
        credentials_payload,
        WRITE_SCOPES,
        "GET",
        _calendar_events_url(
            calendar_id,
            query={
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": "true",
                "orderBy": "startTime",
                "showDeleted": "false",
            },
        ),
    )
    items = response.get("items", [])
    deleted_count = 0
    for event in items:
        if not isinstance(event, dict) or not _contains_auto_marker(event, marker):
            continue
        if not_before is not None:
            start_raw = event.get("start")
            if isinstance(start_raw, dict):
                event_start = _parse_google_datetime(start_raw, target_date, time(0, 0))
                if event_start < not_before:
                    continue
        event_id = event.get("id")
        if not event_id:
            continue
        _google_calendar_rest_request(
            credentials_payload,
            WRITE_SCOPES,
            "DELETE",
            _calendar_events_url(calendar_id, event_id=str(event_id)),
        )
        deleted_count += 1
    return deleted_count


def create_calendar_event(calendar_id: str, event_body: dict[str, Any]) -> str:
    """Create one Google Calendar event and return its API id."""
    raw_credentials = os.environ.get(CREDENTIALS_ENV_VAR)
    if not raw_credentials:
        raise GoogleCalendarReadError(f"{CREDENTIALS_ENV_VAR} fehlt – Google Calendar Schreiben nicht möglich.")

    credentials_payload = _load_credentials_payload(raw_credentials)
    response = _google_calendar_rest_request(
        credentials_payload,
        WRITE_SCOPES,
        "POST",
        _calendar_events_url(calendar_id),
        body=event_body,
    )
    return str(response.get("id", ""))
