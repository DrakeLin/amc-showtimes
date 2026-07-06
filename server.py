#!/usr/bin/env python3
"""AMC Showtimes PWA server."""

import json
import os
import sys
import threading
import time
from datetime import date, timedelta

from flask import Flask, jsonify, request, send_from_directory

# Reuse helpers from amc_notify
sys.path.insert(0, os.path.dirname(__file__))
import amc_notify as amc

app = Flask(__name__, static_folder="static", static_url_path="/static")

# Override time window to 3–8pm
SHOW_START = int(os.environ.get("AMC_EVENING_START", "15"))
SHOW_END   = int(os.environ.get("AMC_EVENING_END",   "20"))

# Schedule (movies/times/ratings) changes rarely -> cache daily.
_schedule_cache: dict = {"data": None, "ts": 0}
_schedule_cache_lock = threading.Lock()
SCHEDULE_CACHE_TTL = 24 * 3600  # 1 day

# Letterboxd ratings/synopses change even less often -> cache weekly.
_lb_cache: dict = {}  # title -> {"rating": str, "synopsis": str, "ts": float}
_lb_cache_lock = threading.Lock()
LB_CACHE_TTL = 7 * 24 * 3600  # 1 week

# Seat fill changes minute to minute -> never cached, fetched fresh on demand.


def get_lb_data_cached(title):
    with _lb_cache_lock:
        entry = _lb_cache.get(title)
        if entry is not None and time.time() - entry["ts"] < LB_CACHE_TTL:
            return entry["rating"], entry["synopsis"]

    rating, synopsis = amc.get_lb_data(title)

    with _lb_cache_lock:
        _lb_cache[title] = {"rating": rating, "synopsis": synopsis, "ts": time.time()}

    return rating, synopsis


def _evening(showtime):
    dt_str = showtime.get("showDateTimeLocal", "")
    try:
        hour = int(dt_str[11:13])
        return SHOW_START <= hour < SHOW_END
    except (ValueError, IndexError):
        return False


def _seat_fill(showtime):
    """Return seat fill % (0-100) or None if unavailable."""
    total = showtime.get("totalSeatsCount") or showtime.get("totalSeats")
    remaining = showtime.get("seatsRemaining") or showtime.get("availableSeats")
    if total and remaining is not None:
        used = total - remaining
        return round(used / total * 100)
    # Some endpoints expose sold-out flag only
    if showtime.get("isSoldOut"):
        return 100
    return None


def _fill_key(theatre_id, show_date, title, fmt):
    return f"{theatre_id}|{show_date.isoformat()}|{title}|{fmt}"


def build_schedule():
    """Movies + showtimes + Letterboxd ratings, WITHOUT seat fill (fetched separately, on demand)."""
    dates = [date.today() + timedelta(days=i) for i in range(7)]
    movies: dict = {}  # title -> {lb_rating, synopsis, showings: [...]}

    for theatre_name, theatre_id in amc.THEATRES.items():
        for show_date in dates:
            try:
                raw = amc.fetch_showtimes(theatre_id, show_date)
            except Exception as exc:
                print(f"WARN: {theatre_name} {show_date}: {exc}", file=sys.stderr)
                continue

            evening = [s for s in raw if _evening(s)]
            if not evening:
                continue

            # Group by (title, format)
            groups: dict = {}
            for s in evening:
                title = s.get("movieTitle") or s.get("movieName") or "Unknown"
                fmt = amc.get_format(s)
                key = (title, fmt)
                t_label = amc._fmt_time(s.get("showDateTimeLocal", ""))
                if key not in groups:
                    groups[key] = {"times": []}
                if t_label not in groups[key]["times"]:
                    groups[key]["times"].append(t_label)

            seen_lb: dict = {}
            for (title, fmt), g in groups.items():
                if title not in seen_lb:
                    rating, synopsis = get_lb_data_cached(title)
                    seen_lb[title] = (rating, synopsis)
                lb_rating, synopsis = seen_lb[title]

                if title not in movies:
                    movies[title] = {
                        "title": title,
                        "lb_rating": lb_rating,
                        "synopsis": synopsis,
                        "showings": [],
                    }

                movies[title]["showings"].append({
                    "date": show_date.isoformat(),
                    "date_label": show_date.strftime("%a %-m/%-d"),
                    "theatre": theatre_name,
                    "theatre_short": amc._THEATRE_SHORT.get(theatre_name, theatre_name),
                    "format": fmt,
                    "times": sorted(g["times"]),
                    "fill_key": _fill_key(theatre_id, show_date, title, fmt),
                })

    result = sorted(
        movies.values(),
        key=lambda m: (-float(m["lb_rating"]) if m["lb_rating"] != "N/A" else 0.0, m["title"]),
    )
    return result


def get_schedule():
    with _schedule_cache_lock:
        if _schedule_cache["data"] is None or time.time() - _schedule_cache["ts"] > SCHEDULE_CACHE_TTL:
            print("Refreshing daily schedule cache...", file=sys.stderr)
            _schedule_cache["data"] = build_schedule()
            _schedule_cache["ts"] = time.time()
        return _schedule_cache["data"]


def fetch_fill_map():
    """Fresh (uncached) seat-fill lookup for every showing in the current schedule window."""
    dates = [date.today() + timedelta(days=i) for i in range(7)]
    fills: dict = {}

    for theatre_name, theatre_id in amc.THEATRES.items():
        for show_date in dates:
            try:
                raw = amc.fetch_showtimes(theatre_id, show_date)
            except Exception as exc:
                print(f"WARN fill lookup: {theatre_name} {show_date}: {exc}", file=sys.stderr)
                continue

            evening = [s for s in raw if _evening(s)]
            groups: dict = {}
            for s in evening:
                title = s.get("movieTitle") or s.get("movieName") or "Unknown"
                fmt = amc.get_format(s)
                key = (title, fmt)
                fill = _seat_fill(s)
                groups.setdefault(key, []).append(fill)

            for (title, fmt), fill_list in groups.items():
                valid = [f for f in fill_list if f is not None]
                avg_fill = round(sum(valid) / len(valid)) if valid else None
                fills[_fill_key(theatre_id, show_date, title, fmt)] = avg_fill

    return fills


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/showtimes")
def showtimes():
    try:
        data = get_schedule()
        return jsonify({"ok": True, "movies": data, "cached_at": _schedule_cache["ts"]})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/fills")
def fills():
    try:
        data = fetch_fill_map()
        return jsonify({"ok": True, "fills": data})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/refresh", methods=["POST"])
def refresh():
    with _schedule_cache_lock:
        _schedule_cache["data"] = None
    if request.args.get("full") == "1":
        with _lb_cache_lock:
            _lb_cache.clear()
    return jsonify({"ok": True})


if __name__ == "__main__":
    vendor_key = os.environ.get("AMC_VENDOR_KEY", "")
    if not vendor_key:
        print("ERROR: AMC_VENDOR_KEY env var is not set", file=sys.stderr)
        sys.exit(1)
    amc.VENDOR_KEY = vendor_key
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)
