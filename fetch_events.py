import csv
import json
import urllib.request

SHEET_CSV_URL = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vQr46JT74Pg6LFvcIh3c9rxE11Sk9a6iqV6482Fj1vPOuHIXGN2zVq41clxIhjv4Z8P0HnhxKRm-ewY/pub?output=csv'

def fetch_events():
    with urllib.request.urlopen(SHEET_CSV_URL) as response:
        content = response.read().decode('utf-8')

    reader = csv.reader(content.splitlines())
    headers = next(reader)  # skip header row

    events = []
    for row in reader:
        if not row or not row[0].strip():
            continue
        def col(i):
            return row[i].strip() if i < len(row) else ''
        events.append({
            'datum':           col(0),
            'uhrzeit':         col(1),
            'titel':           col(2),
            'ensemble':        col(3),
            'ort':             col(4),
            'adresse':         col(5),
            'link':            col(6),
            'beschreibung':    col(7),
            'beschreibung_en': col(8),
        })

    with open('events.json', 'w', encoding='utf-8') as f:
        json.dump(events, f, ensure_ascii=False, indent=2)

    print(f'Done: {len(events)} events written to events.json')

fetch_events()
