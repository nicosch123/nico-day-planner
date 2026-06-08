# AGENTS.md – Regeln für den persönlichen Tagesplaner

Diese Regeln gelten dauerhaft für alle Arbeiten in diesem Repository.

## Zweck des Projekts

Dieses Repository baut einen persönlichen Tagesplaner, der jeden Abend einen realistischen Tagesplan für den nächsten Tag als Textvorschlag erzeugt.

## Version-0.5-Sicherheitsregel

- Version 0.5 ist ausschließlich ein lokaler Dry-Run.
- Es darf kein Google-Kalender-Zugriff eingebaut werden.
- Todoist darf optional nur read-only als Aufgabenquelle gelesen werden.
- Es dürfen keine Todoist-Aufgaben verändert, abgeschlossen, verschoben oder gelöscht werden.
- Secrets wie API-Tokens dürfen niemals ins Repository geschrieben werden.
- Externe Datenquellen dürfen nur gelesen werden.
- Der erzeugte Plan ist ein Vorschlag und muss vom Nutzer manuell geprüft werden.

## Planungsprinzipien

- Plane nur für morgen zwischen 09:00 und 23:00 Uhr.
- Lokale Kalender-Beispieldaten und feste Wochenstruktur sind blockierte Zeiten und dürfen nie überschrieben werden.
- Greife in Version 0.5 nicht auf Google Kalender zu.
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
- Schreibe keine produktiven Kalender- oder Todoist-Mutationen, solange Version 0.5 aktiv ist.
- Halte Todoist-Zugriff in `scripts/todoist_client.py` ausschließlich read-only.
