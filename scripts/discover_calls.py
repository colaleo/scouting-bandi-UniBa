#!/usr/bin/env python3
"""
discover_calls.py — UniBA Grant Office Scouting Dashboard
==========================================================
Scopre nuovi bandi da TUTTE le fonti e aggiorna index.html.
Viene eseguito da GitHub Actions ogni lunedì — nessun intervento manuale.

FONTI:
  1. EU Funding & Tenders Portal  (API pubblica)
  2. FASI.eu RSS                  (gratuito, senza login)
  3. MUR.gov.it                   (PRIN, FIS, dottorati)
  4. MIMIT.gov.it / Invitalia     (incentivi PMI, Mezzogiorno)
  5. AIFA.gov.it                  (ricerca clinica indipendente)
  6. Regione Puglia / SistemaPuglia (bandi regionali)
"""

import feedparser
import hashlib
import html as html_lib
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# ── Percorsi ──────────────────────────────────────────────────────────────────
ROOT      = Path(__file__).parent.parent
INDEX     = ROOT / "index.html"
LOG_FILE  = ROOT / "scripts" / "discovery_log.md"

# ── HTTP ──────────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; UniBAGrantBot/1.0; "
        "+https://www.uniba.it/it/ricerca)"
    )
}

# ── EU F&T Portal ─────────────────────────────────────────────────────────────
FT_API = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
FT_KEY = "SOWAMDYWSUW3BR3Y"

# ── FASI.eu RSS ───────────────────────────────────────────────────────────────
FASI_RSS = "https://fasi.eu/it/news.feed?type=rss"

# ── Fonti italiane e regionali ────────────────────────────────────────────────
SOURCES_IT = [
    ("MUR",           "https://www.mur.gov.it/it/aree-tematiche/ricerca"),
    ("MIMIT",         "https://www.mimit.gov.it/it/incentivi-mise/ricerca-e-innovazione"),
    ("INVITALIA",     "https://www.invitalia.it/per-le-imprese/incentivi-e-strumenti"),
    ("AIFA",          "https://www.aifa.gov.it/ricerca-clinica-indipendente"),
    ("PUGLIA",        "https://www.regione.puglia.it/it/web/competitivita-e-innovazione/elenco-bandi"),
    ("SISTEMAPUGLIA", "https://sistema.regione.puglia.it/it/catalogo-bandi"),
]

# ── Programme → (section_id, data-category, badge_css, badge_label) ──────────
PROG_MAP = {
    "MSCA":          ("sec-msca",     "europeo",   "bp-msca",     "MSCA"),
    "EIC":           ("sec-eic",      "europeo",   "bp-eic",      "EIC"),
    "ERC":           ("sec-erc",      "europeo",   "bp-erc",      "ERC"),
    "LIFE":          ("sec-life",     "europeo",   "bp-life",     "LIFE"),
    "INTERREG":      ("sec-interreg", "europeo",   "bp-interreg", "Interreg"),
    "STEP":          ("sec-step",     "europeo",   "bp-step",     "STEP"),
    "AIFA":          ("sec-mimit",    "nazionale", "bp-mimit",    "AIFA"),
    "MIMIT":         ("sec-mimit",    "nazionale", "bp-mimit",    "MIMIT"),
    "INVITALIA":     ("sec-mimit",    "nazionale", "bp-mimit",    "MIMIT"),
    "PRIN":          ("sec-mur",      "nazionale", "bp-mur",      "MUR"),
    "MUR":           ("sec-mur",      "nazionale", "bp-mur",      "MUR"),
    "PNRR":          ("sec-mur",      "nazionale", "bp-pnrr",     "PNRR"),
    "PUGLIA":        ("sec-puglia",   "regionale", "bp-puglia",   "Puglia Region"),
    "SISTEMAPUGLIA": ("sec-puglia",   "regionale", "bp-puglia",   "Puglia Region"),
    "DEFAULT":       ("sec-horizon",  "europeo",   "bp-horizon",  "Horizon Europe"),
}

PROG_KEYWORDS = [
    ("MSCA",      ["msca", "marie sklodowska", "marie curie", "doctoral network",
                   "postdoctoral fellowship", "staff exchange", "cofund"]),
    ("EIC",       ["eic accelerator", "eic pathfinder", "eic transition", " eic "]),
    ("ERC",       [" erc ", "erc-", "european research council", "proof of concept",
                   "starting grant", "advanced grant", "consolidator grant",
                   "erc plus", "erc synergy"]),
    ("LIFE",      ["life programme", "life-", "life call", "programma life"]),
    ("INTERREG",  ["interreg", "cooperazione territoriale europea"]),
    ("STEP",      ["step programme", "strategic technologies for europe"]),
    ("AIFA",      ["aifa", "ricerca clinica indipendente", "ricerca indipendente",
                   "ricerca farmacologica no-profit"]),
    ("MIMIT",     ["mimit", "competenze pmi", "contratti sviluppo", "nuova sabatini",
                   "voucher digitalizzazione", "credito imposta r&s"]),
    ("INVITALIA", ["invitalia", "resto al sud", "smart&start", "pmi mezzogiorno",
                   "mezzogiorno"]),
    ("PRIN",      ["prin ", "prin2", "prin 20", "progetti di ricerca di rilevante",
                   "prin hybrid"]),
    ("MUR",       ["fis ", "fondo italiano scienza", "dottorato", "partenariati estesi",
                   "ecosistemi dell'innovazione", "italia domani"]),
    ("PNRR",      ["pnrr", "piano nazionale ripresa", "missione 4", "missione 1"]),
    ("PUGLIA",    ["puglia", "regione puglia", "sistema puglia", "minipia",
                   "tecnonidi", "jtf taranto", "pia innova"]),
    ("SISTEMAPUGLIA", ["sistema.puglia", "punti digitale facile"]),
]

FASI_SIGNALS = [
    "bando", "call", "aperto", "scadenza", "deadline", "finanziamento",
    "horizon", "msca", "eic", "erc", "life", "interreg", "prin",
    "pnrr", "mimit", "invitalia", "puglia", "aifa", "mur bando",
]

# Parole chiave che qualificano un item come bando di RICERCA/PROGETTUALITÀ
# (filtra via incentivi PMI, mutui, tax credit, voucher non pertinenti)
RESEARCH_SIGNALS = [
    "ricerca", "research", "innovazione", "innovation", "progetto", "project",
    "bando", "call", "avviso", "finanziamento", "grant", "funding",
    "prin", "pnrr", "horizon", "msca", "eic", "erc", "life", "interreg",
    "dottorato", "phd", "fellowship", "partenariato", "partnership",
    "r&s", "r&d", "sviluppo sperimentale", "ricerca industriale",
    "accordo", "ipcei", "ecosistema", "cluster", "polo di innovazione",
    "trasferimento tecnologico", "spin", "start-up", "startup",
    "brevetto", "patent", "proof of concept", "feasibility",
    "aifa", "ricerca clinica", "ricerca indipendente",
    "minipia", "pia innova", "tecnonidi", "jtf", "just transition",
]

# Parole chiave che ESCLUDONO un item (incentivi non pertinenti)
RESEARCH_EXCLUDE = [
    "nuova sabatini", "sabatini", "mutuo", "leasing", "finanziamento agevolato macchinari",
    "voucher digitalizzazione pmi", "credito d'imposta beni strumentali",
    "contratti di sviluppo produttivo",
    "resto al sud", "autoimprenditorialità", "autoimpiego",
    "registrazione farmaco", "autorizzazione immissione",
    "gara d'appalto", "appalto", "procurement",
    # Elementi di navigazione / intestazioni di pagina (non sono bandi)
    "vai al contenuto", "vai al sito", "salta al contenuto",
    "il sistema della ricerca", "valutazione della ricerca",
    "programmi di finanziamento",
    # Agevolazioni fiscali / contributi non pertinenti
    "credito d'imposta ricerca", "liquidazione contributi",
    "voucher 3i", "voucher per consulenza",
    # Sezioni organizzative non-bando
    "commercio artigianato", "internazionalizzazione delle imprese",
    "avviso a sportello con valutazione",
]

# Titoli troppo corti o generici da escludere (navigazione, header)
TITLE_BLACKLIST = [
    "ricerca e innovazione", "competitività e innovazione",
    "ricerca internazionale", "iniziative speciali",
    "programmi di finanziamento", "horizon europe",
    "eric e infrastrutture", "il sistema della ricerca",
]


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def detect_programme(text: str) -> str:
    low = text.lower()
    for prog, keywords in PROG_KEYWORDS:
        if any(kw in low for kw in keywords):
            return prog
    return "DEFAULT"


def make_dl_id(identifier: str) -> str:
    clean = re.sub(r"[^a-z0-9]", "", identifier.lower())[:10]
    h = hashlib.md5(identifier.encode()).hexdigest()[:4]
    return f"dl-{clean}{h}"


def days_remaining(iso: str) -> int:
    try:
        return (datetime.strptime(iso[:10], "%Y-%m-%d").date() - date.today()).days
    except Exception:
        return 999


def urgency_class(days: int) -> str:
    if days < 0:   return "urgency-expired"
    if days <= 15: return "urgency-critical"
    if days <= 45: return "urgency-high"
    if days <= 90: return "urgency-medium"
    return "urgency-low"


def fmt_date(iso: str) -> str:
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%-d %B %Y")
    except Exception:
        return iso[:10] if iso else "TBD"


def esc(s: str) -> str:
    return html_lib.escape(str(s), quote=True)


def parse_date(text: str) -> str:
    months = {
        "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4,
        "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
        "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    low = text.lower()
    m = re.search(r"(\d{1,2})\s+([a-zà-ù]+)\s+(20\d{2})", low)
    if m:
        mo = months.get(m.group(2))
        if mo:
            try:
                return f"{m.group(3)}-{mo:02d}-{int(m.group(1)):02d}"
            except Exception:
                pass
    m = re.search(r"(\d{1,2})[/\-](\d{1,2})[/\-](20\d{2})", text)
    if m:
        try:
            return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
        except Exception:
            pass
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
    return m.group(1) if m else ""


# ══════════════════════════════════════════════════════════════════════════════
#  LETTURA index.html
# ══════════════════════════════════════════════════════════════════════════════

def read_existing(html: str) -> tuple:
    dl_ids = set(re.findall(r"'(dl-[a-z0-9]+)'", html))
    titles = {t.strip().lower()[:45]
              for t in re.findall(r'class="card-title">([^<]+)</span>', html)}
    return dl_ids, titles


def is_duplicate(title: str, identifier: str, dl_ids: set, titles: set) -> bool:
    return (title.strip().lower()[:45] in titles or
            make_dl_id(identifier) in dl_ids)


# ══════════════════════════════════════════════════════════════════════════════
#  GENERAZIONE CARD
# ══════════════════════════════════════════════════════════════════════════════

def generate_card(title, description, identifier, deadline_iso, url, tags, source=""):
    prog = detect_programme(title + " " + identifier + " " + source)
    section_id, category, badge_cls, badge_label = PROG_MAP[prog]
    dl_id = make_dl_id(identifier) if deadline_iso else None

    days    = days_remaining(deadline_iso) if deadline_iso else 999
    urg     = urgency_class(days)
    dl_text = fmt_date(deadline_iso) if deadline_iso else "TBD — verifica sul portale"

    all_tags  = list(dict.fromkeys([prog if prog != "DEFAULT" else "Horizon Europe"] + tags))[:4]
    tags_html = "".join(f'<span class="tag">{esc(t)}</span>' for t in all_tags)
    if source:
        tags_html += (f' <span class="tag" style="background:#e8fff5;'
                      f'border-color:#aadecc;color:#007a50">🆕 {esc(source)}</span>')

    kw = " ".join([identifier.lower(), prog.lower(), source.lower()]
                  + [t.lower() for t in all_tags])

    dl_badge = ""
    if deadline_iso and dl_id:
        label = ("expired" if days < 0 else "today!" if days == 0
                 else "1 day" if days == 1 else f"{days}d")
        dl_badge = f'<span class="days-left" id="{dl_id}">{label}</span>'

    card = (
        f'\n      <!-- AUTO {date.today()} | {esc(identifier)} -->\n'
        f'      <div class="card {urg}" data-category="{category}"\n'
        f'           data-kw="{esc(kw)}">\n'
        f'        <div class="card-top">\n'
        f'          <span class="card-title">{esc(title[:80])}</span>\n'
        f'          <span class="badge-program {badge_cls}">{badge_label}</span>\n'
        f'        </div>\n'
        f'        <p class="card-desc">{esc(description[:230])}</p>\n'
        f'        <div class="card-meta">{tags_html}</div>\n'
        f'        <div class="card-footer">\n'
        f'          <div class="deadline"><span class="deadline-icon">📅</span>'
        f'{dl_text}{dl_badge}</div>\n'
        f'          <a class="card-link" href="{esc(url)}" target="_blank" '
        f'rel="noopener">Portal ↗</a>\n'
        f'        </div>\n'
        f'      </div>'
    )
    return card, section_id, dl_id


# ══════════════════════════════════════════════════════════════════════════════
#  INIEZIONE IN index.html
# ══════════════════════════════════════════════════════════════════════════════

def inject_card(html: str, section_id: str, card_html: str) -> str:
    pos = html.find(f'id="{section_id}"')
    if pos == -1:
        print(f"  ⚠  Sezione '{section_id}' non trovata — skip.")
        return html
    grid_start = html.find('<div class="cards-grid">', pos)
    if grid_start == -1:
        return html
    depth = 1
    p = grid_start + len('<div class="cards-grid">')
    while depth > 0 and p < len(html):
        no = html.find("<div",  p)
        nc = html.find("</div>", p)
        if nc == -1:
            break
        if no != -1 and no < nc:
            depth += 1; p = no + 4
        else:
            depth -= 1
            if depth == 0:
                return html[:nc] + card_html + "\n\n    " + html[nc:]
            p = nc + 6
    return html


def add_deadline_js(html: str, dl_id: str, iso: str) -> str:
    m = re.search(r"(const deadlines = \{[\s\S]*?)(  \};\n)", html)
    if m:
        return html[:m.start(2)] + f"    '{dl_id}':    '{iso}',\n" + html[m.start(2):]
    return html


# ══════════════════════════════════════════════════════════════════════════════
#  FONTE 1 — EU Funding & Tenders Portal
# ══════════════════════════════════════════════════════════════════════════════

def fetch_eu_portal(dl_ids, titles):
    print("📡  EU Funding & Tenders Portal…")
    try:
        payload = {
            "apiKey": FT_KEY,
            "text": "*",
            "pageSize": 150,
            "pageNumber": 1,
            "sortBy": "DEADLINE_DATE",
            "sortOrder": "ASC",
            "query": json.dumps({"bool": {"must": [
                {"terms": {"type": ["1"]}},
                {"terms": {"status": ["31094501", "31094502"]}},
            ]}}),
        }
        resp = requests.post(FT_API, json=payload, headers=HEADERS, timeout=30)
        if resp.status_code == 405:
            # fallback a GET
            resp = requests.get(FT_API, params=payload, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        print(f"   → {len(results)} call trovate")
    except Exception as e:
        print(f"   ⚠  Errore: {e}"); return []

    calls = []
    for r in results:
        try:
            title = (r.get("title") or "").strip()
            idf   = (r.get("identifier") or r.get("id") or "").strip()
            if not title or is_duplicate(title, idf, dl_ids, titles):
                continue
            raw = r.get("deadlineDate") or ""
            if isinstance(raw, list): raw = raw[0] if raw else ""
            iso = raw[:10] if isinstance(raw, str) and len(raw) >= 10 else ""
            calls.append({
                "title": title, "identifier": idf,
                "description": (r.get("description") or r.get("callDescription") or "")[:300],
                "deadline_iso": iso,
                "url": r.get("callDetailsUrl",
                              "https://ec.europa.eu/info/funding-tenders/opportunities/portal/"),
                "tags": [k for k in ["RIA","IA","CSA","PhD","SME"]
                         if k.upper() in (title+idf).upper()][:3],
                "source": "EU F&T Portal",
            })
        except Exception:
            continue
    print(f"   → {len(calls)} nuove (non già presenti)")
    return calls


# ══════════════════════════════════════════════════════════════════════════════
#  FONTE 2 — FASI.eu RSS
# ══════════════════════════════════════════════════════════════════════════════

def fetch_fasi_rss(dl_ids, titles):
    print("📰  FASI.eu RSS…")
    try:
        feed = feedparser.parse(FASI_RSS)
        print(f"   → {len(feed.entries)} articoli")
    except Exception as e:
        print(f"   ⚠  Errore: {e}"); return []

    calls = []
    for entry in feed.entries:
        try:
            title   = (entry.get("title") or "").strip()
            link    = (entry.get("link")  or "").strip()
            summary = BeautifulSoup(
                entry.get("summary") or "", "lxml"
            ).get_text(" ", strip=True)
            if not any(s in (title+summary).lower() for s in FASI_SIGNALS):
                continue
            idf = f"FASI-{hashlib.md5(title.encode()).hexdigest()[:8]}"
            if is_duplicate(title, idf, dl_ids, titles):
                continue
            calls.append({
                "title": title, "identifier": idf,
                "description": summary[:250],
                "deadline_iso": parse_date(summary),
                "url": link or "https://fasi.eu",
                "tags": [], "source": "FASI.eu",
            })
        except Exception:
            continue
    print(f"   → {len(calls)} nuovi articoli rilevanti")
    return calls


# ══════════════════════════════════════════════════════════════════════════════
#  FONTE 3 — Fonti italiane e regionali (scraping)
# ══════════════════════════════════════════════════════════════════════════════

def scrape_source(name, url, dl_ids, titles):
    print(f"🇮🇹  {name}…")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=25)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        print(f"   ⚠  Errore: {e}"); return []

    items = []
    for sel in ["article", "li.views-row", ".item-block", ".news-item",
                ".bando-item", "div.callout", "div.result-item"]:
        items = soup.select(sel)
        if len(items) > 2:
            break
    if not items:
        items = [a.parent for a in soup.select("a[href]")
                 if len(a.get_text(strip=True)) > 20][:30]

    calls = []
    base_parsed = urlparse(url)
    for item in items[:40]:
        try:
            title_el = (item.select_one("h2, h3, h4, .title, strong")
                        or item.select_one("a"))
            link_el  = item.select_one("a[href]")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            if not (15 < len(title) < 250):
                continue
            # Escludi titoli che sono header/navigazione (blacklist esatta)
            if title.strip().lower() in TITLE_BLACKLIST:
                continue
            link = link_el["href"] if link_el else url
            if link.startswith("/"):
                link = f"{base_parsed.scheme}://{base_parsed.netloc}{link}"
            if not link.startswith("http"):
                link = url
            idf = f"{name}-{hashlib.md5(title.encode()).hexdigest()[:8]}"
            if is_duplicate(title, idf, dl_ids, titles):
                continue
            text = item.get_text(" ", strip=True)

            # Filtra: solo bandi pertinenti a ricerca/progettualità
            combined_low = (title + " " + text).lower()
            if not any(sig in combined_low for sig in RESEARCH_SIGNALS):
                continue
            if any(exc in combined_low for exc in RESEARCH_EXCLUDE):
                continue

            desc_el = item.select_one("p, .description, .abstract")
            desc = desc_el.get_text(strip=True)[:250] if desc_el else ""
            # Scarta card senza descrizione reale — qualità insufficiente
            if not desc or len(desc) < 30:
                continue
            calls.append({
                "title": title, "identifier": idf, "description": desc,
                "deadline_iso": parse_date(text),
                "url": link, "tags": [name], "source": name,
            })
        except Exception:
            continue
    print(f"   → {len(calls)} nuovi bandi")
    return calls


def fetch_all_italian(dl_ids, titles):
    calls = []
    for name, url in SOURCES_IT:
        calls += scrape_source(name, url, dl_ids, titles)
    return calls


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print(f"\n🔍  UniBA Scouting — {date.today()}")
    print("=" * 60)

    if not INDEX.exists():
        print(f"ERRORE: {INDEX} non trovato."); return 1

    html = INDEX.read_text(encoding="utf-8")
    dl_ids, titles = read_existing(html)
    print(f"📄  index.html: {len(dl_ids)} scadenze, {len(titles)} card\n")

    # Scoperta da tutte le fonti
    all_new  = fetch_eu_portal(dl_ids, titles)
    all_new += fetch_fasi_rss(dl_ids, titles)
    all_new += fetch_all_italian(dl_ids, titles)

    # De-duplica tra fonti
    seen, unique = set(), []
    for c in all_new:
        key = c["title"].lower()[:45]
        if key not in seen:
            seen.add(key); unique.append(c)

    print(f"\n✨  {len(unique)} nuovi bandi trovati")
    if not unique:
        print("   Nessun aggiornamento necessario."); return 0

    # Inietta card
    modified = html
    added = []
    for c in unique:
        card_html, section_id, dl_id = generate_card(**c)
        print(f"  ➕  [{c['source']:15}] {c['title'][:55]}…")
        modified = inject_card(modified, section_id, card_html)
        if c["deadline_iso"] and dl_id:
            modified = add_deadline_js(modified, dl_id, c["deadline_iso"])
            dl_ids.add(dl_id)
        titles.add(c["title"].lower()[:45])
        added.append(c)

    # Salva index.html
    INDEX.write_text(modified, encoding="utf-8")
    print(f"\n💾  index.html aggiornato — {len(added)} nuove card")

    # Log
    LOG_FILE.parent.mkdir(exist_ok=True)
    lines = [f"# Discovery Log — {date.today()}\n\nAggiunti **{len(added)}** bandi.\n\n",
             "| Fonte | Titolo | Scadenza |\n", "|-------|--------|----------|\n"]
    for c in added:
        lines.append(f"| {c['source']} | {c['title'][:55]} | {c['deadline_iso'] or 'TBD'} |\n")
    LOG_FILE.write_text("".join(lines), encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(main())
