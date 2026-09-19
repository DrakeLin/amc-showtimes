# AMC SF Showtimes

A small Flask PWA that shows what's playing at AMC Metreon 16 and AMC Kabuki 8 in San Francisco, sorted by Letterboxd rating, with live per-showtime seat status (open / almost sold out / sold out).

Live at **<https://amc-showtimes-114648525819.us-west1.run.app/>** (deployed on Google Cloud Run).

The server returns full-day showtimes; day-of-week and time-range filtering happens entirely client-side (tap a day chip to cycle: all times → custom hours → skipped; choices persist in localStorage). Weekdays default to a 4–9 PM window, weekends to the full day.

The theatre chips toggle visibility locally and remember your selection. Both configured theatres share the existing schedule cache; switching chips makes no network requests and adds no refresh jobs.

## Repository layout

```
amc-showtimes/
├── amc.py            # AMC Theatres / Letterboxd fetch + parse helpers (data only)
├── server.py         # Flask app: API endpoints, caching, serves static/
├── static/           # PWA frontend (HTML/CSS/JS, manifest, service worker)
├── tests/            # unit tests (stdlib unittest, no network)
├── Dockerfile        # Cloud Run build (gunicorn, 1 worker × 8 threads)
└── requirements.txt
```

## API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/showtimes` | GET | Full-day schedule: movies, ratings, showtimes (12h `times` + parallel 24h `times24`) |
| `/api/fills` | GET | Fresh per-showtime seat status: `{"HH:MM": "sold_out" \| "almost" \| "open"}` per `fill_key` |
| `/api/watch` | GET | Uncached title watcher for dates beyond the 7-day window (advance sales): `?title=<substring>&start=YYYY-MM-DD&days=N` (days max 14) returns matching showtimes with the same seat-status enum as `/api/fills` |
| `/api/status` | GET | Build progress while the schedule cache is (re)building |
| `/api/refresh` | POST | Rebuild the schedule synchronously (30–60s when server caches are cold) and persist the GCS snapshot; `?full=1` also busts Letterboxd + movie-metadata caches |

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

The app runs on Google Cloud Run (free at this traffic scale), with two optional pieces of supporting infra:

- a **GCS bucket** holding a ~70KB snapshot of the built schedule, so cold-started instances skip the 30–60s rebuild (`GCS_BUCKET` env var)
- a **Cloud Scheduler job** POSTing `/api/refresh` twice daily, which re-scrapes and rewrites that snapshot

See [CLAUDE.md](CLAUDE.md) for the deploy command, bucket setup, the scheduler job, and the reasoning behind Cloud Run over other hosts. For the installed-app experience, open the deployed URL on your phone and use Safari → Share → **Add to Home Screen**.

## Failure modes

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `ERROR: AMC_VENDOR_KEY env var is not set` | Env var missing | Export it locally, or redeploy with `--set-env-vars AMC_VENDOR_KEY=...` |
| Schedule loads but no movies | Network issue or theatre IDs wrong | Check `THEATRES` IDs in `amc.py` |
| AMC returns 401/403 | Vendor key invalid | Regenerate key, update env var |
| All Letterboxd ratings are N/A | Letterboxd HTML changed | Update `_LB_RATING_RE` / `_LB_LD_RE` in `amc.py` |
| Seat status looks wrong | AMC changed the `isSoldOut`/`isAlmostSoldOut` flags | Inspect a live `/api/fills` response, adjust `_seat_status()` in `server.py` |
| Refresh takes ~a minute | Expected: `/api/refresh` re-scrapes synchronously; slowest with `?full=1` or cold server caches | Progress bar is live via `/api/status`; just wait |
| Stale UI after deploying frontend changes | Service worker serves the cached shell | Bump `CACHE` in `static/sw.js`; installed PWAs update on their second launch |

## Known fragility

- **Letterboxd** is unofficial scraping. If they restructure the page, all ratings silently go N/A.
- **AMC API** is stable in practice but undocumented publicly. Schema changes will surface as `KeyError`.

## Contributing

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for setup, style, and PR guidelines. Good starting points are the TODO list at the bottom of [CLAUDE.md](CLAUDE.md).

## License

[MIT](LICENSE)

## Showtimes on Vercel (Hobby)

The Vercel entrypoint is `app.py`; `server.py` remains the legacy Cloud Run entrypoint.
Vercel uses the same PWA frontend, now branded **Showtimes**. No Google Cloud service
is used by the Vercel entrypoint. `build.py` copies static assets to Vercel's CDN.

### Deployment

1. Import this repository/branch into a **Hobby** Vercel project named `showtimes`
   (use `showtimes-drakelin` if the address is taken). Keep Fluid Compute enabled.
2. Create and connect a **private Vercel Blob** store. Set `BLOB_READ_WRITE_TOKEN`
   for Production. Use a separate store/token for Preview if enabling previews;
   do not point development at the production store.
3. Add these server-only environment variables:
   - `AMC_VENDOR_KEY`: the existing AMC vendor key from the Cloud Run service.
   - `OWNER_ACCESS_KEY`: a random access key of at least 32 characters. Generate
     with `python -c 'import secrets; print(secrets.token_urlsafe(32))'`. Keep it in
     your password manager; it unlocks favorites in the app. Do not put it in a URL.
   - `CRON_SECRET`: a different random secret, at least 32 characters. Vercel sends
     it as a Bearer token for the daily job.
   - Optional `AMC_TIMEZONE`: defaults to `America/Los_Angeles`; change to
     `America/New_York` when appropriate.
   - Optional `INITIAL_FAVORITE_NAMES`: JSON array of exact AMC theater names,
     defaults to `["AMC NewPark 12", "AMC Mercado 20"]`. Only used until the first
     saved favorites record exists. IDs are resolved from AMC's catalog.
4. Deploy. Verify `/api/health`, load the actual theater directory, sign in through
   **Theaters & favorites → Owner settings**, and test a saved favorite after a reload.
5. Trigger `/api/cron` once using its server-side Bearer secret to warm all favorite
   schedules and movie metadata. Never expose the cron secret to frontend code.
6. Retire the old Google services only after the new site's live checks pass.
   This repository does not automatically delete Cloud Run, GCS, Scheduler, or images.

Owner editing is enforced by signed, HttpOnly, Secure, SameSite=Strict sessions
and a CSRF header on mutations. The raw owner key is never persisted in browser
storage. Without a configured owner key, edits are disabled. Changing the key
invalidates existing sessions. Anyone can browse; only the owner can change favorites
or explicitly force an early refresh. There is one shared owner favorites list,
not an account system for visitors. Save replaces the list, so NewPark/Mercado
can replace the SF theaters rather than adding to them forever. An empty list is
valid and stops scheduled showtime fetching.

### Storage and fetching

- Favorites are a separate durable record, independent of schedule refreshes.
- Theater directory: cached for seven days; search matches theater name or city.
- Showtimes: separate record per theater and weekday, containing the actual date.
  Seven reusable slots keep schedule storage bounded per browsed theater.
- Opening the app reads existing snapshots first. Missing or stale theater/dates
  load progressively via individual requests with a 35-second upstream deadline.
  No background Python thread or in-memory progress polling is used on Vercel.
- Successful dates are saved immediately. Failed refreshes preserve the old data;
  an empty successful result is different from a failed request.
- Five-minute immutable claims reduce duplicate fetches across instances. A crashed
  request cannot permanently lock a theater/date. Slot boundaries can permit overlap;
  this is throttling, not a transactional distributed lock. Claim records are tiny
  but accumulate; at personal scale their storage is negligible.
- Daily cron at 12:00 UTC refreshes **only the current favorites**, at most three.
  It prioritizes showtimes, then uses a separate short budget to enrich cached AMC
  metadata and Letterboxd ratings. Metadata is persisted for reuse after cold starts.
  If the budget expires, remaining dates can still load on demand. Ratings for new
  non-favorite movies may remain unavailable until they are included in enrichment.
- Availability colors reflect the saved snapshot and are explicitly labeled as
  potentially changed; this version does not claim live seat status.

The two initial favorites require roughly 420 schedule writes + 420 claim writes
per 30 days, plus metadata/config writes. Reads and extra browsed theaters also
consume the free allowances. Stay on Hobby, use the free `vercel.app` domain, and
monitor Blob quotas. This is designed for personal use within free allowances,
not guaranteed unlimited traffic. No paid upgrade or paid database is required.

### Local checks

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests
LOCAL_DATA_DIR=.local-data OWNER_ACCESS_KEY='<your local test key>' \
  AMC_VENDOR_KEY='<your key>' .venv/bin/flask --app app run
```

`LOCAL_DATA_DIR` enables durable disk records only outside Vercel. It is ignored
on Vercel so a misconfiguration cannot silently save favorites to an ephemeral disk.
The `vercel` Python SDK is pinned to 0.11.3: its synchronous Blob API differs from
newer JavaScript examples (`overwrite`, `result.content`, etc.).
