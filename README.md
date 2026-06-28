# Nico Day Planner v0.6-calendar

Persönlicher Tagesplaner mit sicherem Dry-Run-Standard. Version 0.6-calendar erzeugt jeden Abend einen realistischen Textvorschlag für den nächsten Tag, kann Todoist read-only lesen und Google Calendar read-only als Kalenderquelle verwenden.

## Status und Sicherheitsgrenzen

Version 0.6-calendar bleibt bewusst sicher:

- Standardquelle ist lokales JSON (`data/example_tasks.json`).
- Optional können offene Todoist-Aufgaben read-only gelesen werden.
- Wenn `TODOIST_API_TOKEN` fehlt oder Todoist nicht lesbar ist, fällt der Planer sauber auf JSON zurück.
- Google Calendar darf über `--calendar-source google` read-only als Kalenderquelle gelesen werden.
- Google Calendar Write Mode ist nur erlaubt, wenn `--write-calendar` gesetzt ist und zusätzlich `GOOGLE_CALENDAR_WRITE_ENABLED=true` in der Umgebung steht.
- Ersetzen/Löschen ist nur mit `--replace-auto-events` erlaubt und nur für Events mit Marker `NICO_DAY_PLANNER_AUTO`.
- Manuelle Kalendertermine und Events ohne Marker `NICO_DAY_PLANNER_AUTO` dürfen nicht gelöscht oder geändert werden.
- Es werden keine Todoist-Aufgaben verändert, abgeschlossen, verschoben oder gelöscht.
- Es werden keine Todoist-Labels geändert und keine Todoist-Kommentare geschrieben.
- Es werden keine Secrets ins Repository geschrieben.
- Der Plan ist nur ein Vorschlag und muss manuell geprüft werden.

## Dateien

- `AGENTS.md`: Dauerhafte Projektregeln und Sicherheitsvorgaben.
- `README.md`: Bedienung, Status und Tests.
- `requirements.txt`: Python-Abhängigkeiten für Todoist/Google-Calendar-Läufe.
- `rules.yaml`: Konfigurierbare Planungsregeln für Version 0.6-calendar.
- `planner_prompt.md`: Prompt-Vorlage für spätere LLM-Planung.
- `data/example_tasks.json`: Lokale Beispiel-Aufgaben.
- `data/example_calendar.json`: Lokale Beispiel-Blocker als JSON-Fallback.
- `scripts/planner.py`: Freundliche Anwendungsschicht-CLI für Preview, Write und Review-Platzhalter.
- `scripts/dry_run_plan.py`: Planer mit JSON-Default, optionalem Todoist-Read-only-Modus und Google-Calendar-Read-only-Quelle.
- `scripts/todoist_client.py`: Minimaler Todoist-Client mit ausschließlich lesendem `GET /rest/v2/tasks`.

## Schnellstart

Installiere zuerst die Projekt-Dependencies, insbesondere vor Google-Calendar-Tests:

```bash
python3 -m pip install -r requirements.txt
```

Google-Credentials werden nicht ins Repository geschrieben. Setze sie ausschließlich über Environment Variables oder lokale, nicht versionierte Dateien.

```bash
python3 scripts/dry_run_plan.py
```

Das ist identisch mit:

```bash
python3 scripts/dry_run_plan.py --source json
```

Optionaler Todoist-Read-only-Lauf:

```bash
export TODOIST_API_TOKEN="dein-lokaler-token"
python3 scripts/dry_run_plan.py --source todoist
```

Google-Calendar-Read-only-Lauf:

```bash
export GOOGLE_CALENDAR_CREDENTIALS_JSON='{...}'
export GOOGLE_CALENDAR_ID="dein-kalender@example.com"
python3 scripts/dry_run_plan.py --source todoist --calendar-source google
```

Google-Calendar-Schreiben bleibt standardmäßig blockiert und ist nur mit beiden Gates erlaubt:

```bash
export GOOGLE_CALENDAR_WRITE_ENABLED=true
python3 scripts/dry_run_plan.py --source todoist --calendar-source google --write-calendar
```

Automatisches Ersetzen/Löschen ist zusätzlich nur mit `--replace-auto-events` erlaubt und betrifft ausschließlich Events mit Marker `NICO_DAY_PLANNER_AUTO`.

Ohne `TODOIST_API_TOKEN` wird kein Fehler geworfen; der Planer meldet den fehlenden Token und nutzt lokale JSON-Beispieldaten.

## Anwendungsschicht-CLI

Phase 1 ergänzt eine einfache, freundlichere CLI unter `scripts/planner.py`. Sie verpackt die bestehende sichere Planner-Logik, ohne die Sicherheitsregeln zu lockern.

Preview für morgen:

```bash
python3 scripts/planner.py preview tomorrow
```

Das ruft intern den bestehenden Planner mit Todoist read-only und Google Calendar read-only auf:

```bash
python3 scripts/dry_run_plan.py --source todoist --calendar-source google
```

Write für morgen:

```bash
GOOGLE_CALENDAR_WRITE_ENABLED=true python3 scripts/planner.py write tomorrow
```

Write leitet an den bestehenden Planner mit `--write-calendar --replace-auto-events` weiter. Das Schreiben bleibt weiterhin nur erlaubt, wenn zusätzlich `GOOGLE_CALENDAR_WRITE_ENABLED=true` gesetzt ist. Ohne dieses Environment-Gate blockiert der bestehende Planner den Schreibzugriff weiterhin.

Review für gestern:

```bash
python3 scripts/planner.py review yesterday
```

`review` ist in Phase 1 nur ein Platzhalter. Es werden keine Todoist-Aufgaben und keine Google-Calendar-Termine verändert. In Phase 2/3 soll Review geplante Auto-Events auswerten und Feedback erfassen.

Optionale Phase-1-Parameter werden angezeigt und als Environment Variables an den bestehenden Planner weitergereicht:

```bash
python3 scripts/planner.py preview tomorrow --mode light
python3 scripts/planner.py preview tomorrow --note "Morgen nur Werkstatt und abends frei"
python3 scripts/planner.py preview tomorrow --from 09:00 --to 21:00
```

Unterstützte Modi sind `normal`, `light`, `focus-workshop`, `admin-evening`, `no-evening` und `push`.

## Planungsregeln in Version 0.6-calendar

Der Planer berücksichtigt folgende Regeln:

- Planung nur für morgen zwischen 09:00 und 23:00 Uhr.
- Montag 09:00–17:00: Werkstatt Mengen.
- Dienstag 09:00–14:00: Werkstatt; 14:00–16:00: Soundwerk.
- Mittwoch 09:00–14:00: Werkstatt; 14:00–18:30: Soundwerk.
- Donnerstag 09:00–12:00: Werkstatt; 14:00–18:00 und 20:00–23:00: ALEGRA/Producing Alex/Nico im Studio Aulendorf.
- Freitag 09:00–17:00: Werkstatt.
- Samstag flexibel.
- Sonntag frei / Haushalt / Büro.
- Fahrt Mengen ↔ Aulendorf blockiert 60 Minuten, wenn passende Tagesblöcke direkt aufeinander folgen.
- Admin, Buchhaltung und Krankenkasse werden bevorzugt abends und nicht nach 21:00 Uhr geplant.
- Soundwerk-Planung wird nur direkt in der Stunde vor Unterricht eingeplant.
- Werkstattdiagnosen erhalten 15 Minuten Reset-Puffer.
- Maximal 6 Hauptaufgaben und 2 Mini-Tasks werden automatisch eingeplant.
- Maximal 70 Prozent der freien Zeit werden aktiv verplant.
- Aufgaben über 120 Minuten werden nicht vollständig eingeplant, sondern zur Zerlegung vorgeschlagen.
- Aufgaben ohne Dauer erhalten eine geschätzte Dauer und werden klar markiert.
- Am Ende erscheint immer eine Liste „Nicht eingeplant“.

## Datenformat Aufgaben

`data/example_tasks.json` enthält eine Liste mit Aufgaben:

```json
{
  "id": "task-001",
  "title": "Fehlerdiagnose Verstärker durchführen",
  "category": "Werkstatt",
  "priority": "P1",
  "duration_minutes": 90,
  "notes": "Optionaler Hinweis"
}
```

`duration_minutes` darf `null` sein. Dann schätzt der Planer 30 Minuten und markiert die Aufgabe im Output.

## Datenformat lokale Kalender-Blocker

`data/example_calendar.json` enthält nur lokale Beispiel-Blocker im Tagesformat:

```json
{
  "id": "event-001",
  "title": "Morgenroutine / Frühstück",
  "calendar": "Privat",
  "start": "09:00",
  "end": "09:30"
}
```

Diese Datei ist der lokale JSON-Fallback. Google Calendar wird nur gelesen, wenn `--calendar-source google` explizit gesetzt wird.

## Tests

Vor Tests mit `--calendar-source google` muss die Umgebung mit `python3 -m pip install -r requirements.txt` vorbereitet sein. Wenn die aktuelle Codex-Umgebung die neuen Requirements noch nicht installiert hat, ist ein neuer Codex-Task bzw. Environment-Rebuild nötig.

```bash
python3 -m json.tool data/example_tasks.json >/dev/null
python3 -m json.tool data/example_calendar.json >/dev/null
python3 -m py_compile scripts/todoist_client.py
python3 -m py_compile scripts/google_calendar_client.py
python3 -m py_compile scripts/dry_run_plan.py
python3 scripts/dry_run_plan.py --source json
python3 scripts/dry_run_plan.py --source todoist
python3 scripts/dry_run_plan.py --source todoist --calendar-source google
```

Google-Calendar-Read-only-Lauf:

```bash
export GOOGLE_CALENDAR_CREDENTIALS_JSON='{...}'
export GOOGLE_CALENDAR_ID="dein-kalender@example.com"
python3 scripts/dry_run_plan.py --source todoist --calendar-source google
```

Google-Calendar-Schreiben bleibt standardmäßig blockiert und ist nur mit beiden Gates erlaubt:

```bash
export GOOGLE_CALENDAR_WRITE_ENABLED=true
python3 scripts/dry_run_plan.py --source todoist --calendar-source google --write-calendar
```

Automatisches Ersetzen/Löschen ist zusätzlich nur mit `--replace-auto-events` erlaubt und betrifft ausschließlich Events mit Marker `NICO_DAY_PLANNER_AUTO`.
