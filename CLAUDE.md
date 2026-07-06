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

Caches are in-memory, but the built schedule is also persisted as a single ~70KB JSON object (`schedule.json`, same shape as the dev disk cache) in the `gs://showtimes-123-cache` GCS bucket whenever it's rebuilt. Cloud Run instances are ephemeral (scale-to-zero after ~15 min idle kills the in-memory cache), so on cold start the server loads the snapshot from GCS (<1s) instead of paying the 30-60s rebuild. Stdlib-only (metadata-server token + JSON API, no google-cloud-storage dep); controlled by the `GCS_BUCKET` env var (unset = disabled, e.g. in local dev where the disk cache covers restarts). Usage is ~1% of GCS's always-free tier.

Refresh button / `POST /api/refresh` busts the schedule cache **and rebuilds it synchronously** before returning (so a scheduled call leaves a fresh cache + GCS snapshot behind, not an empty cache); `?full=1` also busts the Letterboxd and movie-metadata caches.

A daemon thread in `server.py` rebuilds the schedule cache every 12h. Know that in the deployed config it's mostly decorative: with `--min-instances 0` an instance rarely lives 12h, and Cloud Run's default request-based billing throttles CPU to ~zero between requests, so the sleeping thread barely advances even on a warm instance. Real freshness comes from the Cloud Scheduler job below; the thread only matters under sustained traffic or an always-on-CPU config.

## Scheduled refresh (Cloud Scheduler — job `amc-showtimes-refresh`, us-west1)

AMC posts the new week's showtimes by Wednesday afternoon. A Cloud Scheduler job POSTs to `/api/refresh` twice daily; because refresh rebuilds synchronously, each run wakes a cold instance, rebuilds the schedule, and writes a fresh GCS snapshot for the next cold start. The 17:00 run catches Wednesday's weekly showtime drop; the 05:00 run keeps mornings fresh. It was created with:

```bash
gcloud scheduler jobs create http amc-showtimes-refresh \
  --schedule "0 5,17 * * *" \
  --time-zone "America/Los_Angeles" \
  --uri "https://amc-showtimes-114648525819.us-west1.run.app/api/refresh" \
  --http-method POST \
  --attempt-deadline 300s \
  --location us-west1
```

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

For your own deployment, first create the snapshot bucket and let the Cloud Run service account write to it (skip and omit `GCS_BUCKET` if you don't care about cold-start speed):

```bash
gcloud storage buckets create gs://<your-bucket> --location us-west1 --uniform-bucket-level-access
gcloud storage buckets add-iam-policy-binding gs://<your-bucket> \
  --member="serviceAccount:<project-number>-compute@developer.gserviceaccount.com" \
  --role=roles/storage.objectAdmin
```

Then add `,GCS_BUCKET=<your-bucket>` to `--set-env-vars` above. This builds the `Dockerfile` in Cloud Build and deploys it. `--max-instances 1` keeps the in-memory caches from fragmenting across concurrent instances, matching the `--workers 1` intent in the Dockerfile. Grab the printed `*.run.app` URL and add it to your phone's home screen (Safari → Share → Add to Home Screen) for the installed PWA experience.

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
