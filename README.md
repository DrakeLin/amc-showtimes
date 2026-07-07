# AMC SF Showtimes

A small Flask PWA that shows what's playing at AMC Metreon 16 and AMC Kabuki 8 in San Francisco, sorted by Letterboxd rating, with live per-showtime seat status (open / almost sold out / sold out).

Live at **<https://amc-showtimes-114648525819.us-west1.run.app/>** (deployed on Google Cloud Run).

The server returns full-day showtimes; day-of-week and time-range filtering happens entirely client-side (tap a day chip to cycle: all times → custom hours → skipped; choices persist in localStorage). Weekdays default to a 4–9 PM window, weekends to the full day.

## Repository layout

```
amc-showtimes/
├── amc.py            # AMC Theatres / Letterboxd fetch + parse helpers (data only)
├── server.py         # Flask app: API endpoints, caching, serves static/
├── static/           # PWA frontend (HTML/CSS/JS, manifest, service worker)
├── tests/            # unit tests (stdlib unittest, no network)
├── Dockerfile        # Cloud Run build (gunicorn, single worker)
└── requirements.txt
```

## API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/showtimes` | GET | Full-day schedule: movies, ratings, showtimes (12h `times` + parallel 24h `times24`) |
| `/api/fills` | GET | Fresh per-showtime seat status: `{"HH:MM": "sold_out" \| "almost" \| "open"}` per `fill_key` |
| `/api/status` | GET | Build progress while the schedule cache is (re)building |
| `/api/refresh` | POST | Bust the schedule cache; `?full=1` also busts Letterboxd + movie-metadata caches |

Seat status is an enum, not a percentage — AMC's public API exposes no seat counts, only `isSoldOut`/`isAlmostSoldOut` flags. It is fetched fresh on every page load; everything else is cached in-memory (schedule 24h, Letterboxd + movie metadata 7 days). See [CLAUDE.md](CLAUDE.md) for the full caching model.

## Configuration

| Var | Required | Default | Purpose |
|-----|----------|---------|---------|
| `AMC_VENDOR_KEY` | yes | — | AMC Theatres API vendor key (request one at [developers.amctheatres.com](https://developers.amctheatres.com)) |
| `PORT` | no | `8080` | Set automatically by Cloud Run |
| `FLASK_DEBUG` | no | — | Set to `1` for the local dev loop (see below) |
| `GCS_BUCKET` | no | — | GCS bucket for the schedule snapshot; unset disables it. Lets cold-started Cloud Run instances load the last built schedule (<1s) instead of re-scraping (30–60s) |
| `AMC_THEATRES` | no | SF: Metreon 16 + Kabuki 8 | Theatres to scrape, as `Name:id,Name:id` (e.g. `AMC Empire 25:375`); ids are in amctheatres.com URL slugs |


## Local development

```bash
pip install -r requirements.txt
AMC_VENDOR_KEY="your-key" FLASK_DEBUG=1 python3 server.py
```

Visit `http://localhost:8080`.

`FLASK_DEBUG=1` enables two things for a fast iteration loop:
- **Auto-reload** — the server restarts itself whenever you save a `.py` file.
- **Disk-backed schedule cache** (`.dev_schedule_cache.json`, gitignored) — without this, every reload would re-hit AMC + Letterboxd (30–60s). The schedule is written to disk after the first build and reloaded on subsequent restarts, still respecting the normal 24h TTL. Delete the file (or call `POST /api/refresh`) to force a real refetch.

Frontend-only changes (`static/`) don't need a restart at all — just reload the page.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

No network, no env vars, runs in well under a second. Coverage is the parsing/cleaning logic that breaks most often (title cleanup, Letterboxd page parsing, time/format helpers) — please add cases when you touch those.

## Deploying

The app runs on Google Cloud Run (free at this traffic scale). See [CLAUDE.md](CLAUDE.md) for the deploy command, the reasoning behind Cloud Run over other hosts, and the optional Cloud Scheduler refresh job.

## Failure modes

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `ERROR: AMC_VENDOR_KEY env var is not set` | Env var missing | Export it locally, or redeploy with `--set-env-vars AMC_VENDOR_KEY=...` |
| Schedule loads but no movies | Network issue or theatre IDs wrong | Check `THEATRES` IDs in `amc.py` |
| AMC returns 401/403 | Vendor key invalid | Regenerate key, update env var |
| All Letterboxd ratings are N/A | Letterboxd HTML changed | Update `_LB_RATING_RE` / `_LB_LD_RE` in `amc.py` |
| Seat status looks wrong | AMC changed the `isSoldOut`/`isAlmostSoldOut` flags | Inspect a live `/api/fills` response, adjust `_seat_status()` in `server.py` |

## Known fragility

- **Letterboxd** is unofficial scraping. If they restructure the page, all ratings silently go N/A.
- **AMC API** is stable in practice but undocumented publicly. Schema changes will surface as `KeyError`.

## Contributing

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for setup, style, and PR guidelines. Good starting points are the TODO list at the bottom of [CLAUDE.md](CLAUDE.md).

## License

[MIT](LICENSE)
