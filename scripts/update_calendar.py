#!/usr/bin/env python3
"""Daily updater for the EF sports-calendar GitHub Pages site.

Fetches:
  - Sporza's own "livestream" widget (precise: only things actually streamed live)
  - RTBF Auvio's live TV planning grid, filtered to sport categories

Adds genuinely new events into the AUTO-DETECTED block of index.html,
rewrites the rolling MONTHS window, and auto-creates CSS/filter entries
for any sport not seen before. Never touches the hand-curated section
above the AUTO-DETECTED marker.
"""
import json
import re
import sys
import urllib.request
import urllib.error
from datetime import date, timedelta

UA = "Mozilla/5.0 (compatible; ef-sports-calendar-bot/1.0; +https://github.com/regis-bovy/sports-calendar)"
INDEX_PATH = "index.html"

AUTO_START = "// --- AUTO-DETECTED (daily bot — do not hand-edit below this line) ---"
AUTO_END = "// --- end auto-detected ---"

KNOWN_SPORTS = ["F1", "Cycling", "Cyclocross", "Football", "Hockey", "Athletics", "Darts"]

# extra colors for sports that show up later (border, bg tint, text) -- picked to stay
# visually distinct from the 7 already in use and from each other.
EXTRA_PALETTE = [
    ("#fff7d6", "#c9a400", "#6b5800"),  # Tennis - yellow
    ("#e9f7e0", "#4caf00", "#2b5c00"),  # Golf - green
    ("#f0e6ff", "#6a3bd6", "#3a1f78"),  # Basketball - purple
    ("#ffe9e0", "#ff5722", "#8a2c0e"),  # Rugby - deep orange
    ("#e0fbff", "#00b8c4", "#00636b"),  # Swimming - teal
    ("#f5e0ee", "#c2185b", "#6b0d33"),  # Boxing - crimson
    ("#eef0e0", "#8d9c1f", "#4a5410"),  # MotoGP - olive
    ("#e0e8ff", "#3d5afe", "#1b2b8a"),  # Handball - indigo
]

NL_SPORT_MAP = {
    "wielrennen": "Cycling", "veldrijden": "Cyclocross", "voetbal": "Football",
    "tennis": "Tennis", "atletiek": "Athletics", "hockey": "Hockey",
    "darts": "Darts", "formule 1": "F1", "f1": "F1", "golf": "Golf",
    "basketbal": "Basketball", "rugby": "Rugby", "zwemmen": "Swimming",
    "boksen": "Boxing", "motorsport": "MotoGP", "handbal": "Handball",
}
FR_SPORT_MAP = {
    "cyclisme": "Cycling", "cyclo-cross": "Cyclocross", "football": "Football",
    "tennis": "Tennis", "athlétisme": "Athletics", "hockey": "Hockey",
    "fléchettes": "Darts", "formule 1": "F1", "sports mécaniques": "F1",
    "golf": "Golf", "basket": "Basketball", "basketball": "Basketball",
    "rugby": "Rugby", "natation": "Swimming", "boxe": "Boxing", "handball": "Handball",
}

DUTCH_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "augustus": 8, "september": 9, "oktober": 10, "november": 11, "december": 12,
}

WARNINGS = []
NEW_SPORTS_SEEN = set()


def log(msg):
    print(msg, file=sys.stderr)


def fetch(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8")


# ---------- Sporza ----------

def fetch_sporza_livestreams():
    """Return list of {date, t, lbl, sport, plat} from Sporza's own live widget."""
    try:
        html = fetch("https://sporza.be/nl/kalender")
    except Exception as e:
        WARNINGS.append(f"Sporza fetch failed: {e}")
        return []

    m = re.search(r"window\.__remixContext\s*=\s*(\{.*?\});", html, re.S)
    if not m:
        WARNINGS.append("Sporza: could not find __remixContext in page HTML (site may have changed)")
        return []
    try:
        ctx = json.loads(m.group(1))
        items = ctx["state"]["loaderData"]["routes/nl.kalender"]["page"]["header"][0]["componentProps"]["items"]
    except Exception as e:
        WARNINGS.append(f"Sporza: unexpected data shape ({e}) -- site may have changed")
        return []

    today = date.today()
    out = []
    for it in items:
        props = it.get("componentProps", {})
        title = props.get("title", "").strip()
        subtitle = props.get("subtitle", "")
        time_text = (props.get("time") or {}).get("text", "")
        btn_title = (props.get("button") or {}).get("title", "")
        if not title:
            continue

        nl_sport = subtitle.split("|")[0].strip().lower() if subtitle else ""
        sport = NL_SPORT_MAP.get(nl_sport)
        if sport is None:
            sport = nl_sport.title() if nl_sport else "Other"
            if sport != "Other":
                NEW_SPORTS_SEEN.add(sport)

        d = parse_dutch_date_label(btn_title, today)
        if d is None:
            WARNINGS.append(f"Sporza: could not parse date label '{btn_title}' for '{title}' -- skipped")
            continue

        out.append({"date": d.isoformat(), "t": time_text or "TBC", "lbl": title, "sport": sport, "plat": ["vrt"]})
    return out


def parse_dutch_date_label(label, today):
    label = label.lower().strip()
    if label.startswith("vandaag"):
        return today
    if label.startswith("morgen"):
        return today + timedelta(days=1)
    m = re.search(r"(\d{1,2})\s+([a-z]+)", label)
    if not m:
        return None
    day = int(m.group(1))
    month = DUTCH_MONTHS.get(m.group(2))
    if month is None:
        return None
    year = today.year
    candidate = date(year, month, day)
    # handle year rollover (e.g. run in December, event label says January)
    if candidate < today - timedelta(days=3):
        candidate = date(year + 1, month, day)
    return candidate


# ---------- RTBF Auvio ----------

def fetch_auvio_sport_events():
    """Return list of {date, t, lbl, sport, plat} from RTBF's live planning grid (~48h window)."""
    partner_key = "82ed2c5b7df0a9334dfbda21eccd8427"
    base = ("https://www.rtbf.be/api/partner/generic/live/planninglist"
            "?target_site=media&origin_site=media&category_id=0&start_date="
            f"&offset={{offset}}&limit=100&partner_key={partner_key}&v=8")

    items = []
    for offset in (0, 100):
        try:
            raw = fetch(base.format(offset=offset))
            items.extend(json.loads(raw))
        except Exception as e:
            WARNINGS.append(f"RTBF Auvio fetch (offset {offset}) failed: {e}")
            break

    out = []
    for it in items:
        cat = (it.get("category") or {}).get("label", "")
        cat_key = cat.strip().lower()
        sport = FR_SPORT_MAP.get(cat_key)
        if sport is None:
            continue  # not a recognised sport category -- conservative, no guessing
        start = it.get("start_date", "")
        end = it.get("end_date", "")
        if not start:
            continue
        d, t_start = start[:10], start[11:16]
        t_end = end[11:16] if end else ""
        t = f"{t_start} - {t_end}" if t_end and t_end != t_start else t_start
        title = it.get("title", "").strip()
        subtitle = it.get("subtitle", "").strip()
        lbl = f"{title} · {subtitle}" if subtitle and subtitle not in title else title
        if not lbl:
            continue
        out.append({"date": d, "t": t, "lbl": lbl, "sport": sport, "plat": ["auvio"]})
    return out


# ---------- merge ----------

def merge_sources(sporza_events, auvio_events):
    merged = list(sporza_events)
    used_auvio = set()
    for i, sp in enumerate(sporza_events):
        for j, av in enumerate(auvio_events):
            if j in used_auvio:
                continue
            if sp["date"] == av["date"] and sp["sport"] == av["sport"]:
                merged[i]["plat"] = sorted(set(merged[i]["plat"]) | set(av["plat"]))
                used_auvio.add(j)
                break
    for j, av in enumerate(auvio_events):
        if j not in used_auvio:
            merged.append(av)
    return merged


# ---------- index.html parsing / rewriting ----------

def normalize_lbl(lbl):
    return re.sub(r"\s+", " ", lbl).strip().lower()


def _add_call_keys(html_fragment):
    keys = set()

    for m in re.finditer(
        r"add\('(\d{4}-\d{2}-\d{2})',\{t:'[^']*',lbl:'((?:[^'\\]|\\.)*)',sport:'([^']*)'", html_fragment
    ):
        d, lbl, sport = m.group(1), m.group(2), m.group(3)
        keys.add((d, sport, normalize_lbl(lbl)))

    for m in re.finditer(
        r"addRange\('(\d{4}-\d{2}-\d{2})','(\d{4}-\d{2}-\d{2})',"
        r"\{t:'[^']*',lbl:'((?:[^'\\]|\\.)*)',sport:'([^']*)'[^}]*\}(?:,\[([^\]]*)\])?\)",
        html_fragment,
    ):
        start, end, lbl, sport, skip_raw = m.groups()
        skip = set(re.findall(r"'(\d{4}-\d{2}-\d{2})'", skip_raw or ""))
        cur = date.fromisoformat(start)
        stop = date.fromisoformat(end)
        while cur <= stop:
            ds = cur.isoformat()
            if ds not in skip:
                keys.add((ds, sport, normalize_lbl(lbl)))
            cur += timedelta(days=1)

    return keys


def extract_hand_curated_keys(html):
    """(date, sport, normalized lbl) triples from the hand-written section only
    (everything above the AUTO-DETECTED marker) -- these are never touched or re-added."""
    idx = html.find(AUTO_START)
    fragment = html if idx == -1 else html[:idx]
    return _add_call_keys(fragment)


def extract_auto_events(html):
    """Structured events currently in the AUTO-DETECTED block (empty if not present yet)."""
    m = re.search(re.escape(AUTO_START) + r"(.*?)" + re.escape(AUTO_END), html, re.S)
    if not m:
        return []
    fragment = m.group(1)
    out = []
    for em in re.finditer(
        r"add\('(\d{4}-\d{2}-\d{2})',\{t:'([^']*)',lbl:'((?:[^'\\]|\\.)*)',sport:'([^']*)',"
        r"plat:\[([^\]]*)\]\}\);",
        fragment,
    ):
        d, t, lbl, sport, plat_raw = em.groups()
        plat = re.findall(r"'([^']*)'", plat_raw)
        out.append({"date": d, "t": t, "lbl": lbl.replace("\\'", "'"), "sport": sport, "plat": plat})
    return out


def js_escape(s):
    return s.replace("\\", "\\\\").replace("'", "\\'")


def build_auto_block(events):
    lines = [AUTO_START]
    for e in sorted(events, key=lambda e: (e["date"], e["sport"])):
        plat = ",".join(f"'{p}'" for p in e["plat"])
        lines.append(
            f"add('{e['date']}',{{t:'{js_escape(e['t'])}',lbl:'{js_escape(e['lbl'])}',"
            f"sport:'{e['sport']}',plat:[{plat}]}});"
        )
    lines.append(AUTO_END)
    return "\n".join(lines)


def replace_auto_block(html, new_block):
    pattern = re.compile(re.escape(AUTO_START) + r".*?" + re.escape(AUTO_END), re.S)
    if pattern.search(html):
        return pattern.sub(new_block.replace("\\", "\\\\"), html, count=1)
    # first run: insert right before the render section marker
    marker = "// ---- Render ----"
    idx = html.index(marker)
    return html[:idx] + new_block + "\n\n" + html[idx:]


def rolling_months(today, count=5):
    names = ["January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"]
    out = []
    y, m0 = today.year, today.month - 1  # 0-indexed month like the JS
    for i in range(count):
        mm = (m0 + i) % 12
        yy = y + (m0 + i) // 12
        out.append({"name": f"{names[mm]} {yy}", "y": yy, "m": mm})
    return out


def build_months_js(months):
    rows = "\n".join(
        f"  {{name:'{mo['name']}',y:{mo['y']},m:{mo['m']}}}," for mo in months
    )
    return "const MONTHS=[\n" + rows + "\n];"


def replace_months(html, months):
    pattern = re.compile(r"const MONTHS=\[.*?\];", re.S)
    return pattern.sub(build_months_js(months).replace("\\", "\\\\"), html, count=1)


def add_new_sport_styling(html, new_sports):
    if not new_sports:
        return html
    used_colors = set(re.findall(r"\.s-[A-Za-z]+\{[^}]*border-color:(#[0-9a-fA-F]{6})", html))
    palette = [p for p in EXTRA_PALETTE if p[1] not in used_colors]

    css_lines = []
    btn_lines = []
    for i, sport in enumerate(sorted(new_sports)):
        if re.search(rf"\.s-{re.escape(sport)}\{{", html):
            continue  # already styled (e.g. added in a previous run)
        bg, border, text = palette[i % len(palette)]
        css_lines.append(f"  .s-{sport}{{background:{bg};border-color:{border};color:{text}}}")
        btn_lines.append(f'    <button data-f="{sport}">{sport}</button>')

    if css_lines:
        html = html.replace(
            "  .s-Darts{background:#e5f6ec;border-color:#12a150;color:#0a5c2e}",
            "  .s-Darts{background:#e5f6ec;border-color:#12a150;color:#0a5c2e}\n" + "\n".join(css_lines),
            1,
        )
    if btn_lines:
        html = html.replace(
            '    <button data-f="Darts">Darts</button>',
            '    <button data-f="Darts">Darts</button>\n' + "\n".join(btn_lines),
            1,
        )
    return html


def main():
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    hand_curated_keys = extract_hand_curated_keys(html)
    today = date.today()

    # carry forward previous auto-added events that are still upcoming; drop past ones
    # (they're inert anyway once MONTHS rolls past them, no need to keep bloating the file)
    auto_events_by_key = {}
    for e in extract_auto_events(html):
        if e["date"] >= today.isoformat():
            auto_events_by_key[(e["date"], e["sport"], normalize_lbl(e["lbl"]))] = e

    sporza_events = fetch_sporza_livestreams()
    auvio_events = fetch_auvio_sport_events()
    log(f"Sporza livestream candidates: {len(sporza_events)}")
    log(f"RTBF Auvio sport candidates: {len(auvio_events)}")

    if not sporza_events and not auvio_events and WARNINGS:
        log("Both sources failed -- aborting without touching index.html")
        for w in WARNINGS:
            log(f"  - {w}")
        sys.exit(1)

    merged = merge_sources(sporza_events, auvio_events)

    added, upgraded = 0, 0
    for e in merged:
        key = (e["date"], e["sport"], normalize_lbl(e["lbl"]))
        if key in hand_curated_keys:
            continue  # a human already curates this exact event -- never touch it
        existing = auto_events_by_key.get(key)
        if existing is None:
            auto_events_by_key[key] = e
            added += 1
        else:
            new_plat = sorted(set(existing["plat"]) | set(e["plat"]))
            if new_plat != existing["plat"]:
                existing["plat"] = new_plat
                upgraded += 1

    months = rolling_months(today)
    html = replace_months(html, months)

    html = add_new_sport_styling(html, NEW_SPORTS_SEEN)

    auto_block = build_auto_block(list(auto_events_by_key.values()))
    html = replace_auto_block(html, auto_block)

    with open(INDEX_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)

    log(f"New events added: {added}  (platform upgrades on existing: {upgraded})")
    if NEW_SPORTS_SEEN:
        log(f"New sport categories introduced: {sorted(NEW_SPORTS_SEEN)}")
    if WARNINGS:
        log("Warnings:")
        for w in WARNINGS:
            log(f"  - {w}")

    print(f"SUMMARY: {added} new event(s), {upgraded} platform upgrade(s)"
          + (f", new sports: {sorted(NEW_SPORTS_SEEN)}" if NEW_SPORTS_SEEN else "")
          + (f", {len(WARNINGS)} warning(s)" if WARNINGS else ""))


if __name__ == "__main__":
    main()
