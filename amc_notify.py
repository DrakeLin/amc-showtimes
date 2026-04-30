#!/usr/bin/env python3
"""AMC SF evening-showtime digest -> stdout JSON {subject, html}."""


import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from typing import Optional

# -- Configuration -------------------------------------------------------------
THEATRES = {"AMC Metreon 16": 2325, "AMC Kabuki 8": 4145}

EVENING_START = int(os.environ.get("AMC_EVENING_START", "16"))
EVENING_END   = int(os.environ.get("AMC_EVENING_END",   "22"))

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


# -- HTTP helper ---------------------------------------------------------------
def _get(url, headers=None, retries=4):
    req_headers = {"Accept": "application/json", "User-Agent": "amc-notify/1.0 (drakelin18@gmail.com)"}
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


# -- Date helpers --------------------------------------------------------------
def get_target_dates():
    """Return today through the next 6 days (7 days total)."""
    today = date.today()
    return [today + timedelta(days=i) for i in range(7)]


# -- AMC helpers ---------------------------------------------------------------
def _amc_headers():
    return {"X-AMC-Vendor-Key": VENDOR_KEY}


def _evening(showtime):
    dt_str = showtime.get("showDateTimeLocal", "")
    try:
        hour = int(dt_str[11:13])
        return EVENING_START <= hour < EVENING_END
    except (ValueError, IndexError):
        return False


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


def get_lb_data(title):
    """Fetch rating and synopsis from Letterboxd in a single request."""
    slug = lb_slug(title)
    year = date.today().year
    candidates = [
        f"{LB_BASE}/film/{slug}/",
        f"{LB_BASE}/film/{slug}-{year}/",
        f"{LB_BASE}/film/{slug}-{year - 1}/",
        f"{LB_BASE}/film/{slug}-{year - 2}/",
    ]
    for url in candidates:
        try:
            time.sleep(0.3)
            html = _get(url, headers={"Accept": "text/html"}).decode("utf-8", errors="replace")

            # Extract rating
            rating = "N/A"
            m = _LB_RATING_RE.search(html)
            if m:
                rating = m.group(1)
            else:
                m = _LB_LD_RE.search(html)
                if m:
                    rating = m.group(1)

            # Extract synopsis
            synopsis = ""
            m = re.search(r'<meta property="og:description" content="([^"]+)"', html)
            if m:
                synopsis = m.group(1).strip()
                if len(synopsis) <= 20 or "rating" in synopsis.lower():
                    synopsis = ""

            return rating, synopsis
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise
        except Exception:
            continue
    return "N/A", ""




# -- Rendering ----------------------------------------------------------------
_THEATRE_SHORT = {"AMC Kabuki 8": "Kabuki", "AMC Metreon 16": "Metreon"}
_SEP = "─" * 52


def _fmt_time(dt_str):
    try:
        h, m = int(dt_str[11:13]), int(dt_str[14:16])
        ampm = "PM" if h >= 12 else "AM"
        return f"{h % 12 or 12}:{m:02d} {ampm}"
    except Exception:
        return dt_str[11:16]


def _fmt_format(fmt):
    if fmt == "Standard":
        return ""
    return fmt.replace(" at AMC", "").replace("Cinema ", "")


def _pivot(digest):
    movies = {}
    for theatre, shows_by_date in digest.items():
        for show_date, show_list in shows_by_date.items():
            for mv in show_list:
                title = mv["title"]
                if title not in movies:
                    movies[title] = {"lb_rating": mv["lb_rating"], "synopsis": mv.get("synopsis", ""), "days": {}}
                days = movies[title]["days"]
                if show_date not in days:
                    days[show_date] = []
                days[show_date].append((theatre, mv["format"], mv["times"]))
    return movies


def _day_parts(day_info):
    parts = []
    for theatre, fmt, times in sorted(day_info):
        name = _THEATRE_SHORT.get(theatre, theatre)
        f_str = _fmt_format(fmt)
        label = f"{name} ({f_str})" if f_str else name
        parts.append(f"{label}: {', '.join(times)}")
    return " · ".join(parts)


def render(digest):
    all_dates = sorted({d for td in digest.values() for d in td})
    date_range_short = f"{all_dates[0].strftime('%-m/%-d')} - {all_dates[-1].strftime('%-m/%-d')}"
    date_range = f"{all_dates[0].strftime('%A %-m/%-d')} - {all_dates[-1].strftime('%A %-m/%-d')}"
    subject = f"AMC SF Showtimes - {date_range}"

    def _fmt_hour(h):
        return f"{h % 12 or 12} {'pm' if h >= 12 else 'am'}"

    intro = (
        f"Movies ordered by Letterboxd rating — {date_range_short}, "
        f"{_fmt_hour(EVENING_START)} to {_fmt_hour(EVENING_END)}"
    )

    movies = _pivot(digest)

    def _sort_key(title):
        r = movies[title]["lb_rating"]
        return (-float(r) if r != "N/A" else 0.0, title)

    lines = [intro, ""]

    for title in sorted(movies, key=_sort_key):
        info = movies[title]
        rating = info["lb_rating"]
        synopsis = info.get("synopsis", "")
        if len(synopsis) > 200:
            synopsis = synopsis[:197] + "..."

        rating_str = f"  ★ {rating}" if rating != "N/A" else ""
        lines.append(_SEP)
        lines.append(f"{title}{rating_str}")
        if synopsis:
            lines.append(synopsis)
        lines.append("")
        for show_date in sorted(info["days"]):
            lines.append(f"  {show_date.strftime('%a %-m/%-d'):<10} {_day_parts(info['days'][show_date])}")
        lines.append("")

    lines.append(_SEP)
    return subject, "\n".join(lines)


# -- Main ----------------------------------------------------------------------
def main():
    if not VENDOR_KEY:
        print("ERROR: AMC_VENDOR_KEY env var is not set", file=sys.stderr)
        sys.exit(1)

    dates = get_target_dates()
    print(f"Target dates: {[str(d) for d in dates]}", file=sys.stderr)

    # digest[theatre][date] = [{title, format, times, lb_rating}]
    digest = {}

    for theatre_name, theatre_id in THEATRES.items():
        digest[theatre_name] = {}
        for show_date in dates:
            print(f"Fetching {theatre_name} / {show_date} ...", file=sys.stderr)
            try:
                raw_shows = fetch_showtimes(theatre_id, show_date)
            except Exception as exc:
                print(f"  ERROR: {exc}", file=sys.stderr)
                continue

            evening = [s for s in raw_shows if _evening(s)]
            print(f"  {len(evening)} evening showtimes", file=sys.stderr)
            if not evening:
                continue

            # Group by (title, format), collect unique showtimes
            movies = {}
            for s in evening:
                title = s.get("movieTitle") or s.get("movieName") or "Unknown"
                fmt = get_format(s)
                key = (title, fmt)
                t_label = _fmt_time(s.get("showDateTimeLocal", ""))
                if key not in movies:
                    movies[key] = {"title": title, "format": fmt, "times": [], "lb_rating": None}
                if t_label not in movies[key]["times"]:
                    movies[key]["times"].append(t_label)

            # Letterboxd ratings + synopsis -- deduplicated by title
            seen_lb = {}
            for (title, _fmt), mv in movies.items():
                if title not in seen_lb:
                    print(f"  Letterboxd: {title}", file=sys.stderr)
                    rating, synopsis = get_lb_data(title)
                    seen_lb[title] = (rating, synopsis)
                mv["lb_rating"] = seen_lb[title][0]
                mv["synopsis"] = seen_lb[title][1]

            digest[theatre_name][show_date] = list(movies.values())

    subject, text = render(digest)
    print(json.dumps({"subject": subject, "text": text}))


if __name__ == "__main__":
    main()
