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

_cache: dict = {"data": None, "ts": 0}
_cache_lock = threading.Lock()
CACHE_TTL = 3600  # 1 hour (showtimes/seat fills change often)

_lb_cache: dict = {}  # title -> {"rating": str, "synopsis": str, "ts": float}
_lb_cache_lock = threading.Lock()
LB_CACHE_TTL = 7 * 24 * 3600  # 1 week (ratings/synopses rarely change)


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


def build_showtimes():
    dates = [date.today() + timedelta(days=i) for i in range(7)]
    movies: dict = {}  # title -> {lb_rating, synopsis, showings: [{date, theatre, format, times, fill}]}

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
                fill = _seat_fill(s)
                if key not in groups:
                    groups[key] = {"times": [], "fills": []}
                if t_label not in groups[key]["times"]:
                    groups[key]["times"].append(t_label)
                    groups[key]["fills"].append(fill)

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

                # Average fill across times (exclude None)
                fills = [f for f in g["fills"] if f is not None]
                avg_fill = round(sum(fills) / len(fills)) if fills else None

                movies[title]["showings"].append({
                    "date": show_date.isoformat(),
                    "date_label": show_date.strftime("%a %-m/%-d"),
                    "theatre": theatre_name,
                    "theatre_short": amc._THEATRE_SHORT.get(theatre_name, theatre_name),
                    "format": fmt,
                    "times": sorted(g["times"]),
                    "fill_pct": avg_fill,
                })

    result = sorted(
        movies.values(),
        key=lambda m: (-float(m["lb_rating"]) if m["lb_rating"] != "N/A" else 0.0, m["title"]),
    )
    return result


def get_cached():
    with _cache_lock:
        if _cache["data"] is None or time.time() - _cache["ts"] > CACHE_TTL:
            print("Refreshing showtime cache...", file=sys.stderr)
            _cache["data"] = build_showtimes()
            _cache["ts"] = time.time()
        return _cache["data"]


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/showtimes")
def showtimes():
    try:
        data = get_cached()
        return jsonify({"ok": True, "movies": data, "cached_at": _cache["ts"]})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/refresh", methods=["POST"])
def refresh():
    with _cache_lock:
        _cache["data"] = None
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
