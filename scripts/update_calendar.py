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
    ("#fde0e0", "#d32f2f", "#7a1414"),  # Gymnastics - red-pink
    ("#e0fff2", "#00c896", "#006644"),  # Rowing - mint
    ("#f7e0ff", "#a300cc", "#570070"),  # Judo - magenta-purple
    ("#fff0f7", "#e91e8c", "#8a0c52"),  # Fencing - pink
    ("#e0f0ff", "#1565c0", "#0b3a70"),  # Sailing - deep blue
    ("#f2f0e0", "#9e8b1f", "#544a10"),  # Biathlon - khaki
    ("#e0f7ff", "#0288a8", "#014b5c"),  # Skating - light teal
    ("#ffe0ea", "#c2185b", "#6b0d33"),  # Triathlon - rose
    ("#eef4ff", "#5c7cfa", "#2a3d8a"),  # Skiing - periwinkle
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
    "gymnastique": "Gymnastics", "aviron": "Rowing", "judo": "Judo",
    "escrime": "Fencing", "voile": "Sailing", "biathlon": "Biathlon",
    "patinage": "Skating", "triathlon": "Triathlon", "ski": "Skiing",
}

DUTCH_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "augustus": 8, "september": 9, "oktober": 10, "november": 11, "december": 12,
}

WARNINGS = []


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
        sport = NL_SPORT_MAP.get(nl_sport) or (nl_sport.title() if nl_sport else "Other")

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
    unmatched_titles = []
    for it in items:
        cat = (it.get("category") or {}).get("label", "")
        cat_key = cat.strip().lower()
        title = it.get("title", "").strip()
        sport = FR_SPORT_MAP.get(cat_key)
        if sport is None and cat_key in ("sport", "sports"):
            # RTBF files almost everything sport-related under one generic "Sport"
            # bucket rather than per-sport categories -- the actual sport name is
            # the lead-in of the title instead, e.g. "Athlétisme - Euro Birmingham 2026".
            prefix = re.split(r"[-:]", title, maxsplit=1)[0].strip().lower()
            sport = FR_SPORT_MAP.get(prefix)
            if sport is None and prefix:
                unmatched_titles.append(title)
        if sport is None:
            continue  # not a recognised sport -- conservative, no guessing
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

    if unmatched_titles:
        WARNINGS.append(
            f"RTBF: {len(unmatched_titles)} item(s) under generic 'Sport' category with an "
            f"unrecognised sport name in the title (not added, needs a FR_SPORT_MAP entry): "
            f"{sorted(set(unmatched_titles))[:5]}"
        )
    return out


# ---------- merge ----------
#
# Same date + same sport does NOT mean same broadcast: a championship routinely
# has a morning session and a separate evening session for the same sport on the
# same day (e.g. athletics heats at 11:45, finals at 20:15) and both deserve their
# own row. So "is this the same event" is decided by date + sport + start time
# being close together, not by date + sport alone, and not by label (labels from
# different sources for the same broadcast rarely match verbatim: Sporza's NL
# "EK atletiek" vs. Auvio's FR "Athlétisme - Euro Birmingham 2026").

SAME_EVENT_TOLERANCE_MIN = 60


def start_minutes(t):
    """Leading 'HH:MM' of a time string -> minutes since midnight, else None."""
    m = re.match(r"(\d{1,2}):(\d{2})", t or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def same_broadcast(a, b):
    if a["date"] != b["date"] or a["sport"] != b["sport"]:
        return False
    sa, sb = start_minutes(a["t"]), start_minutes(b["t"])
    if sa is None or sb is None:
        return False  # can't confirm they're the same slot -- treat as distinct, not a guess
    return abs(sa - sb) <= SAME_EVENT_TOLERANCE_MIN


def merge_sources(sporza_events, auvio_events):
    merged = list(sporza_events)
    used_auvio = set()
    for i, sp in enumerate(sporza_events):
        for j, av in enumerate(auvio_events):
            if j in used_auvio:
                continue
            if same_broadcast(sp, av):
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


def add_new_sport_styling(html, sports_in_play):
    """Ensure every sport currently in play has a CSS rule + filter button.
    Returns (html, list of sports newly styled by this call)."""
    unstyled = [s for s in sorted(sports_in_play) if not re.search(rf"\.s-{re.escape(s)}\{{", html)]
    if not unstyled:
        return html, []

    used_colors = set(re.findall(r"\.s-[A-Za-z]+\{[^}]*border-color:(#[0-9a-fA-F]{6})", html))
    palette = [p for p in EXTRA_PALETTE if p[1] not in used_colors] or EXTRA_PALETTE

    css_lines = []
    btn_lines = []
    for i, sport in enumerate(unstyled):
        bg, border, text = palette[i % len(palette)]
        css_lines.append(f"  .s-{sport}{{background:{bg};border-color:{border};color:{text}}}")
        btn_lines.append(f'    <button data-f="{sport}">{sport}</button>')

    html = html.replace(
        "  .s-Darts{background:#e5f6ec;border-color:#12a150;color:#0a5c2e}",
        "  .s-Darts{background:#e5f6ec;border-color:#12a150;color:#0a5c2e}\n" + "\n".join(css_lines),
        1,
    )
    html = html.replace(
        '    <button data-f="Darts">Darts</button>',
        '    <button data-f="Darts">Darts</button>\n' + "\n".join(btn_lines),
        1,
    )
    return html, unstyled


def main():
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    hand_curated_keys = extract_hand_curated_keys(html)
    today = date.today()

    # carry forward previous auto-added events that are still upcoming; drop past ones
    # (they're inert anyway once MONTHS rolls past them, no need to keep bloating the file).
    # Kept as a list, not keyed by label: a championship can run a morning session and
    # an evening session for the same sport on the same day, and they must both survive
    # as separate rows -- see same_broadcast().
    auto_events = [e for e in extract_auto_events(html) if e["date"] >= today.isoformat()]

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
        lbl_key = (e["date"], e["sport"], normalize_lbl(e["lbl"]))
        if lbl_key in hand_curated_keys:
            continue  # a human already curates this exact event -- never touch it
        existing = next((x for x in auto_events if same_broadcast(x, e)), None)
        if existing is None:
            auto_events.append(e)
            added += 1
        else:
            new_plat = sorted(set(existing["plat"]) | set(e["plat"]))
            if new_plat != existing["plat"]:
                existing["plat"] = new_plat
                upgraded += 1

    months = rolling_months(today)
    html = replace_months(html, months)

    # style/filter-button any sport in play that isn't styled yet -- based on what's
    # actually in the file, not on whether the fetch layer happened to guess it as
    # "new": a sport we pre-mapped (e.g. Swimming) is just as unstyled on first use
    # as one we fell back to title-casing for.
    sports_in_play = {e["sport"] for e in auto_events}
    html, newly_styled = add_new_sport_styling(html, sports_in_play)

    auto_block = build_auto_block(auto_events)
    html = replace_auto_block(html, auto_block)

    with open(INDEX_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)

    log(f"New events added: {added}  (platform upgrades on existing: {upgraded})")
    if newly_styled:
        log(f"New sport categories styled: {newly_styled}")
    if WARNINGS:
        log("Warnings:")
        for w in WARNINGS:
            log(f"  - {w}")

    print(f"SUMMARY: {added} new event(s), {upgraded} platform upgrade(s)"
          + (f", new sports styled: {newly_styled}" if newly_styled else "")
          + (f", {len(WARNINGS)} warning(s)" if WARNINGS else ""))


if __name__ == "__main__":
    main()
