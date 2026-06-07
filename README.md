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

Der aktuelle Dry-Run nutzt ausschließlich lokale JSON-Dateien:

- `data/example_tasks.json` für Beispiel-Aufgaben aus allen Kategorien.
- `data/example_calendar.json` für feste Beispiel-Termine für morgen.

Es werden keine Daten aus Todoist oder Google Kalender gelesen und es wird nichts geschrieben.

Test ausführen:

```bash
python3 scripts/dry_run_plan.py
```

Das Script gibt einen Tagesplan als Markdown-Text aus. Dabei werden freie Zeitfenster zwischen 09:00 und 23:00 Uhr berechnet, feste Termine blockiert, maximal 70 Prozent der freien Zeit verplant und Aufgaben über 120 Minuten unter „Vorschläge zur Zerlegung“ ausgegeben.

## Todoist read-only anbinden

1. Erstelle in Todoist einen API-Token unter `Settings` → `Integrations` → `Developer`.
2. Speichere den Token lokal als Umgebungsvariable:

   ```bash
   export TODOIST_API_TOKEN="dein-token"
   ```

3. Für Version 1 nur lesende Endpunkte verwenden, zum Beispiel offene Aufgaben abrufen.
4. Keine Endpunkte zum Abschließen, Aktualisieren, Verschieben oder Löschen von Aufgaben verwenden.
5. Später kann `scripts/dry_run_plan.py` die Aufgaben laden und in ein neutrales Eingabeformat für den Prompt umwandeln.

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
