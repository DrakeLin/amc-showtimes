# AMC Showtimes

A personal-scale Flask PWA deployed on Google Cloud Run at https://amc-showtimes-114648525819.us-west1.run.app/ (installed to the owner's phone home screen): shows movies playing at AMC Metreon 16 / Kabuki 8, sorted by Letterboxd rating, with live per-showtime seat status. The server returns full-day showtimes (with a parallel 24h `times24` list per showing); day-of-week and time-range filtering happens client-side (day chips: each tap cycles all times → custom hours (time-range popover) → skipped; persisted in localStorage).

- **`amc.py`** — shared AMC Theatres / Letterboxd fetch + parse helpers (no CLI, no rendering — just data fetching)
- **`server.py`** — Flask API (schedule + fill endpoints) + serves `static/`
- **`static/`** — PWA frontend (HTML/CSS/JS, manifest, service worker)

Don't read `amc.py` or `server.py` in full unless you're touching their logic — grep for the function you need first.

Repo docs: `README.md` covers setup/API/config for humans; `CONTRIBUTING.md` has contributor ground rules (no new deps without discussion, keep scrape rate limits, secrets via env var only); license is MIT (`LICENSE`). Keep README/CONTRIBUTING in sync when changing endpoints, config, or the caching model. The only runtime deps are `flask` + `gunicorn`; all scraping uses stdlib `urllib`.

## Caching model (server.py)

Three tiers, deliberately different TTLs because these values change at different rates:

| Data | Cache | Why |
|---|---|---|
| Schedule (movies + full-day times/times24) | 24h, in-memory | Rarely changes within a day; day/time filtering is client-side |
| Letterboxd rating/synopsis/`lb_url` | 7 days, in-memory, keyed by title | Essentially static; `lb_url` is the film page the data came from, linked from the star pill |
| Movie metadata (poster/director/cast) | 7 days, in-memory, keyed by AMC movieId | Essentially static |
| Seat status | None — fetched fresh every page load via `/api/fills` | The one fast-moving value |

Seat status is an enum, not a percentage: AMC's public API exposes no seat counts (verified live), only per-showtime `isSoldOut`/`isAlmostSoldOut` flags. `/api/fills` returns, per `fill_key`, a `{"HH:MM": "sold_out" | "almost" | "open"}` map (same 24h keys as `times24`); the frontend colors each time pill accordingly.

All caches are in-memory, no persistent volume — Cloud Run instances are ephemeral by design, so the cache resets whenever the instance scales to zero and cold-starts again (idle timeout, default ~15 min). That's the tradeoff for staying on Cloud Run's free tier. Refresh button busts the schedule cache; `POST /api/refresh?full=1` also busts the Letterboxd and movie-metadata caches.

A daemon thread in `server.py` rebuilds the schedule cache every 12h (skips a cycle if a build is already holding the cache lock; logs to stderr). Note that with `--min-instances 0` this thread only helps while an instance happens to be warm — see the Cloud Scheduler section below for cold coverage.

## Scheduled refresh (Cloud Scheduler — configure manually, not yet set up)

AMC posts the new week's showtimes by Wednesday afternoon. A Cloud Scheduler job hitting the refresh endpoint twice daily both refreshes the cache and pre-warms a cold instance (which the in-process 12h thread can't do at min-instances 0):

```bash
gcloud scheduler jobs create http amc-showtimes-refresh \
  --schedule "0 5,17 * * *" \
  --time-zone "America/Los_Angeles" \
  --uri "https://<service-url>/api/refresh" \
  --http-method POST \
  --location us-west1
```

The 17:00 run catches Wednesday's weekly showtime drop; the 05:00 run keeps mornings fresh.

## Deploying (Google Cloud Run)

Why Cloud Run: the app needs long request handling (the schedule build — AMC + Letterboxd scraping with rate-limit sleeps — routinely takes 30-60s, which rules out serverless platforms with ~10s function timeouts), unrestricted outbound HTTP (some free tiers allowlist which hosts you can fetch from), and a free tier at this traffic scale. Cloud Run satisfies all three.

Prereqs: a Google Cloud project with billing enabled (Cloud Run's free tier doesn't require a paid account, but GCP requires a card on file) and the `gcloud` CLI installed and authenticated (`gcloud init`).

```bash
gcloud run deploy amc-showtimes \
  --source . \
  --region us-west1 \
  --allow-unauthenticated \
  --set-env-vars AMC_VENDOR_KEY=your_key_here \
  --timeout 300 \
  --min-instances 0 \
  --max-instances 1
```

This builds the `Dockerfile` in Cloud Build and deploys it. `--max-instances 1` keeps the in-memory caches from fragmenting across concurrent instances, matching the `--workers 1` intent in the Dockerfile. Grab the printed `*.run.app` URL and add it to your phone's home screen (Safari → Share → Add to Home Screen) for the installed PWA experience.

To redeploy after code changes, just re-run the same `gcloud run deploy` command.

**Cold starts**: since `--min-instances 0`, the container spins down after ~15 min idle. The next request pays a few-second cold start plus a full cache rebuild if the in-memory cache was also lost. Acceptable for a personal app opened a few times a day; if it's annoying, `--min-instances 1` keeps one instance warm at all times but will likely exceed the free tier's compute-time allowance and start incurring (small) charges.

## TODO / ideas (not yet built, unvalidated)

- [ ] Pull-to-refresh gesture on mobile instead of only the header button.
- [ ] Push notifications when a highly-rated movie gets added to the week's schedule (would need a Web Push backend + VAPID keys — adds real infra, not free).
- [ ] Theatre picker — currently hardcoded to Metreon + Kabuki; could extend `THEATRES` and add a UI toggle.
- [ ] Show/skip movies already seen (would need a small persisted "seen" list, e.g. localStorage).
- [ ] Trailer links (YouTube search link or TMDB API) per movie card.

Defaults and UI notes:

- Weekdays (Mon–Fri) default to 4:00 PM–9:00 PM and are pre-filtered on first load.
- Weekends (Sat–Sun) default to full day (00:00–23:59) and remain open on first load.
- Time-range semantics: start==end is treated as full-day; default full-day sentinel is `23:59`.
- Letterboxd badge: if a Letterboxd page exists we show `Letterboxd: ★ <rating>`; if page exists but rating is unavailable we show `Letterboxd: ★ N/A`; if no Letterboxd page is found we show plain `N/A`.
- The time picker UI uses two knobs; sliders are spaced for touch and labels show concise AM/PM (e.g., `3 AM – 4:30 PM`).
