# Nico Day Planner

Persönlicher Tagesplaner als Dry-Run: Jeden Abend um 23:00 Uhr sollen offene Todoist-Aufgaben und feste Google-Kalender-Termine für den nächsten Tag ausgewertet werden. Daraus entsteht ein realistischer Tagesplan als Textvorschlag.

## Status

Version 1 ist bewusst read-only:

- Es wird nichts in Google Kalender geschrieben, geändert oder gelöscht.
- Es werden keine Todoist-Aufgaben verändert, abgeschlossen, verschoben oder gelöscht.
- Der Plan wird nur als Textvorschlag erzeugt.

## Dateien

- `AGENTS.md`: Dauerhafte Projektregeln und Sicherheitsvorgaben.
- `rules.yaml`: Konfigurierbare Planungsregeln.
- `planner_prompt.md`: Täglicher Prompt für die Planerstellung.
- `scripts/dry_run_plan.py`: Lokaler Dry-Run-Planer mit Beispiel-Daten.
- `data/example_tasks.json`: Lokale Beispiel-Aufgaben ohne Todoist-Zugriff.
- `data/example_calendar.json`: Lokale Beispiel-Termine ohne Google-Kalender-Zugriff.

## Planungsregeln

Der Planer arbeitet mit diesen Grundregeln:

- Planung für morgen zwischen 09:00 und 23:00 Uhr.
- Feste Google-Kalender-Termine blockieren Zeitfenster und dürfen nie überschrieben werden.
- P1-Aufgaben werden zuerst eingeplant.
- Aufgaben ohne Dauer werden geschätzt und entsprechend markiert.
- Aufgaben über 120 Minuten werden nicht automatisch eingeplant, sondern zur Zerlegung vorgeschlagen.
- Werkstatt-Diagnose wird bevorzugt vormittags oder nachmittags geplant.
- Buchhaltung wird nicht nach 21:00 Uhr geplant.
- Haushalt wird bevorzugt als kleiner Lückenfüller genutzt.
- Privat/Gesundheit darf nicht vollständig verdrängt werden.
- Maximal 70 Prozent des freien Tages werden verplant.
- Es werden täglich Puffer eingebaut.
- Am Ende wird immer eine Liste „nicht eingeplant“ ausgegeben.

## Kategorien

- Werkstatt
- Studio
- ALEGRA
- Haushalt
- Privat
- LIVE
- Soundwerk
- Buchhaltung

## Lokaler Dry-Run-Test

Der Standard-Dry-Run nutzt lokale JSON-Dateien:

- `data/example_tasks.json` für Beispiel-Aufgaben aus allen Kategorien.
- `data/example_calendar.json` für feste Beispiel-Termine und Wochenstruktur.

Dabei werden keine Daten aus Todoist oder Google Kalender gelesen und es wird nichts geschrieben.

JSON-Test ausführen:

```bash
python3 scripts/dry_run_plan.py --source json
```

`--source json` ist der Standard, daher funktioniert auch weiterhin:

```bash
python3 scripts/dry_run_plan.py
```

Das Script gibt einen Tagesplan als Markdown-Text aus. Dabei werden freie Zeitfenster zwischen 09:00 und 23:00 Uhr berechnet, feste Termine blockiert, maximal 70 Prozent der freien Zeit verplant und Aufgaben über 120 Minuten unter „Vorschläge zur Zerlegung“ ausgegeben.

Für Tests bestimmter Wochentage kann ein Zieldatum gesetzt werden:

```bash
NICO_PLAN_DATE=2026-06-11 python3 scripts/dry_run_plan.py
```

Ohne `NICO_PLAN_DATE` plant das Script für morgen.

## Todoist read-only anbinden

Version 0.5 kann Todoist optional als Aufgabenquelle lesen. Google Kalender wird weiterhin nicht per API gelesen oder geschrieben; die Kalender-/Wochenstruktur kommt im Dry-Run aus `data/example_calendar.json`.

1. Erstelle in Todoist einen API-Token unter `Settings` → `Integrations` → `Developer`.
2. Speichere den Token lokal als Umgebungsvariable:

   ```bash
   export TODOIST_API_TOKEN="dein-token"
   ```

3. Todoist-Dry-Run starten:

   ```bash
   python3 scripts/dry_run_plan.py --source todoist
   ```

4. Wenn `TODOIST_API_TOKEN` fehlt oder Todoist nicht erreichbar ist, fällt das Script mit klarer Meldung auf lokale JSON-Aufgaben zurück.
5. Die Todoist-Anbindung ist strikt read-only: Es werden nur offene Aufgaben und Projekte gelesen. Es werden keine Aufgaben abgeschlossen, verändert, verschoben oder gelöscht, keine Labels geändert und keine Kommentare geschrieben.

### Todoist-Mapping

- Projektname → Kategorie (`Werkstatt`, `Studio`, `ALEGRA`, `Haushalt`, `Privat`, `LIVE`, `Soundwerk`, `Buchhaltung`).
- Todoist-Priorität → `P1` bis `P4`.
- Titel → Aufgaben-Titel.
- Beschreibung → Notizen und Dauer-Erkennung.
- Labels wie `15min`, `30min`, `45min`, `60min`, `90min`, `120min` setzen die Dauer.
- Beschreibungen wie `Dauer: 60`, `Dauer: 90 min` oder `duration: 45` setzen ebenfalls die Dauer.
- Kontextlabels wie `Mengen`, `Aulendorf`, `Werkstatt`, `Studio`, `Zuhause`, `Computer`, `Telefon`, `Abends`, `Unterwegs` werden als weiche Planungshinweise genutzt.

## Google Kalender read-only anbinden

1. Lege in der Google Cloud Console ein Projekt an.
2. Aktiviere die Google Calendar API.
3. Erstelle OAuth-Client-Zugangsdaten für eine Desktop-App oder eine geeignete lokale Anwendung.
4. Lade die OAuth-Credentials herunter und speichere sie lokal, zum Beispiel als `credentials/google-calendar.json`.
5. Verwende ausschließlich den Scope:

   ```text
   https://www.googleapis.com/auth/calendar.readonly
   ```

6. Rufe nur Termine für den nächsten Tag ab.
7. Verwende diese Termine als feste Blocker im Plan.
8. In Version 1 keine Schreib-Scopes wie `calendar.events` oder `calendar` verwenden.

## Automatisierung um 23:00 Uhr

Eine einfache lokale Cron-Variante könnte später so aussehen:

```cron
0 23 * * * cd /pfad/zu/nico-day-planner && python3 scripts/dry_run_plan.py >> logs/dry_run_plan.log 2>&1
```

Vor der Automatisierung sollten Todoist- und Google-Kalender-Zugriff lokal erfolgreich im Read-only-Modus getestet werden.

## Nächste Ausbaustufen

- Todoist-Aufgaben read-only laden.
- Google-Kalender-Termine read-only laden.
- Daten normalisieren und Kategorien erkennen.
- Prompt mit realen Eingaben ausführen.
- Tagesplan als Markdown-Datei speichern.
- Optional Benachrichtigung per E-Mail, Messenger oder lokaler Datei ergänzen.
