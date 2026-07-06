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

All caches are in-memory (no Railway volume — costs money, and redeploys are rare enough that resetting on deploy is fine). Refresh button busts the schedule cache; `POST /api/refresh?full=1` also busts the Letterboxd cache.

## Deploying (Railway)

1. [railway.app](https://railway.app) → New Project → Deploy from GitHub repo → `drakelin/amc-showtimes`
2. Railway auto-detects `Procfile` (`gunicorn server:app ... --workers 1`) and `requirements.txt`
3. Service → Variables → set `AMC_VENDOR_KEY`
4. Push to `main` to redeploy. Grab the `*.railway.app` URL, add to iOS home screen (Safari → Share → Add to Home Screen) for the installed PWA experience.

`--workers 1` is required — the in-memory caches would fragment across workers otherwise.

## TODO / ideas (not yet built, unvalidated)

- [ ] Seat fill: `_seat_fill()` in `server.py` reads `totalSeatsCount`/`seatsRemaining` fields that haven't been confirmed present on AMC's public API response — verify against a live response before trusting the numbers.
- [ ] Pull-to-refresh gesture on mobile instead of only the header button.
- [ ] Push notifications when a highly-rated movie gets added to the week's schedule (would need a Web Push backend + VAPID keys — adds real infra, not free).
- [ ] Theatre picker — currently hardcoded to Metreon + Kabuki; could extend `THEATRES` and add a UI toggle.
- [ ] Show/skip movies already seen (would need a small persisted "seen" list, e.g. localStorage).
- [ ] Trailer links (YouTube search link or TMDB API) per movie card.
