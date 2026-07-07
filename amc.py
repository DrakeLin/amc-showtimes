#!/usr/bin/env python3
"""Shared AMC Theatres / Letterboxd helpers for the showtimes website."""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

# -- Configuration -------------------------------------------------------------
def _parse_theatres(spec):
    """Parse the AMC_THEATRES env var ("Name:id,Name:id") into {name: id}.
    Malformed entries are skipped; an empty/invalid spec returns {} so the
    caller can fall back to the defaults."""
    theatres = {}
    for part in spec.split(","):
        name, _, tid = part.rpartition(":")
        name, tid = name.strip(), tid.strip()
        if name and tid.isdigit():
            theatres[name] = int(tid)
    return theatres


# Override with e.g. AMC_THEATRES="AMC Empire 25:375,AMC Lincoln Square 13:2206"
# (find theatre ids in the URL slugs on amctheatres.com). Defaults to SF.
THEATRES = _parse_theatres(os.environ.get("AMC_THEATRES", "")) or {
    "AMC Metreon 16": 2325,
    "AMC Kabuki 8": 4145,
}
THEATRE_SHORT = {"AMC Kabuki 8": "Kabuki", "AMC Metreon 16": "Metreon"}


def theatre_short(name):
    """Compact display name: explicit mapping first, else strip the "AMC "
    prefix and trailing screen count ("AMC Empire 25" -> "Empire")."""
    if name in THEATRE_SHORT:
        return THEATRE_SHORT[name]
    stripped = re.sub(r"\s+\d+$", "", re.sub(r"^AMC\s+", "", name)).strip()
    return stripped or name

AMC_BASE  = "https://api.amctheatres.com/v2"
LB_BASE   = "https://letterboxd.com"
PAGE_SIZE = 100
MAX_PAGES = 20

VENDOR_KEY = os.environ.get("AMC_VENDOR_KEY", "")

_LB_RATING_RE = re.compile(
    r'<meta[^>]+name="twitter:data2"[^>]+content="([\d.]+)\s*out of',
    re.IGNORECASE,
)
_LB_LD_RE = re.compile(r'"ratingValue"\s*:\s*"?([\d.]+)"?')
_FORMAT_STRIP_RE = re.compile(
    r"^\s*(IMAX|DOLBY|PRIME|PLF|3D|4DX|DBOX|SCREENX|RPX)[\s:]+",
    re.IGNORECASE,
)

# -- Marketing-title cleanup ----------------------------------------------------
# AMC listing titles are full of format tags, event marketing, and re-release
# tags that Letterboxd doesn't know about. These regexes are intentionally
# conservative (anchored to start/end or specific known phrases) so we don't
# accidentally mangle a real film title.
_FORMAT_TOKEN_RE = re.compile(
    r"(IMAX(?:\s*3D)?|DOLBY(?:\s*CINEMA)?|DOLBY\s*ATMOS|PRIME|PLF|3D|4DX|DBOX|D-BOX|SCREENX|RPX)",
    re.IGNORECASE,
)
_LEADING_FORMAT_RE = re.compile(rf"^\s*{_FORMAT_TOKEN_RE.pattern}[\s:-]+", re.IGNORECASE)
_TRAILING_FORMAT_RE = re.compile(rf"[\s:-]+{_FORMAT_TOKEN_RE.pattern}\s*$", re.IGNORECASE)

_EVENT_PHRASE_RE = re.compile(
    r"\b("
    r"opening night fan event|fan event|early access|"
    r"w/\s*bonus footage|bonus footage|"
    r"(?:[\w'’]+\s+){0,3}edition"
    r")\b",
    re.IGNORECASE,
)

# Suffixes introduced after " - " (or " – ") that are marketing/re-release
# tags rather than part of the title, e.g.
#   "My Neighbor Totoro - Studio Ghibli Fest 2026"
#   "Citizen Kane 85th Anniversary" (no dash, handled separately below)
_DASH_SUFFIX_RE = re.compile(
    r"\s+[-–]\s*("
    r"studio ghibli fest\s*\d{0,4}|"
    r"\d+(?:st|nd|rd|th)\s+anniversary|"
    r"anniversary(?:\s+(?:edition|screening|celebration))?|"
    r"remastered|restored|re-?release|director'?s cut"
    r")\s*$",
    re.IGNORECASE,
)

# Same tags when they appear without a leading dash, e.g. "Citizen Kane 85th Anniversary".
_TRAILING_TAG_RE = re.compile(
    r"\s+("
    r"\d+(?:st|nd|rd|th)\s+anniversary(?:\s+edition)?|"
    r"anniversary(?:\s+(?:edition|screening|celebration))?|"
    r"\(?\d{4}\)?\s*re-?release|"
    r"remastered|restored"
    r")\s*$",
    re.IGNORECASE,
)

# Bare trailing 4-digit year, e.g. "Moana 2026" -> "Moana" (only strip if it
# looks like a re-release/event year tag, not part of the actual title).
_TRAILING_YEAR_RE = re.compile(r"\s+\(?(19|20)\d{2}\)?\s*$")


def clean_title(title):
    """Strip AMC marketing cruft (format tags, event phrases, anniversary /
    re-release suffixes) from a listing title before slugging/searching it
    against Letterboxd. Conservative by design -- prefers to leave text alone
    over risking mangling a legitimate title."""
    t = title.strip()

    # Event phrases can appear anywhere in the string.
    t = _EVENT_PHRASE_RE.sub("", t)

    # Iteratively strip leading/trailing format tokens (titles can carry more
    # than one, e.g. "IMAX 3D ...").
    for _ in range(3):
        new_t = _LEADING_FORMAT_RE.sub("", t)
        new_t = _TRAILING_FORMAT_RE.sub("", new_t)
        if new_t == t:
            break
        t = new_t

    # " - <marketing tag>" suffixes.
    t = _DASH_SUFFIX_RE.sub("", t)
    # Same tags without a dash.
    t = _TRAILING_TAG_RE.sub("", t)
    # Bare trailing re-release year.
    t = _TRAILING_YEAR_RE.sub("", t)

    # Collapse leftover punctuation/whitespace from removed phrases.
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"[\s:-]+$", "", t)
    t = re.sub(r"^[\s:-]+", "", t)

    return t.strip() or title.strip()


_STOPWORDS = {"the", "a", "an", "of", "and", "&"}


def _title_tokens(title):
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {w for w in words if w not in _STOPWORDS}


def _titles_plausibly_match(query_title, candidate_title):
    """Sanity check a search hit: does the candidate look like the same film
    as what we searched for? Uses normalized token overlap / startswith so we
    don't return a wrong, unrelated film (e.g. an old "Obsession" for a new
    one)."""
    if not candidate_title:
        return False
    q_norm = lb_slug(query_title).replace("-", " ").strip()
    c_norm = lb_slug(candidate_title).replace("-", " ").strip()
    if not q_norm or not c_norm:
        return False
    if q_norm == c_norm or c_norm.startswith(q_norm) or q_norm.startswith(c_norm):
        return True

    q_tokens = _title_tokens(query_title)
    c_tokens = _title_tokens(candidate_title)
    if not q_tokens or not c_tokens:
        return False
    overlap = q_tokens & c_tokens
    # Require most of the (shorter) title's meaningful words to be present.
    smaller = min(len(q_tokens), len(c_tokens))
    return smaller > 0 and len(overlap) / smaller >= 0.6


# -- HTTP helper ---------------------------------------------------------------
def _get(url, headers=None, retries=4):
    req_headers = {"Accept": "application/json", "User-Agent": "amc-showtimes/1.0 (+https://github.com/DrakeLin/amc-showtimes)"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    delay = 1.0
    last_err = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or exc.code >= 500:
                last_err = exc
                print(f"  WARN {exc.code} on {url}, retry in {delay:.1f}s", file=sys.stderr)
                time.sleep(delay)
                delay *= 2
            else:
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_err = exc
            print(f"  WARN network error on {url}: {exc}, retry in {delay:.1f}s", file=sys.stderr)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"Failed after {retries} retries: {url}") from last_err


# -- AMC helpers ---------------------------------------------------------------
def _amc_headers():
    return {"X-AMC-Vendor-Key": VENDOR_KEY}


def get_format(showtime):
    pf = showtime.get("premiumFormat", "")
    if pf:
        return pf
    known = {"IMAX": "IMAX", "DOLBY": "Dolby", "PLF": "PLF",
             "3D": "3D", "4DX": "4DX", "PRIME": "Prime", "DBOX": "D-BOX"}
    for attr in showtime.get("attributes", []) or []:
        code = str(attr).upper()
        for k, v in known.items():
            if k in code:
                return v
    return "Standard"


def get_movie_details(movie_id):
    """Fetch poster URL, director(s), and starring actors for a movie from AMC.

    Uses the same vendor key/HTTP path as showtimes, so no new external
    dependency -- just one extra request per distinct movie.
    """
    url = f"{AMC_BASE}/movies/{movie_id}"
    raw = _get(url, headers=_amc_headers())
    data = json.loads(raw)
    media = data.get("media", {}) or {}
    return {
        "poster": media.get("posterDynamic") or "",
        "director": data.get("directors") or "",
        "cast": data.get("starringActors") or "",
        "synopsis": data.get("synopsis") or "",
    }


def fetch_showtimes(theatre_id, show_date):
    date_str = show_date.strftime("%Y-%m-%d")
    showtimes = []
    for page in range(1, MAX_PAGES + 1):
        url = (
            f"{AMC_BASE}/theatres/{theatre_id}/showtimes/{date_str}"
            f"?pageNumber={page}&pageSize={PAGE_SIZE}"
        )
        raw = _get(url, headers=_amc_headers())
        data = json.loads(raw)
        page_items = data.get("_embedded", {}).get("showtimes", [])
        showtimes.extend(page_items)
        total = data.get("count", len(showtimes))
        if len(showtimes) >= total:
            break
        if page == MAX_PAGES:
            print(
                f"  WARN pagination cap hit for theatre {theatre_id} on {date_str}",
                file=sys.stderr,
            )
    return showtimes


# -- Letterboxd helpers --------------------------------------------------------
def lb_slug(title):
    t = _FORMAT_STRIP_RE.sub("", title)
    t = t.lower()
    t = re.sub(r"[^a-z0-9\s-]", "", t)
    t = re.sub(r"\s+", "-", t.strip())
    return t


def _parse_lb_film_page(html):
    rating = "N/A"
    m = _LB_RATING_RE.search(html)
    if m:
        rating = m.group(1)
    else:
        m = _LB_LD_RE.search(html)
        if m:
            rating = m.group(1)

    synopsis = ""
    m = re.search(r'<meta property="og:description" content="([^"]+)"', html)
    if m:
        synopsis = m.group(1).strip()
        if len(synopsis) <= 20 or "rating" in synopsis.lower():
            synopsis = ""

    return rating, synopsis


def _lb_search_slug(title):
    """Find a film's real slug (and its displayed name, for sanity-checking)
    via Letterboxd search. Handles year/disambiguation suffixes the naive
    slug guesses miss. Returns (slug, name) or (None, None)."""
    q = urllib.parse.quote(_FORMAT_STRIP_RE.sub("", title).strip())
    try:
        time.sleep(0.3)
        html = _get(
            f"{LB_BASE}/s/search/films/{q}/", headers={"Accept": "text/html"}
        ).decode("utf-8", errors="replace")
        slug = None
        m = re.search(r'data-film-slug="([^"]+)"', html)
        if m:
            slug = m.group(1)
        else:
            m = re.search(r'href="/film/([^/"]+)/"', html)
            if m:
                slug = m.group(1)
        if not slug:
            return None, None
        name = None
        m = re.search(r'data-film-name="([^"]+)"', html)
        if m:
            name = m.group(1)
        else:
            # Fall back to the slug itself as the "name" for sanity checking
            # -- turns "citizen-kane" into "citizen kane".
            name = slug.replace("-", " ")
        return slug, name
    except Exception:
        pass
    return None, None


def _progressive_queries(title):
    """Yield the cleaned title, then progressively shorter head segments split
    on ':' or ' - ', for retrying a failed search (e.g. "Foo: Bar Edition"
    -> "Foo" if "Foo: Bar" doesn't match anything)."""
    seen = set()
    cleaned = clean_title(title)
    if cleaned and cleaned not in seen:
        seen.add(cleaned)
        yield cleaned

    remainder = cleaned
    while True:
        m = re.search(r"^(.*?)\s*(:| - | – )\s*(.+)$", remainder)
        if not m:
            break
        remainder = m.group(1).strip()
        if remainder and remainder not in seen:
            seen.add(remainder)
            yield remainder
        if not remainder:
            break


def get_lb_data(title):
    """Fetch (rating, synopsis, film_url) from Letterboxd; falls back to LB
    search when slug guesses miss or land on a page without a rating.
    film_url is the page the data actually came from, or None.

    AMC listing titles are often marketing copy ("... IMAX Opening Night Fan
    Event", "... - Studio Ghibli Fest 2026") that doesn't match Letterboxd's
    title, so we clean the title first and, if search still can't find a
    match, retry with progressively shorter head segments of the title.
    """
    cleaned = clean_title(title)
    slug = lb_slug(cleaned)
    year = date.today().year
    candidates = [
        f"{LB_BASE}/film/{slug}/",
        f"{LB_BASE}/film/{slug}-{year}/",
        f"{LB_BASE}/film/{slug}-{year - 1}/",
        f"{LB_BASE}/film/{slug}-{year - 2}/",
    ]
    best = ("N/A", "", None)
    tried = set()
    for url in candidates:
        tried.add(url)
        try:
            time.sleep(0.3)
            html = _get(url, headers={"Accept": "text/html"}).decode("utf-8", errors="replace")
        except Exception:
            continue
        rating, synopsis = _parse_lb_film_page(html)
        if rating != "N/A":
            return rating, synopsis, url
        # Page exists (200) even without rating/synopsis (e.g. unreleased
        # films) -- keep its url so the frontend can still link to it.
        if synopsis and not best[1]:
            best = (best[0], synopsis, url)
        elif best[2] is None:
            best = (best[0], best[1], url)

    # Slug guesses failed to find a rating -- ask Letterboxd search, retrying
    # with progressively shorter queries (e.g. "Foo: Bar Edition" -> "Foo")
    # if the full cleaned title doesn't turn up a plausible match.
    for query in _progressive_queries(title):
        found_slug, found_name = _lb_search_slug(query)
        if not found_slug:
            continue
        if not _titles_plausibly_match(query, found_name):
            continue
        url = f"{LB_BASE}/film/{found_slug}/"
        if url in tried:
            # Already fetched this exact page above with no rating.
            break
        try:
            time.sleep(0.3)
            html = _get(url, headers={"Accept": "text/html"}).decode("utf-8", errors="replace")
            rating, synopsis = _parse_lb_film_page(html)
            # Search found the film's real page -- return its url even if
            # the page has no rating/synopsis yet. Prefer this URL over a
            # bare slug guess, since an untouched slug guess can silently
            # resolve to a different, older film of the same name.
            return rating, synopsis or best[1], url
        except Exception:
            pass

    # Nothing plausible found via search either. Prefer a search-fallback URL
    # over a slug-guess URL when we have no rating -- a bare slug guess like
    # /film/obsession/ can land on a different, older film with that name.
    if best[2] is not None:
        return best
    return (best[0], best[1], None)


# -- Formatting helpers ----------------------------------------------------------
def fmt_time(dt_str):
    """'2026-07-06T19:30:00' -> '7:30 PM' (falls back to raw 'HH:MM' slice)."""
    try:
        h, m = int(dt_str[11:13]), int(dt_str[14:16])
        ampm = "PM" if h >= 12 else "AM"
        return f"{h % 12 or 12}:{m:02d} {ampm}"
    except Exception:
        return dt_str[11:16]
