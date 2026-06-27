# AGENTS.md – Regeln für den persönlichen Tagesplaner

Diese Regeln gelten dauerhaft für alle Arbeiten in diesem Repository.

## Zweck des Projekts

Dieses Repository baut einen persönlichen Tagesplaner, der jeden Abend einen realistischen Tagesplan für den nächsten Tag als Textvorschlag erzeugt.

## Version-0.6-Calendar-Sicherheitsregel

- Version 0.6-calendar bleibt standardmäßig ein Dry-Run.
- Todoist darf ausschließlich read-only als Aufgabenquelle gelesen werden.
- Es dürfen keine Todoist-Aufgaben verändert, abgeschlossen, verschoben oder gelöscht werden.
- Es dürfen keine Todoist-Labels verändert und keine Todoist-Kommentare geschrieben werden.
- Google Calendar darf read-only über `--calendar-source google` gelesen werden.
- Google Calendar Write Mode ist nur erlaubt, wenn `--write-calendar` gesetzt ist und zusätzlich `GOOGLE_CALENDAR_WRITE_ENABLED=true` in der Umgebung steht.
- Google Calendar Events dürfen nur ersetzt oder gelöscht werden, wenn `--replace-auto-events` gesetzt ist und die Events den Marker `NICO_DAY_PLANNER_AUTO` tragen.
- Manuelle Kalendertermine oder Google Calendar Events ohne Marker `NICO_DAY_PLANNER_AUTO` dürfen niemals gelöscht oder geändert werden.
- Secrets wie API-Tokens dürfen niemals ins Repository geschrieben werden.
- Google-Credentials bleiben ausschließlich in Environment Variables oder lokalen, nicht versionierten Dateien.
- Vor Google-Calendar-Tests müssen die Projekt-Dependencies aus `requirements.txt` in der Umgebung installiert sein.
- Der erzeugte Plan ist ein Vorschlag und muss vom Nutzer manuell geprüft werden.

## Planungsprinzipien

- Plane nur für morgen zwischen 09:00 und 23:00 Uhr.
- Lokale Kalender-Beispieldaten, Google Calendar Read-only-Blocker und feste Wochenstruktur sind blockierte Zeiten und dürfen nie überschrieben werden.
- Nutze Google Calendar in Version 0.6-calendar standardmäßig nur lesend; Schreiben erfordert die expliziten Sicherheitsgates.
- Priorisiere P1-Aufgaben vor niedrigeren Prioritäten.
- Aufgaben ohne angegebene Dauer müssen geschätzt und klar als geschätzt markiert werden.
- Aufgaben über 120 Minuten dürfen nicht automatisch eingeplant werden; schlage stattdessen eine Zerlegung vor.
- Plane maximal 70 Prozent der freien Tageszeit aktiv ein.
- Plane maximal 6 Hauptaufgaben und 2 Mini-Tasks automatisch ein.
- Baue täglich Pufferzeiten ein.
- Werkstattdiagnosen bekommen 15 Minuten Reset-Puffer.
- Soundwerk-Planung soll direkt in der Stunde vor Unterricht liegen, nicht am Vortag.
- Fahrt Mengen ↔ Aulendorf blockiert 60 Minuten.
- Gib am Ende immer eine Liste „nicht eingeplant“ aus.

## Kategorien

Die unterstützten Kategorien sind:

- Werkstatt
- Studio
- ALEGRA
- Haushalt
- Privat
- LIVE
- Soundwerk
- Buchhaltung

## Kategoriepräferenzen

- Werkstatt-Diagnose eher vormittags oder nachmittags planen, nicht spät nachts.
- Buchhaltung nicht nach 21:00 Uhr planen.
- Haushalt bevorzugt als kleine Lückenfüller planen.
- Privat/Gesundheit darf nicht vollständig verdrängt werden.

## Umgang mit Änderungen

- Halte Konfigurationsregeln nach Möglichkeit in `rules.yaml`.
- Halte den täglichen LLM-Prompt in `planner_prompt.md`.
- Halte ausführbare Logik in `scripts/`.
- Schreibe keine Todoist-Mutationen.
- Halte Todoist-Zugriff in `scripts/todoist_client.py` ausschließlich read-only.
- Halte Google Calendar Schreibzugriffe in `scripts/google_calendar_client.py` durch `--write-calendar`, `GOOGLE_CALENDAR_WRITE_ENABLED=true` und den Marker `NICO_DAY_PLANNER_AUTO` abgesichert.
