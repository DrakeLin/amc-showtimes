# AMC Showtimes

A Flask PWA at drakelin18@gmail.com's phone: shows movies playing 3–8pm at AMC Metreon 16 / Kabuki 8, sorted by Letterboxd rating, with live seat-fill %.

- **`amc.py`** — shared AMC Theatres / Letterboxd fetch + parse helpers (no CLI, no rendering — just data fetching)
- **`server.py`** — Flask API (schedule + fill endpoints) + serves `static/`
- **`static/`** — PWA frontend (HTML/CSS/JS, manifest, service worker)

Don't read `amc.py` or `server.py` in full unless you're touching their logic — grep for the function you need first.

## Caching model (server.py)

Three tiers, deliberately different TTLs because these values change at different rates:

| Data | Cache | Why |
|---|---|---|
| Schedule (movies/times) | 24h, in-memory | Rarely changes within a day |
| Letterboxd rating/synopsis | 7 days, in-memory, keyed by title | Essentially static |
| Seat fill % | None — fetched fresh every page load via `/api/fills` | The one fast-moving number |

All caches are in-memory, no persistent volume — Cloud Run instances are ephemeral by design, so the cache resets whenever the instance scales to zero and cold-starts again (idle timeout, default ~15 min). That's the tradeoff for staying on Cloud Run's free tier. Refresh button busts the schedule cache; `POST /api/refresh?full=1` also busts the Letterboxd cache.

## Deploying (Google Cloud Run)

Why Cloud Run and not Railway/Vercel/PythonAnywhere: Railway dropped its free tier in 2023. Vercel's serverless functions have a 10s timeout — the schedule build (AMC + Letterboxd scraping with rate-limit sleeps) routinely takes 30-60s, so it would just fail. PythonAnywhere's free tier restricts outbound requests to an allowlist of documented public APIs — `api.amctheatres.com` and Letterboxd scraping aren't on it, so the app couldn't fetch data at all. Cloud Run has none of these problems and is free at this app's traffic scale.

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

- [ ] Seat fill: `_seat_fill()` in `server.py` reads `totalSeatsCount`/`seatsRemaining` fields that haven't been confirmed present on AMC's public API response — verify against a live response before trusting the numbers.
- [ ] Pull-to-refresh gesture on mobile instead of only the header button.
- [ ] Push notifications when a highly-rated movie gets added to the week's schedule (would need a Web Push backend + VAPID keys — adds real infra, not free).
- [ ] Theatre picker — currently hardcoded to Metreon + Kabuki; could extend `THEATRES` and add a UI toggle.
- [ ] Show/skip movies already seen (would need a small persisted "seen" list, e.g. localStorage).
- [ ] Trailer links (YouTube search link or TMDB API) per movie card.
