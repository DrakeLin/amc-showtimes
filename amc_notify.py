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




# -- HTML rendering ------------------------------------------------------------
# Inline style constants for email compatibility (Gmail strips <style> blocks)
_S_WRAP   = 'font-family:Arial,Helvetica,sans-serif;background-color:#ffffff;color:#2d1f14;max-width:640px;margin:0 auto;padding:2em 1.2em;'
_S_INTRO  = 'font-size:.88em;color:#8a6a58;margin:0 0 1.4em;'
_S_BLOCK  = 'background:#fff8f3;border:1px solid #f0ddd0;border-radius:12px;padding:1.1em 1.2em 1em;margin:1em 0 0;'
_S_MHDR   = 'margin-bottom:.5em;'
_S_TITLE  = 'font-family:Georgia,serif;font-size:1.1em;font-weight:600;color:#1e120a;margin:0;display:inline;'
_S_RATING = 'display:inline-block;background:#c95c2e;color:#fff;font-size:.72em;font-weight:500;padding:.18em .55em;border-radius:20px;letter-spacing:.02em;white-space:nowrap;margin-left:.5em;vertical-align:middle;'
_S_NA     = 'color:#c8b0a4;font-size:.8em;margin-left:.5em;'
_S_SYN    = 'font-size:.82em;color:#7a5a4a;margin:0 0 .7em;line-height:1.5;font-style:italic;'
_S_TABLE  = 'border-collapse:collapse;width:100%;font-size:.82em;border:1px solid #edddd4;'
_S_TH     = 'text-align:left;padding:.35em .7em;background-color:#f5e8de;color:#9a6f5e;font-weight:500;font-size:.9em;border-bottom:1px solid #edddd4;'
_S_TD     = 'tc1'
_S_TD_ALT = 'tc1-alt'
_S_TD_LST = 'tc1'
_S_TD_LST_ALT = 'tc1-alt'
_S_FMT    = 'color:#b07060;font-size:.85em;'
_S_TIMES  = 'tc1-times'

# CSS for optimized HTML (to be embedded in <style> tag)
_CSS_CLASSES = """<style>
.tc1{padding:.32em .7em;vertical-align:top;border-bottom:1px solid #f5ece5;color:#3d2518;}
.tc1-alt{padding:.32em .7em;vertical-align:top;border-bottom:1px solid #f5ece5;color:#3d2518;background-color:#fdf3ed;}
.th1{text-align:left;padding:.35em .7em;background-color:#f5e8de;color:#9a6f5e;font-weight:500;font-size:.9em;border-bottom:1px solid #edddd4;}
.tc1-times{color:#1e120a;font-weight:500;letter-spacing:.01em;}
</style>"""


def _fmt_time(dt_str):
    try:
        h, m = int(dt_str[11:13]), int(dt_str[14:16])
        ampm = "PM" if h >= 12 else "AM"
        return f"{h % 12 or 12}:{m:02d} {ampm}"
    except Exception:
        return dt_str[11:16]


def _pivot(digest):
    """Pivot digest[theatre][date][movie] -> movies[title]{rating, days{date: rows}}."""
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


def render(digest):
    all_dates = sorted({d for td in digest.values() for d in td})
    date_labels = " / ".join(d.strftime("%-m/%-d") for d in all_dates)
    date_range_short = f"{all_dates[0].strftime('%-m/%-d')} - {all_dates[-1].strftime('%-m/%-d')}"
    date_range = f"{all_dates[0].strftime('%A %-m/%-d')} - {all_dates[-1].strftime('%A %-m/%-d')}"
    subject = f"AMC SF Showtimes - {date_range}"

    def _fmt_hour(h):
        return f"{h % 12 or 12} {'pm' if h >= 12 else 'am'}"

    intro = (
        f"Here are the movies, ordered by letterboxd rating, "
        f"for {date_range_short} from {_fmt_hour(EVENING_START)} to {_fmt_hour(EVENING_END)}"
    )

    movies = _pivot(digest)

    def _sort_key(title):
        r = movies[title]["lb_rating"]
        return (-float(r) if r != "N/A" else 0.0, title)

    # Generate HTML version for email (with CSS classes for optimization)
    html_parts = [
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'{_CSS_CLASSES}'
        '</head><body style="margin:0;padding:0;background-color:#ffffff;">\n',
        f'<div style="{_S_WRAP}">'
        f'<p style="{_S_INTRO}">{intro}</p>\n',
    ]

    for title in sorted(movies, key=_sort_key):
        info = movies[title]
        rating = info["lb_rating"]
        rating_html = (
            f'<span style="{_S_RATING}">{rating} &#9733;</span>'
            if rating != "N/A"
            else f'<span style="{_S_NA}">N/A</span>'
        )
        synopsis = info.get("synopsis", "")
        synopsis_html = f'<p style="{_S_SYN}">{synopsis}</p>\n' if synopsis else ""
        html_parts.append(
            f'<div style="{_S_BLOCK}">'
            f'<div style="{_S_MHDR}">'
            f'<span style="{_S_TITLE}">{title}</span>{rating_html}'
            f'</div>\n'
            f'{synopsis_html}'
            f'<table style="{_S_TABLE}">'
            f'<tr>'
            f'<th class="th1">Day</th>'
            f'<th class="th1">Showtime</th>'
            f'<th class="th1">Format</th>'
            f'<th class="th1">Theatre</th>'
            f'</tr>\n'
        )
        rows = []
        for show_date in sorted(info["days"]):
            day_label = show_date.strftime("%a %-m/%-d")
            for theatre, fmt, times in sorted(info["days"][show_date]):
                rows.append((day_label, theatre, fmt, times))
        for i, (day_label, theatre, fmt, times) in enumerate(rows):
            first_time = times[0] if times else ""
            is_even = (i % 2 == 1)
            td_class = _S_TD_ALT if is_even else _S_TD
            times_class = f'{td_class} {_S_TIMES}'
            html_parts.append(
                f'<tr>'
                f'<td class="{td_class}">{day_label}</td>'
                f'<td class="{times_class}">{first_time}</td>'
                f'<td class="{td_class}"><span style="{_S_FMT}">{fmt}</span></td>'
                f'<td class="{td_class}">{theatre}</td>'
                f'</tr>\n'
            )
        html_parts.append("</table></div>\n")

    html_parts.append("</div></body></html>")
    html = "".join(html_parts)
    return subject, html


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

    subject, html = render(digest)
    print(json.dumps({"subject": subject, "html": html}))


if __name__ == "__main__":
    main()
