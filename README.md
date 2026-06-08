# Nico Day Planner v0.5

Persönlicher Tagesplaner als lokaler Dry-Run. Version 0.5 erzeugt jeden Abend einen realistischen Textvorschlag für den nächsten Tag, ohne externe Daten zu verändern.

## Status und Sicherheitsgrenzen

Version 0.5 ist bewusst klein und sicher:

- Standardquelle ist lokales JSON (`data/example_tasks.json`).
- Optional können offene Todoist-Aufgaben read-only gelesen werden.
- Wenn `TODOIST_API_TOKEN` fehlt oder Todoist nicht lesbar ist, fällt der Planer sauber auf JSON zurück.
- Es gibt keinen Google-Kalender-Zugriff.
- Es werden keine Todoist-Aufgaben verändert, abgeschlossen, verschoben oder gelöscht.
- Es werden keine Secrets ins Repository geschrieben.
- Der Plan ist nur ein Vorschlag und muss manuell geprüft werden.

## Dateien

- `AGENTS.md`: Dauerhafte Projektregeln und Sicherheitsvorgaben.
- `README.md`: Bedienung, Status und Tests.
- `rules.yaml`: Konfigurierbare Planungsregeln für Version 0.5.
- `planner_prompt.md`: Prompt-Vorlage für spätere LLM-Planung.
- `data/example_tasks.json`: Lokale Beispiel-Aufgaben.
- `data/example_calendar.json`: Lokale Beispiel-Blocker; kein Google-Kalender.
- `scripts/dry_run_plan.py`: Lokaler Planer mit JSON-Default und optionalem Todoist-Read-only-Modus.
- `scripts/todoist_client.py`: Minimaler Todoist-Client mit ausschließlich lesendem `GET /rest/v2/tasks`.

## Schnellstart

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

Ohne `TODOIST_API_TOKEN` wird kein Fehler geworfen; der Planer meldet den fehlenden Token und nutzt lokale JSON-Beispieldaten.

## Planungsregeln in Version 0.5

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

Diese Datei ist kein Google-Kalender-Export und löst keinen Google-Zugriff aus.

## Tests

```bash
python3 -m json.tool data/example_tasks.json >/dev/null
python3 -m json.tool data/example_calendar.json >/dev/null
python3 -m py_compile scripts/dry_run_plan.py
python3 scripts/dry_run_plan.py --source json
python3 scripts/dry_run_plan.py --source todoist
```
