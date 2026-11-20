#!/usr/bin/env python3
"""
fetch_events.py
Läuft täglich via GitHub Actions und tut Folgendes:
  1. Holt die Konzertdaten aus Google Sheets (CSV)
  2. Übersetzt leere beschreibung_en-Felder via DeepL (falls API-Key vorhanden)
  3. Schreibt events.json
  4. Generiert individuelle HTML-Seiten für alle Konzerte unter events/
  5. Aktualisiert sitemap.xml
"""

import csv
import json
import os
import re
import sys
import urllib.request
import urllib.parse
from datetime import date

# ── Konfiguration ──────────────────────────────────────────────────────────

SHEET_CSV_URL = (
    'https://docs.google.com/spreadsheets/d/e/'
    '2PACX-1vQr46JT74Pg6LFvcIh3c9rxE11Sk9a6iqV6482Fj1vPOuHIXGN2zVq41clxIhjv4Z8P0HnhxKRm-ewY/'
    'pub?output=csv'
)

BASE_URL = 'https://earlymusic-hamburg.de'
DEEPL_API_URL = 'https://api-free.deepl.com/v2/translate'

MONTHS_DE = [
    'Januar', 'Februar', 'März', 'April', 'Mai', 'Juni',
    'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember',
]
MONTHS_SHORT = [
    'Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun',
    'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez',
]
# JS getDay()-Indizes: 0=So, 1=Mo, 2=Di, 3=Mi, 4=Do, 5=Fr, 6=Sa
SHORT_DAYS_JS = ['So', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa']


# ── DeepL ──────────────────────────────────────────────────────────────────

def get_deepl_key():
    """Liest den DeepL-API-Key aus Umgebungsvariable oder lokaler Datei."""
    key = os.environ.get('DEEPL_API_KEY', '').strip()
    if key:
        return key
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'deepl_key.txt')
    if os.path.exists(key_file):
        with open(key_file, encoding='utf-8') as f:
            return f.read().strip()
    return ''


def translate_batch(texts, api_key):
    """Übersetzt eine Liste von DE-Texten nach EN-GB via DeepL-API."""
    if not texts or not api_key:
        return [''] * len(texts)
    pairs = [('text', t) for t in texts]
    pairs += [('source_lang', 'DE'), ('target_lang', 'EN-GB')]
    data = urllib.parse.urlencode(pairs, doseq=True).encode('utf-8')
    req = urllib.request.Request(
        DEEPL_API_URL,
        data=data,
        headers={
            'Authorization': f'DeepL-Auth-Key {api_key}',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            return [t['text'] for t in result['translations']]
    except Exception as exc:
        print(f'  DeepL-Fehler: {exc}', file=sys.stderr)
        return [''] * len(texts)


# ── Hilfsfunktionen ────────────────────────────────────────────────────────

def esc(s):
    """HTML-Escape."""
    return (
        str(s)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )


def parse_date(s):
    s = s.strip()
    m = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{4})$', s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def format_date_de(d):
    return f'{d.day}. {MONTHS_DE[d.month - 1]} {d.year}'


def js_weekday(d):
    """Konvertiert Python-Wochentag (Mo=0) in JS-getDay()-Index (So=0)."""
    return (d.weekday() + 1) % 7


def make_slug(datum, ensemble, titel):
    """Erzeugt URL-sicheren Slug: YYYY-MM-DD-ensemble-name."""
    m = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{4})$', datum.strip())
    date_part = (
        f'{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}'
        if m else re.sub(r'[^\d]', '-', datum)[:10]
    )
    raw = (ensemble or titel or 'konzert').split(',')[0].split('(')[0].strip()
    for a, b in [
        ('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue'),
        ('Ä', 'ae'), ('Ö', 'oe'), ('Ü', 'ue'), ('ß', 'ss'),
    ]:
        raw = raw.replace(a, b)
    raw = re.sub(r'[^a-zA-Z0-9\s]', '', raw)
    raw = re.sub(r'\s+', '-', raw.lower()).strip('-')[:45].strip('-')
    return f'{date_part}-{raw}'


def make_meta_desc(e, d):
    """Baut Meta-Description (~155 Zeichen)."""
    parts = []
    if e['ensemble']:
        parts.append(e['ensemble'].split(',')[0].strip())
    parts.append(format_date_de(d))
    if e['ort']:
        parts.append(e['ort'])
    base = ' · '.join(parts)
    if e['beschreibung']:
        remaining = 155 - len(base) - 3
        if remaining > 20:
            desc = e['beschreibung']
            if len(desc) > remaining:
                desc = desc[:remaining - 1].rsplit(' ', 1)[0] + '…'
            base = f'{base} · {desc}'
    return base[:155]


def build_jsonld(e, d, page_url):
    """Erstellt Schema.org Event JSON-LD."""
    day_str = f'{d.year}-{str(d.month).zfill(2)}-{str(d.day).zfill(2)}'
    start = f'{day_str}T{e["uhrzeit"]}:00' if e['uhrzeit'] else day_str
    obj = {
        '@context': 'https://schema.org',
        '@type': 'Event',
        'name': e['titel'],
        'startDate': start,
        'eventStatus': 'https://schema.org/EventScheduled',
        'location': {'@type': 'Place', 'name': e['ort'], 'address': e['adresse']},
        'url': page_url,
        'organizer': {
            '@type': 'Organization',
            'name': 'Early Music Hamburg',
            'url': BASE_URL,
        },
    }
    if e['ensemble']:
        obj['performer'] = {'@type': 'PerformingGroup', 'name': e['ensemble']}
    if e['beschreibung']:
        obj['description'] = e['beschreibung']
    if e['link']:
        obj['sameAs'] = e['link']
    return json.dumps(obj, ensure_ascii=False)


# ── CSS für Event-Einzelseiten ─────────────────────────────────────────────

EVENT_CSS = """
    @font-face{font-family:'Cormorant Garamond';src:url('../cormorant-garamond-v21-latin-300.woff2') format('woff2');font-weight:300;font-style:normal;font-display:swap}
    @font-face{font-family:'Cormorant Garamond';src:url('../cormorant-garamond-v21-latin-300italic.woff2') format('woff2');font-weight:300;font-style:italic;font-display:swap}
    @font-face{font-family:'Cormorant Garamond';src:url('../cormorant-garamond-v21-latin-regular.woff2') format('woff2');font-weight:400;font-style:normal;font-display:swap}
    @font-face{font-family:'Cormorant Garamond';src:url('../cormorant-garamond-v21-latin-italic.woff2') format('woff2');font-weight:400;font-style:italic;font-display:swap}
    @font-face{font-family:'Cormorant Garamond';src:url('../cormorant-garamond-v21-latin-500.woff2') format('woff2');font-weight:500;font-style:normal;font-display:swap}
    @font-face{font-family:'Outfit';src:url('../outfit-v15-latin-300.woff2') format('woff2');font-weight:300;font-style:normal;font-display:swap}
    @font-face{font-family:'Outfit';src:url('../outfit-v15-latin-regular.woff2') format('woff2');font-weight:400;font-style:normal;font-display:swap}
    @font-face{font-family:'Outfit';src:url('../outfit-v15-latin-500.woff2') format('woff2');font-weight:500;font-style:normal;font-display:swap}
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
    :root{
      --bg:#F7F4EF;--bg-side:#E8E0D0;--bg-card:#FDFBF8;
      --text:#2C2416;--text-muted:#7A6E5F;
      --accent:#A87820;--accent-pale:#EFE5C8;--border:#DDD5C0;
      --serif:'Cormorant Garamond',Georgia,serif;
      --sans:'Outfit',system-ui,sans-serif;
      --image-w:170px;--content-w:720px
    }
    body{background:var(--bg-side);color:var(--text);font-family:var(--sans);font-weight:300;line-height:1.6}
    .page-frame{max-width:calc(var(--content-w) + 2 * var(--image-w));margin:0 auto;background:var(--bg);min-height:100vh;display:flex;flex-direction:column}
    .header-outer{border-bottom:1px solid var(--border)}
    header{display:flex;align-items:flex-end;background:#EDE7D9}
    .relief-img{width:var(--image-w);flex-shrink:0;display:block;opacity:.92}
    .header-center{flex:1;padding:2rem 2rem 1.6rem;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;text-align:center}
    .site-title{font-family:var(--serif);font-weight:300;font-size:clamp(2.2rem,5vw,3.2rem);line-height:1.1;letter-spacing:.02em}
    .site-title a{text-decoration:none;color:inherit}
    .header-rule{width:40px;height:1px;background:var(--accent);margin:.65rem auto .55rem}
    .site-subtitle{font-family:var(--sans);font-weight:300;font-size:.78rem;letter-spacing:.18em;text-transform:uppercase;color:var(--text-muted)}
    main{max-width:var(--content-w);margin:0 auto;padding:2.5rem 2rem 3rem;flex:1}
    .back-link{display:inline-block;margin-bottom:2.8rem;font-family:var(--sans);font-size:.78rem;letter-spacing:.12em;text-transform:uppercase;color:var(--text-muted);text-decoration:none}
    .back-link:hover{color:var(--accent)}
    .concert-card{display:grid;grid-template-columns:3.8rem 1fr;gap:0 1.4rem;padding:1.6rem 1.2rem;border:1px solid var(--border);background:var(--bg-card);border-radius:4px}
    .date-box{text-align:center;padding-top:.2rem}
    .date-day{font-family:var(--serif);font-size:2.4rem;font-weight:300;line-height:1;color:var(--accent)}
    .date-weekday,.date-month{font-size:.7rem;letter-spacing:.1em;text-transform:uppercase;color:var(--text-muted)}
    .concert-time{font-size:.74rem;letter-spacing:.1em;text-transform:uppercase;color:var(--text-muted);margin-bottom:.2rem}
    .concert-title{font-family:var(--serif);font-size:1.55rem;font-weight:400;line-height:1.25;margin-bottom:.25rem}
    .concert-ensemble{font-size:.9rem;color:var(--text-muted);margin-bottom:.3rem}
    .concert-venue{font-size:.78rem;color:var(--text-muted);display:inline-flex;align-items:center;gap:.25rem}
    .concert-venue::before{content:'↗';color:var(--accent);font-size:.7rem}
    .concert-venue a{color:var(--text-muted);text-decoration:none}
    .concert-venue a:hover{color:var(--accent)}
    .concert-address{font-size:.73rem;color:var(--text-muted);margin-top:.12rem;padding-left:.95rem}
    .concert-address a{color:var(--text-muted);text-decoration:none}
    .concert-address a:hover{color:var(--accent)}
    .concert-description{font-size:.83rem;color:var(--text-muted);font-style:italic;margin-top:.5rem;line-height:1.5}
    .concert-more-link{margin-top:.55rem}
    .concert-more-link a{font-size:.78rem;color:var(--accent);text-decoration:none;letter-spacing:.04em}
    .concert-more-link a:hover{text-decoration:underline}
    .past-notice{margin-top:1.5rem;padding:.9rem 1.2rem;border:1px solid var(--border);border-radius:4px;font-size:.82rem;color:var(--text-muted);font-style:italic}
    .promo-section{margin-top:3.5rem;padding-top:2.5rem;border-top:1px solid var(--border);text-align:center}
    .promo-text{font-family:var(--serif);font-weight:300;font-size:1.1rem;color:var(--text-muted);font-style:italic;margin-bottom:1rem;letter-spacing:.01em}
    .promo-btn{display:inline-block;padding:.55rem 1.6rem;border:1px solid var(--accent);color:var(--accent);font-family:var(--sans);font-size:.72rem;letter-spacing:.16em;text-transform:uppercase;text-decoration:none;transition:background .15s,color .15s}
    .promo-btn:hover{background:var(--accent);color:#fff}
    .social-bar{border-top:1px solid var(--border);padding:1.2rem 2rem;display:flex;justify-content:center;gap:1.1rem;background:var(--bg)}
    .social-bar a{display:flex;align-items:center;color:var(--text-muted);transition:color .15s}
    .social-bar a:hover{color:var(--accent)}
    .footer-outer{border-top:1px solid var(--border)}
    footer{max-width:var(--content-w);margin:0 auto;padding:.75rem 2rem;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.5rem;font-size:.73rem;color:var(--text-muted)}
    footer a{color:var(--text-muted);text-decoration:none}
    footer a:hover{color:var(--accent)}
    .footer-note{max-width:var(--content-w);margin:0 auto;padding:0 2rem .8rem;font-size:.68rem;color:var(--text-muted);text-align:center}
    @media(max-width:640px){
      .relief-img{display:none}
      .header-center{padding:1.8rem 1.5rem 1.4rem}
      .concert-card{grid-template-columns:2.8rem 1fr;gap:0 .9rem}
      .date-day{font-size:1.5rem}
      footer{flex-direction:column;align-items:flex-start}
    }
"""


def generate_event_page(e, slug, is_past):
    """Generiert eine vollständige HTML-Seite für ein einzelnes Konzert."""
    d = parse_date(e['datum'])
    if d is None:
        return None

    page_url = f'{BASE_URL}/events/{slug}.html'
    title_page = f'{e["titel"]} – {format_date_de(d)} · Early Music Hamburg'
    meta_desc = make_meta_desc(e, d)
    jsonld = build_jsonld(e, d, page_url)

    day_num = d.day
    weekday_short = SHORT_DAYS_JS[js_weekday(d)]
    month_short = MONTHS_SHORT[d.month - 1]
    time_str = f'{e["uhrzeit"]} Uhr' if e['uhrzeit'] else ''

    maps_url = (
        'https://maps.google.com/?q=' + urllib.parse.quote(e['adresse'])
        if e['adresse'] else ''
    )

    # Karten-Teile einzeln aufbauen (keine verschachtelten f-strings mit Anführungszeichen)
    time_html = f'<div class="concert-time">{esc(time_str)}</div>' if time_str else ''
    ens_html = f'<div class="concert-ensemble">{esc(e["ensemble"])}</div>' if e['ensemble'] else ''

    if e['ort']:
        if maps_url:
            venue_inner = f'<a href="{esc(maps_url)}" target="_blank" rel="noopener noreferrer">{esc(e["ort"])}</a>'
        else:
            venue_inner = esc(e['ort'])
        venue_html = f'<div class="concert-venue">{venue_inner}</div>'
    else:
        venue_html = ''

    if e['adresse']:
        if maps_url:
            addr_inner = f'<a href="{esc(maps_url)}" target="_blank" rel="noopener noreferrer">{esc(e["adresse"])}</a>'
        else:
            addr_inner = esc(e['adresse'])
        addr_html = f'<div class="concert-address">{addr_inner}</div>'
    else:
        addr_html = ''

    desc_html = (
        f'<div class="concert-description">{esc(e["beschreibung"])}</div>'
        if e['beschreibung'] else ''
    )
    if e['link']:
        link_inner = f'<a href="{esc(e["link"])}" target="_blank" rel="noopener noreferrer">Mehr Infos →</a>'
        link_html = f'<div class="concert-more-link">{link_inner}</div>'
    else:
        link_html = ''

    past_html = (
        '<p class="past-notice">Dieses Konzert hat bereits stattgefunden.</p>'
        if is_past else ''
    )

    return f'''<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(title_page)}</title>
  <meta name="description" content="{esc(meta_desc)}">
  <meta property="og:title" content="{esc(e['titel'])} – Early Music Hamburg">
  <meta property="og:description" content="{esc(meta_desc)}">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{page_url}">
  <meta property="og:image" content="{BASE_URL}/og_image.jpg">
  <link rel="canonical" href="{page_url}">
  <script type="application/ld+json">{jsonld}</script>
  <style>{EVENT_CSS}</style>
</head>
<body>
<div class="page-frame">

<div class="header-outer">
<header>
  <img src="../relief_left_web_3.jpg" class="relief-img" alt="Musikerreliefs, Sainte-Croix Bordeaux" width="170">
  <div class="header-center">
    <h1 class="site-title"><a href="{BASE_URL}/">Early Music Hamburg</a></h1>
    <div class="header-rule"></div>
    <span class="site-subtitle">Konzerte &amp; Veranstaltungen · Alte Musik</span>
  </div>
  <img src="../relief_right_web_4.jpg" class="relief-img" alt="Musikerreliefs, Sainte-Croix Bordeaux" width="170">
</header>
</div>

<main>
  <a class="back-link" href="{BASE_URL}/">&#8592; Alle Konzerte</a>

  <div class="concert-card">
    <div class="date-box">
      <div class="date-day">{day_num}</div>
      <div class="date-weekday">{weekday_short}</div>
      <div class="date-month">{month_short}</div>
    </div>
    <div class="concert-info">
      {time_html}
      <div class="concert-title">{esc(e['titel'])}</div>
      {ens_html}
      {venue_html}
      {addr_html}
      {desc_html}
      {link_html}
    </div>
  </div>
  {past_html}

  <div class="promo-section">
    <p class="promo-text">Ihr Konzert fehlt? Termin mitteilen.</p>
    <a class="promo-btn" href="mailto:hello@earlymusic-hamburg.de">Termin eintragen</a>
  </div>
</main>

<div class="social-bar">
  <a href="https://www.instagram.com/earlymusic.hamburg/" aria-label="Instagram" title="Instagram">
    <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="20" rx="5" ry="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.5" cy="6.5" r="0.5" fill="currentColor" stroke="none"/></svg>
  </a>
  <a href="https://www.facebook.com/earlymusic.hamburg" aria-label="Facebook" title="Facebook">
    <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M18 2h-3a5 5 0 0 0-5 5v3H7v4h3v8h4v-8h3l1-4h-4V7a1 1 0 0 1 1-1h3z"/></svg>
  </a>
</div>

<div class="footer-outer">
<footer>
  <span>Early Music Hamburg · Alte Musik in Hamburg</span>
  <span>
    <a href="../impressum.html">Impressum</a> ·
    <a href="../datenschutz.html">Datenschutz</a> ·
    <a href="mailto:hello@earlymusic-hamburg.de">hello@earlymusic-hamburg.de</a>
  </span>
</footer>
<p class="footer-note">Englische Übersetzungen der Veranstaltungsbeschreibungen wurden maschinell erstellt.</p>
</div>
</body>
</html>'''


# ── CSV-Abruf ──────────────────────────────────────────────────────────────

def fetch_events():
    print('Lade Konzertdaten aus Google Sheets …')
    with urllib.request.urlopen(SHEET_CSV_URL, timeout=30) as resp:
        content = resp.read().decode('utf-8')
    reader = csv.reader(content.splitlines())
    next(reader)  # Kopfzeile überspringen
    events = []
    for row in reader:
        if not row or not row[0].strip():
            continue
        def col(i, r=row):
            return r[i].strip() if i < len(r) else ''
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
    print(f'  {len(events)} Konzerte geladen.')
    return events


# ── Hauptprogramm ──────────────────────────────────────────────────────────

def main():
    events = fetch_events()

    # DeepL-Übersetzung (Batch für Effizienz)
    api_key = get_deepl_key()
    to_translate = [e for e in events if e['beschreibung'] and not e['beschreibung_en']]
    if to_translate:
        if api_key:
            print(f'Übersetze {len(to_translate)} Beschreibungen mit DeepL …')
            texts = [e['beschreibung'] for e in to_translate]
            translations = translate_batch(texts, api_key)
            for e, tr in zip(to_translate, translations):
                if tr:
                    e['beschreibung_en'] = tr
                    print(f'  ✓ {e["datum"]}  {e["titel"][:50]}')
        else:
            print('Kein DeepL-API-Key gefunden – Übersetzung übersprungen.')
            print('  Tipp: Umgebungsvariable DEEPL_API_KEY setzen oder deepl_key.txt anlegen.')
    else:
        print('Alle beschreibung_en bereits vorhanden – kein DeepL-Aufruf nötig.')

    # events.json schreiben
    with open('events.json', 'w', encoding='utf-8') as f:
        json.dump(events, f, ensure_ascii=False, indent=2)
    print(f'events.json geschrieben ({len(events)} Konzerte).')

    # Individuelle Event-Seiten generieren
    os.makedirs('events', exist_ok=True)
    today = date.today()
    slug_counts: dict[str, int] = {}
    event_pages: list[tuple[str, date, bool]] = []

    for e in events:
        if not e['datum'] or not e['titel']:
            continue
        d = parse_date(e['datum'])
        if d is None:
            print(f'  Warnung: Ungültiges Datum "{e["datum"]}" – übersprungen.', file=sys.stderr)
            continue

        slug_base = make_slug(e['datum'], e['ensemble'], e['titel'])
        if slug_base in slug_counts:
            slug_counts[slug_base] += 1
            slug = f'{slug_base}-{slug_counts[slug_base]}'
        else:
            slug_counts[slug_base] = 1
            slug = slug_base

        is_past = d < today
        html = generate_event_page(e, slug, is_past)
        if html:
            path = os.path.join('events', f'{slug}.html')
            with open(path, 'w', encoding='utf-8') as f:
                f.write(html)
            event_pages.append((slug, d, is_past))

    print(f'{len(event_pages)} Event-Seiten in events/ generiert.')

    # sitemap.xml aktualisieren
    today_str = today.isoformat()
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']

    # Hauptseiten
    lines += [
        '  <url>',
        f'    <loc>{BASE_URL}/</loc>',
        f'    <lastmod>{today_str}</lastmod>',
        '    <changefreq>weekly</changefreq>',
        '    <priority>1.0</priority>',
        '  </url>',
        '  <url>',
        f'    <loc>{BASE_URL}/impressum.html</loc>',
        '    <lastmod>2026-08-16</lastmod>',
        '    <changefreq>yearly</changefreq>',
        '    <priority>0.3</priority>',
        '  </url>',
        '  <url>',
        f'    <loc>{BASE_URL}/datenschutz.html</loc>',
        '    <lastmod>2026-08-16</lastmod>',
        '    <changefreq>yearly</changefreq>',
        '    <priority>0.3</priority>',
        '  </url>',
    ]

    # Event-Seiten
    for slug, d, is_past in event_pages:
        priority = '0.5' if is_past else '0.7'
        changefreq = 'never' if is_past else 'monthly'
        lines += [
            '  <url>',
            f'    <loc>{BASE_URL}/events/{slug}.html</loc>',
            f'    <lastmod>{today_str}</lastmod>',
            f'    <changefreq>{changefreq}</changefreq>',
            f'    <priority>{priority}</priority>',
            '  </url>',
        ]

    lines.append('</urlset>')

    with open('sitemap.xml', 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print(f'sitemap.xml aktualisiert ({len(event_pages) + 3} URLs).')

    print('Fertig.')


if __name__ == '__main__':
    main()
