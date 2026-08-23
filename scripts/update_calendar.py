#!/usr/bin/env python3
"""Daily updater for the EF sports-calendar GitHub Pages site.

Fetches:
  - Sporza's own "livestream" widget (precise: only things actually streamed live)
  - RTBF Auvio's live TV planning grid, filtered to sport categories
  - VTM GO's live TV guide (DPG Media), filtered by sport keywords in the title
  - RTL Play's live TV guide (DPG Media), filtered by sport keywords in the title

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
    "esports": "Esports", "paardensport": "Equestrian", "dressuur": "Equestrian",
    "springconcours": "Equestrian", "turnen": "Gymnastics", "volleybal": "Volleyball",
    "badminton": "Badminton", "waterpolo": "WaterPolo", "triatlon": "Triathlon",
    "zeilen": "Sailing", "boogschieten": "Archery", "roeien": "Rowing",
    "schermen": "Fencing", "schaatsen": "Skating", "basketbal (v)": "Basketball",
    "mountainbike": "MTB", "triathlon": "Triathlon", "wielrennen (v)": "Cycling",
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
    "esports": "Esports", "e-sports": "Esports", "équitation": "Equestrian",
    "jumping": "Equestrian",
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
#
# Sporza's site runs on React Router 7's "single fetch" mode: route data is no
# longer inlined as a plain JSON blob in the HTML (the old window.__remixContext
# approach) -- it's served from a `<route>.data` endpoint in the `turbo-stream`
# wire format (a flat JSON array of values where objects/arrays hold references
# -- by index -- to other array slots, so shared/repeated values aren't
# duplicated). No official Python client for this exists; decode_turbo_stream()
# below is a minimal reimplementation covering the value shapes Sporza actually
# uses (plain objects/arrays/scalars, Map). Reference: jacob-ebey/turbo-stream.

TS_SPECIAL = {-1: None, -2: float("nan"), -3: float("-inf"), -4: -0.0, -5: None, -6: float("inf"), -7: None}
TS_TYPE_TAGS = {"B", "D", "E", "M", "N", "P", "R", "S", "Y", "U", "Z"}


def decode_turbo_stream(text):
    flat = json.loads(text)
    cache = {}

    def resolve(idx):
        if idx < 0:
            return TS_SPECIAL.get(idx)
        if idx in cache:
            return cache[idx]
        raw = flat[idx]
        if isinstance(raw, dict):
            result = {}
            cache[idx] = result
            for k, v_idx in raw.items():
                result[resolve(int(k[1:]))] = resolve(v_idx)
            return result
        if isinstance(raw, list):
            if raw and isinstance(raw[0], str) and raw[0] in TS_TYPE_TAGS:
                tag = raw[0]
                if tag == "M":
                    result = {}
                    cache[idx] = result
                    for i in range(1, len(raw), 2):
                        result[resolve(raw[i])] = resolve(raw[i + 1])
                    return result
                if tag == "S":
                    result = [resolve(i) for i in raw[1:]]
                    cache[idx] = result
                    return result
                if tag in ("P", "Z"):
                    return resolve(raw[1]) if len(raw) > 1 else None
                return raw  # D/R/U/B/Y/E/N -- not needed for our data
            result = []
            cache[idx] = result
            for el_idx in raw:
                result.append(resolve(el_idx))
            return result
        return raw  # literal scalar

    return resolve(0)


def fetch_sporza_livestreams():
    """Return list of {date, t, lbl, sport, plat} from Sporza's own live widget."""
    try:
        raw = fetch("https://sporza.be/nl/kalender.data", headers={"Accept": "text/x-script"})
        decoded = decode_turbo_stream(raw)
        items = decoded["routes/nl.kalender"]["data"]["page"]["header"][0]["componentProps"]["items"]
    except Exception as e:
        WARNINGS.append(f"Sporza fetch/decode failed: {e} -- site may have changed again")
        return []

    today = date.today()
    out = []
    for it in items:
        props = it.get("componentProps", {})
        title = props.get("title", "").strip()
        subtitle = props.get("subtitle", "")
        btn = props.get("button") or {}
        time_text = (props.get("time") or {}).get("text", "")
        if not title:
            continue

        nl_sport = subtitle.split("|")[0].strip().lower() if subtitle else ""
        sport = NL_SPORT_MAP.get(nl_sport) or (nl_sport.title() if nl_sport else "Other")

        if btn.get("status") == "LIVE" or not time_text:
            # currently airing right now -- the widget only gives a completion
            # %, not a start/end time, at this point
            d, t = today, "Live now"
        else:
            d = parse_dutch_date_label(btn.get("title", ""), today)
            if d is None:
                WARNINGS.append(f"Sporza: could not parse date label '{btn.get('title')}' for '{title}' -- skipped")
                continue
            t = time_text or "TBC"

        out.append({"date": d.isoformat(), "t": t, "lbl": title, "sport": sport, "plat": ["vrt"]})
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
    auto_named = []
    skipped_non_sport = []
    for it in items:
        cat = (it.get("category") or {}).get("label", "")
        cat_key = cat.strip().lower()
        title = it.get("title", "").strip()
        sport = FR_SPORT_MAP.get(cat_key)
        if sport is None and cat_key in ("sport", "sports"):
            # RTBF files almost everything sport-related under one generic "Sport"
            # bucket rather than per-sport categories -- the actual sport name is
            # the lead-in of the title instead, e.g. "Athlétisme - Euro Birmingham 2026".
            # RTBF already decided it belongs under "Sport": trust that and include it,
            # using a known nice name where we have one, or a name derived from the
            # title itself otherwise -- but only when that derived name still looks
            # like a sport (short, no punctuation): the "Sport" bucket also carries
            # magazine shows / documentaries ABOUT a sport (e.g. "The Red Lions, a
            # better place"), and blindly title-casing those made a garbage
            # "sport" category (broke the CSS, since sport names feed unescaped
            # into a class selector -- a comma splits it into multiple invalid
            # selectors) instead of just skipping a non-live item.
            title_lower = title.lower()
            sport = next((v for k, v in FR_SPORT_MAP.items() if k in title_lower), None)
            if sport is None:
                # sport is used directly as a CSS class and a data-f attribute value
                # elsewhere, so it must be a single clean word -- not just "short":
                # a space or comma in there produces an invalid/broken selector.
                prefix = re.split(r"[-:]", title, maxsplit=1)[0].strip()
                words = prefix.split()
                if len(words) == 1 and 2 <= len(words[0]) <= 16 and words[0].isalpha():
                    sport = words[0].title()
                    auto_named.append((title, sport))
                elif prefix:
                    skipped_non_sport.append(title)
        if sport is None:
            continue  # category isn't sport-related, or looked like a non-sport show
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

    if auto_named:
        WARNINGS.append(
            f"RTBF: {len(auto_named)} item(s) under generic 'Sport' category had no FR_SPORT_MAP "
            f"entry, named from their title instead (added anyway; consider adding a proper "
            f"mapping): {sorted(set(s for _, s in auto_named))}"
        )
    if skipped_non_sport:
        WARNINGS.append(
            f"RTBF: {len(skipped_non_sport)} item(s) under generic 'Sport' category skipped -- "
            f"title didn't look like a sport name (likely a magazine/documentary show, not a "
            f"live broadcast): {sorted(set(skipped_non_sport))[:5]}"
        )
    return out


# ---------- DPG Media (VTM GO / RTL Play share the same backend) ----------
#
# Both apps hit lfvp-api.dpgmedia.net behind a WAF that rejects generic/unbranded
# requests -- it needs headers that plausibly look like the real mobile app,
# including a current-ish x-app-version (an old one gets HTTP 426 "upgrade
# required", not blocked outright). No login/token needed for the /live guide.
# Unlike RTBF there's no genre tag at all here -- every program on every channel
# comes back, so classification is keyword-matching the program title against
# NL_SPORT_MAP / FR_SPORT_MAP (only kept when a real match is found -- no
# generic "Other" fallback here, since without a category to lean on, guessing
# on arbitrary titles would let regular non-sport programs slip through).

VTM_HEADERS = {
    "User-Agent": "VTM_GO/25.260415 (be.vmma.vtm.zenderapp; build:30644; Android 30) okhttp/4.11.0",
    "x-app-version": "25",
    "x-persgroep-mobile-app": "true",
    "x-persgroep-os": "android",
    "x-persgroep-os-version": "30",
}
RTL_HEADERS = {
    "User-Agent": "RTL_PLAY/25.260415 (com.tapptic.rtl.tvi; build:30644; Android 30)",
    "Accept": "*/*",
    "lfvp-device-segment": "TV>Android",
    "x-app-version": "25",
}


def fetch_dpg_live(mode, headers, sport_map, plat_tag, source_label):
    """Shared fetcher for DPG Media's '/{mode}/live' TV guide (VTM_GO or RTL_PLAY)."""
    try:
        raw = fetch(f"https://lfvp-api.dpgmedia.net/{mode}/live", headers=headers)
        data = json.loads(raw)
    except Exception as e:
        WARNINGS.append(f"{source_label} fetch failed: {e}")
        return []

    out = []
    for chan in data.get("channels", []):
        for b in chan.get("broadcasts", []):
            name = (b.get("name") or "").strip()
            if not name or name.lower() in ("geen uitzending", "pas d'émission"):
                continue  # "off air" placeholder slot
            name_lower = name.lower()
            sport = next((v for k, v in sport_map.items() if k in name_lower), None)
            if sport is None:
                continue  # no recognisable sport keyword -- most programs aren't sport
            start = b.get("startsAt", "")
            end = b.get("endsAt", "")
            if not start:
                continue
            d, t_start = start[:10], start[11:16]
            t_end = end[11:16] if end else ""
            t = f"{t_start} - {t_end}" if t_end and t_end != t_start else t_start
            episode = (b.get("episodeTitle") or "").strip()
            lbl = f"{name} · {episode}" if episode and episode not in name else name
            out.append({"date": d, "t": t, "lbl": lbl, "sport": sport, "plat": [plat_tag]})
    return out


def fetch_vtm_events():
    return fetch_dpg_live("VTM_GO", VTM_HEADERS, NL_SPORT_MAP, "vtm", "VTM GO")


def fetch_rtl_events():
    return fetch_dpg_live("RTL_PLAY", RTL_HEADERS, FR_SPORT_MAP, "rtl", "RTL Play")


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


def merge_sources(*event_lists):
    """Fold any number of source event lists into one, unioning platforms for
    broadcasts that same_broadcast() considers the same across sources."""
    merged = []
    for events in event_lists:
        for e in events:
            existing = next((m for m in merged if same_broadcast(m, e)), None)
            if existing is None:
                merged.append(dict(e))
            else:
                existing["plat"] = sorted(set(existing["plat"]) | set(e["plat"]))
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
    vtm_events = fetch_vtm_events()
    rtl_events = fetch_rtl_events()
    log(f"Sporza livestream candidates: {len(sporza_events)}")
    log(f"RTBF Auvio sport candidates: {len(auvio_events)}")
    log(f"VTM GO sport candidates: {len(vtm_events)}")
    log(f"RTL Play sport candidates: {len(rtl_events)}")

    if not any([sporza_events, auvio_events, vtm_events, rtl_events]) and WARNINGS:
        log("All sources failed -- aborting without touching index.html")
        for w in WARNINGS:
            log(f"  - {w}")
        sys.exit(1)

    merged = merge_sources(sporza_events, auvio_events, vtm_events, rtl_events)

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
