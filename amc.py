#!/usr/bin/env python3
"""Shared AMC Theatres / Letterboxd helpers for the showtimes website."""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date

# -- Configuration -------------------------------------------------------------
THEATRES = {"AMC Metreon 16": 2325, "AMC Kabuki 8": 4145}
_THEATRE_SHORT = {"AMC Kabuki 8": "Kabuki", "AMC Metreon 16": "Metreon"}

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
    req_headers = {"Accept": "application/json", "User-Agent": "amc-showtimes/1.0 (drakelin18@gmail.com)"}
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


# -- Formatting helpers ---------------------------------------------------------
def _fmt_time(dt_str):
    try:
        h, m = int(dt_str[11:13]), int(dt_str[14:16])
        ampm = "PM" if h >= 12 else "AM"
        return f"{h % 12 or 12}:{m:02d} {ampm}"
    except Exception:
        return dt_str[11:16]
