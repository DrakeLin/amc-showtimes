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

import anthropic

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


def get_lb_rating(title):
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
            m = _LB_RATING_RE.search(html)
            if m:
                return m.group(1)
            m = _LB_LD_RE.search(html)
            if m:
                return m.group(1)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise
        except Exception:
            continue
    return "N/A"


# -- Wikipedia synopsis --------------------------------------------------------
_WIKI_BASE = "https://en.wikipedia.org/api/rest_v1/page/summary"
_WIKI_HEADERS = {"Accept": "application/json", "User-Agent": "amc-notify/1.0 (drakelin18@gmail.com)"}


def _synopsis_from_claude(title):
    """Fallback: ask Claude Haiku for a one-sentence film synopsis."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ""
    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": (
                    f"Give me a single sentence (max 160 characters) describing what the film "
                    f'"{title}" is about. Reply with only that sentence, no quotes or preamble.'
                ),
            }],
        )
        text = next((b.text for b in response.content if b.type == "text"), "").strip()
        if len(text) > 160:
            text = text[:157] + "..."
        return text
    except Exception as e:
        print(f"  WARN Claude synopsis failed for '{title}': {e}", file=sys.stderr)
        return ""


def get_synopsis(title):
    clean = _FORMAT_STRIP_RE.sub("", title).strip()
    year = date.today().year
    candidates = [
        clean,
        f"{clean} ({year} film)",
        f"{clean} ({year - 1} film)",
        f"{clean} ({year - 2} film)",
        clean + " (film)",
        clean + " (film series)",
    ]
    for slug in candidates:
        encoded = urllib.request.quote(slug.replace(" ", "_"))
        try:
            raw = _get(f"{_WIKI_BASE}/{encoded}", headers=_WIKI_HEADERS)
            data = json.loads(raw)
            # Skip disambiguation pages
            if data.get("type") == "disambiguation":
                continue
            extract = data.get("extract", "").strip()
            if not extract:
                continue
            # Skip articles that aren't about a film/movie
            desc = (data.get("description") or "").lower()
            cats = extract[:300].lower()
            if not any(w in desc or w in cats for w in ("film", "movie", "directed")):
                continue
            # Return the first sentence, capped at 160 chars
            first = extract.split(". ")[0]
            if len(first) > 160:
                first = first[:157] + "..."
            return first + ("." if not first.endswith(".") else "")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            print(f"    WARN Wikipedia {exc.code} for '{slug}'", file=sys.stderr)
            return _synopsis_from_claude(title)
        except Exception as e:
            print(f"    WARN Wikipedia error for '{slug}': {e}", file=sys.stderr)
            continue
    print(f"    INFO Wikipedia not found, trying Claude for '{title}'", file=sys.stderr)
    return _synopsis_from_claude(title)


# -- HTML rendering ------------------------------------------------------------
_CSS = """
  body  { font-family: Georgia, serif; max-width: 680px; margin: 2em auto; color: #222; }
  h1    { font-size: 1.4em; border-bottom: 2px solid #c00; padding-bottom: .3em; }
  .movie-block { margin: 1.6em 0 0; }
  .movie-title  { font-size: 1.05em; font-weight: bold; margin: 0 0 .1em; }
  .movie-rating { font-size: .9em; color: #e07000; font-weight: bold; }
  .synopsis { font-size: .84em; color: #555; margin: .15em 0 .4em; font-style: italic; }
  .na   { color: #bbb; }
  table { border-collapse: collapse; width: 100%; font-size: .86em; margin-top: .4em; }
  th    { text-align: left; border-bottom: 1px solid #ddd; padding: .25em .6em;
          color: #888; font-weight: normal; }
  td    { padding: .25em .6em; vertical-align: top; }
  tr:nth-child(even) { background: #f9f9f9; }
  .fmt  { font-size: .8em; color: #999; }
  hr.sep { border: none; border-top: 1px solid #eee; margin: 1.2em 0 0; }
"""


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
    subject = "AMC SF Showtimes — " + date_labels

    movies = _pivot(digest)

    # Build markdown output
    md_parts = [
        f"# AMC SF Evening Showtimes — {date_labels}\n\n",
    ]

    def _sort_key(title):
        r = movies[title]["lb_rating"]
        return (-float(r) if r != "N/A" else 0.0, title)

    for title in sorted(movies, key=_sort_key):
        info = movies[title]
        rating = info["lb_rating"]
        rating_str = f"{rating} ★" if rating != "N/A" else "N/A"

        md_parts.append(f"## {title} {rating_str}\n\n")

        synopsis = info.get("synopsis", "")
        if synopsis:
            md_parts.append(f"*{synopsis}*\n\n")

        md_parts.append("| Day | Theatre | Format | Showtimes |\n")
        md_parts.append("|-----|---------|--------|----------|\n")

        for show_date in sorted(info["days"]):
            day_label = show_date.strftime("%a %-m/%-d")
            for theatre, fmt, times in sorted(info["days"][show_date]):
                times_str = "  ".join(times)
                md_parts.append(
                    f"| {day_label} | {theatre} | {fmt} | {times_str} |\n"
                )

        md_parts.append("\n")

    markdown = "".join(md_parts)

    # Also generate HTML version for email
    html_parts = [
        '<!DOCTYPE html><html><head><meta charset="utf-8">',
        f"<style>{_CSS}</style></head><body>\n",
        f"<h1>AMC SF Evening Showtimes — {date_labels}</h1>\n",
    ]

    for i, title in enumerate(sorted(movies, key=_sort_key)):
        info = movies[title]
        rating = info["lb_rating"]
        rating_html = (
            f"<span class='movie-rating'>{rating} &#9733;</span>"
            if rating != "N/A"
            else "<span class='na'>N/A</span>"
        )
        if i > 0:
            html_parts.append("<hr class='sep'>\n")
        synopsis = info.get("synopsis", "")
        synopsis_html = f"<p class='synopsis'>{synopsis}</p>\n" if synopsis else ""
        html_parts.append(
            f"<div class='movie-block'>"
            f"<p class='movie-title'>{title}&ensp;{rating_html}</p>\n"
            f"{synopsis_html}"
            "<table><tr>"
            "<th>Day</th><th>Theatre</th><th>Format</th><th>Showtimes</th>"
            "</tr>\n"
        )
        for show_date in sorted(info["days"]):
            day_label = show_date.strftime("%a %-m/%-d")
            for theatre, fmt, times in sorted(info["days"][show_date]):
                times_str = "&nbsp;&nbsp;".join(times)
                html_parts.append(
                    f"<tr><td>{day_label}</td>"
                    f"<td>{theatre}</td>"
                    f"<td><span class='fmt'>{fmt}</span></td>"
                    f"<td>{times_str}</td></tr>\n"
                )
        html_parts.append("</table></div>\n")

    html_parts.append("</body></html>")
    html = "".join(html_parts)
    return markdown, subject, html


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

            # Letterboxd ratings + Wikipedia synopsis -- deduplicated by title
            seen_lb = {}
            seen_synopsis = {}
            for (title, _fmt), mv in movies.items():
                if title not in seen_lb:
                    print(f"  Letterboxd: {title}", file=sys.stderr)
                    seen_lb[title] = get_lb_rating(title)
                if title not in seen_synopsis:
                    print(f"  Synopsis:   {title}", file=sys.stderr)
                    seen_synopsis[title] = get_synopsis(title)
                mv["lb_rating"] = seen_lb[title]
                mv["synopsis"] = seen_synopsis[title]

            digest[theatre_name][show_date] = list(movies.values())

    markdown, subject, html = render(digest)
    print(json.dumps({"subject": subject, "markdown": markdown, "html": html}))


if __name__ == "__main__":
    main()
