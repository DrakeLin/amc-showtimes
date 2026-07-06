#!/usr/bin/env python3
"""AMC Showtimes PWA server."""

import json
import os
import sys
import threading
import time
from datetime import date, timedelta

from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(__file__))
import amc

app = Flask(__name__, static_folder="static", static_url_path="/static")

# Historical server-side evening window; no longer used to filter, kept only
# as the default hint for the frontend's initial (unfiltered) UI state.
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

# Movie metadata (poster/director/cast) is essentially static -> cache weekly,
# same TTL as Letterboxd, keyed by AMC movieId.
_movie_meta_cache: dict = {}  # movie_id -> {"poster": str, "director": str, "cast": str, "ts": float}
_movie_meta_cache_lock = threading.Lock()

# Seat fill changes minute to minute -> never cached, fetched fresh on demand.

# Build status for tracking progress during long initial cache build.
_build_status: dict = {"stage": "idle", "detail": "", "progress": 0}
_build_status_lock = threading.Lock()


def get_lb_data_cached(title):
    with _lb_cache_lock:
        entry = _lb_cache.get(title)
        if entry is not None and time.time() - entry["ts"] < LB_CACHE_TTL:
            return entry["rating"], entry["synopsis"], entry.get("url")

    rating, synopsis, url = amc.get_lb_data(title)

    with _lb_cache_lock:
        _lb_cache[title] = {"rating": rating, "synopsis": synopsis, "url": url, "ts": time.time()}

    return rating, synopsis, url


def get_movie_meta_cached(movie_id):
    with _movie_meta_cache_lock:
        entry = _movie_meta_cache.get(movie_id)
        if entry is not None and time.time() - entry["ts"] < LB_CACHE_TTL:
            return entry

    try:
        meta = amc.get_movie_details(movie_id)
    except Exception as exc:
        print(f"WARN: movie details for {movie_id}: {exc}", file=sys.stderr)
        meta = {"poster": "", "director": "", "cast": ""}

    entry = {**meta, "ts": time.time()}
    with _movie_meta_cache_lock:
        _movie_meta_cache[movie_id] = entry

    return entry


def _time24(showtime):
    """Return 'HH:MM' 24h sortable/filterable string, or None if unparseable."""
    dt_str = showtime.get("showDateTimeLocal", "")
    try:
        h, m = int(dt_str[11:13]), int(dt_str[14:16])
        return f"{h:02d}:{m:02d}"
    except (ValueError, IndexError):
        return None


def _seat_status(showtime):
    """Return "sold_out" | "almost" | "open" for a showtime.
    Verified live 2026-07: the public showtimes API exposes no seat counts
    (no totalSeatsCount/seatsRemaining; seating-layout links 404 at this
    vendor-key tier). Only the isSoldOut/isAlmostSoldOut flags are available."""
    if showtime.get("isSoldOut"):
        return "sold_out"
    if showtime.get("isAlmostSoldOut"):
        return "almost"
    return "open"


def _fill_key(theatre_id, show_date, title, fmt):
    return f"{theatre_id}|{show_date.isoformat()}|{title}|{fmt}"


def _set_build_status(stage, detail="", progress=0):
    """Update build progress status."""
    with _build_status_lock:
        _build_status["stage"] = stage
        _build_status["detail"] = detail
        _build_status["progress"] = progress


def build_schedule():
    """Movies + showtimes + Letterboxd ratings, WITHOUT seat fill (fetched separately, on demand)."""
    dates = [date.today() + timedelta(days=i) for i in range(7)]
    movies: dict = {}  # title -> {lb_rating, synopsis, showings: [...]}

    total_steps = len(amc.THEATRES) * len(dates)
    step = 0

    for theatre_name, theatre_id in amc.THEATRES.items():
        for show_date in dates:
            step += 1
            pct = int(5 + 90 * (step - 1) / total_steps)
            short = amc._THEATRE_SHORT.get(theatre_name, theatre_name)
            day = show_date.strftime("%a %-m/%-d")
            _set_build_status("amc", f"Showtimes: {short} · {day}", pct)
            try:
                raw = amc.fetch_showtimes(theatre_id, show_date)
            except Exception as exc:
                print(f"WARN: {theatre_name} {show_date}: {exc}", file=sys.stderr)
                continue

            if not raw:
                continue

            # Group by (title, format) -- ALL showtimes for the day; the client
            # filters by day/time range using each time's times24 value.
            groups: dict = {}
            for s in raw:
                title = s.get("movieTitle") or s.get("movieName") or "Unknown"
                fmt = amc.get_format(s)
                key = (title, fmt)
                t_label = amc._fmt_time(s.get("showDateTimeLocal", ""))
                t24 = _time24(s)
                if t24 is None:
                    continue
                if key not in groups:
                    groups[key] = {"times": [], "times24": [], "movie_id": s.get("movieId")}
                if t_label not in groups[key]["times"]:
                    groups[key]["times"].append(t_label)
                    groups[key]["times24"].append(t24)

            seen_lb: dict = {}
            for (title, fmt), g in groups.items():
                if title not in seen_lb:
                    _set_build_status("letterboxd", f"Rating: {title}", pct)
                    seen_lb[title] = get_lb_data_cached(title)
                lb_rating, synopsis, lb_url = seen_lb[title]

                if title not in movies:
                    meta = {"poster": "", "director": "", "cast": ""}
                    if g["movie_id"]:
                        _set_build_status("details", f"Details: {title}", pct)
                        meta = get_movie_meta_cached(g["movie_id"])
                    movies[title] = {
                        "title": title,
                        "lb_rating": lb_rating,
                        "lb_url": lb_url or "",
                        "synopsis": meta.get("synopsis") or synopsis,
                        "poster": meta.get("poster", ""),
                        "director": meta.get("director", ""),
                        "cast": meta.get("cast", ""),
                        "showings": [],
                    }

                order = sorted(range(len(g["times"])), key=lambda i: g["times24"][i])
                movies[title]["showings"].append({
                    "date": show_date.isoformat(),
                    "date_label": show_date.strftime("%a %-m/%-d"),
                    "theatre": theatre_name,
                    "theatre_short": amc._THEATRE_SHORT.get(theatre_name, theatre_name),
                    "format": fmt,
                    "times": [g["times"][i] for i in order],
                    "times24": [g["times24"][i] for i in order],
                    "fill_key": _fill_key(theatre_id, show_date, title, fmt),
                })

    _set_build_status("amc", "Sorting…", 97)

    result = sorted(
        movies.values(),
        key=lambda m: (-float(m["lb_rating"]) if m["lb_rating"] != "N/A" else 0.0, m["title"]),
    )

    _set_build_status("done", "", 100)
    return result


# Dev-only: persist the schedule cache to disk so the Flask reloader (which
# restarts the process on every file save) doesn't re-hit AMC/Letterboxd
# on each edit. Never used in prod (Cloud Run instances are ephemeral anyway).
DEV_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".dev_schedule_cache.json")


def _load_dev_cache():
    if os.environ.get("FLASK_DEBUG") != "1" or not os.path.exists(DEV_CACHE_FILE):
        return
    try:
        with open(DEV_CACHE_FILE) as f:
            saved = json.load(f)
        if time.time() - saved["ts"] < SCHEDULE_CACHE_TTL:
            _schedule_cache["data"] = saved["data"]
            _schedule_cache["ts"] = saved["ts"]
            print("Loaded schedule from dev disk cache", file=sys.stderr)
    except Exception as exc:
        print(f"WARN: couldn't load dev cache: {exc}", file=sys.stderr)


def _save_dev_cache():
    if os.environ.get("FLASK_DEBUG") != "1":
        return
    try:
        with open(DEV_CACHE_FILE, "w") as f:
            json.dump({"data": _schedule_cache["data"], "ts": _schedule_cache["ts"]}, f)
    except Exception as exc:
        print(f"WARN: couldn't save dev cache: {exc}", file=sys.stderr)


def get_schedule():
    with _schedule_cache_lock:
        if _schedule_cache["data"] is None:
            _load_dev_cache()
        if _schedule_cache["data"] is None or time.time() - _schedule_cache["ts"] > SCHEDULE_CACHE_TTL:
            print("Refreshing daily schedule cache...", file=sys.stderr)
            _schedule_cache["data"] = build_schedule()
            _schedule_cache["ts"] = time.time()
            _save_dev_cache()
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

            groups: dict = {}
            for s in raw:
                title = s.get("movieTitle") or s.get("movieName") or "Unknown"
                fmt = amc.get_format(s)
                t24 = _time24(s)
                if t24 is None:
                    continue
                # Per-time status keyed the same "HH:MM" way as times24 so the
                # frontend can color each individual time pill.
                groups.setdefault((title, fmt), {})[t24] = _seat_status(s)

            for (title, fmt), statuses in groups.items():
                fills[_fill_key(theatre_id, show_date, title, fmt)] = statuses

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


@app.route("/api/status")
def status():
    """Return current build status for frontend progress tracking."""
    with _build_status_lock:
        return jsonify({
            "ok": True,
            "stage": _build_status["stage"],
            "detail": _build_status["detail"],
            "progress": _build_status["progress"],
        })


@app.route("/api/refresh", methods=["POST"])
def refresh():
    with _schedule_cache_lock:
        _schedule_cache["data"] = None
    if request.args.get("full") == "1":
        with _lb_cache_lock:
            _lb_cache.clear()
        with _movie_meta_cache_lock:
            _movie_meta_cache.clear()
    return jsonify({"ok": True})


# -- Background auto-refresh -----------------------------------------------------
# Rebuild the schedule cache every 12h while the process is alive. On Cloud Run
# with min-instances 0 this only helps while an instance is warm; the Cloud
# Scheduler job documented in CLAUDE.md covers cold instances (and pre-warms).
REFRESH_INTERVAL = 12 * 3600
_refresh_thread_started = False


def _auto_refresh_loop():
    while True:
        time.sleep(REFRESH_INTERVAL)
        # Non-blocking: if a build is already running (lock held), skip this cycle.
        if not _schedule_cache_lock.acquire(blocking=False):
            print("auto-refresh: build already in progress, skipping", file=sys.stderr)
            continue
        try:
            print("auto-refresh: rebuilding schedule cache", file=sys.stderr)
            _schedule_cache["data"] = build_schedule()
            _schedule_cache["ts"] = time.time()
            _save_dev_cache()
            print("auto-refresh: done", file=sys.stderr)
        except Exception as exc:
            print(f"auto-refresh: failed: {exc}", file=sys.stderr)
        finally:
            _schedule_cache_lock.release()


def _start_refresh_thread():
    global _refresh_thread_started
    if _refresh_thread_started:
        return
    # Under the Flask debug reloader the module is imported twice; only start
    # the thread in the actual serving process.
    if os.environ.get("FLASK_DEBUG") == "1" and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    _refresh_thread_started = True
    threading.Thread(target=_auto_refresh_loop, name="auto-refresh", daemon=True).start()
    print("auto-refresh: thread started (every 12h)", file=sys.stderr)


_start_refresh_thread()


if __name__ == "__main__":
    vendor_key = os.environ.get("AMC_VENDOR_KEY", "")
    if not vendor_key:
        print("ERROR: AMC_VENDOR_KEY env var is not set", file=sys.stderr)
        sys.exit(1)
    amc.VENDOR_KEY = vendor_key
    port = int(os.environ.get("PORT", "8080"))
    debug = os.environ.get("FLASK_DEBUG") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
