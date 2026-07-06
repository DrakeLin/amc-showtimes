# Contributing to AMC SF Showtimes

Thanks for your interest! This is a small personal-scale project, so the process is lightweight.

## Getting set up

1. You need an AMC Theatres API vendor key — request one at [developers.amctheatres.com](https://developers.amctheatres.com). Without it the server exits at startup.
2. Install and run:

   ```bash
   pip install -r requirements.txt
   AMC_VENDOR_KEY="your-key" FLASK_DEBUG=1 python3 server.py
   ```

   `FLASK_DEBUG=1` gives you auto-reload and a disk-backed schedule cache (`.dev_schedule_cache.json`) so restarts don't re-scrape AMC/Letterboxd every time. Frontend changes in `static/` just need a page reload.

## Before you code

- Read [CLAUDE.md](CLAUDE.md) — it documents the architecture, and especially the **caching model** (three deliberately different TTLs) and why seat status is an enum rather than a percentage. Changes that fight those decisions will need a strong rationale.
- Check the TODO list at the bottom of CLAUDE.md for ideas that are already wanted.
- For anything non-trivial, open an issue first to discuss.

## Ground rules

- **No new dependencies without discussion.** The whole app is Flask + gunicorn + the Python standard library (all scraping uses `urllib`); keep it that way unless there's a clear win.
- **Respect the scrape targets.** `amc.py` deliberately rate-limits Letterboxd requests. Don't remove sleeps or add parallel scraping that could get the app blocked.
- **Keep secrets out.** The vendor key comes from the `AMC_VENDOR_KEY` env var only — never commit keys, and don't add config files that hold them.
- **Match the existing style.** Plain Python, minimal abstractions, comments explaining *why* rather than *what*. The frontend is dependency-free vanilla JS/CSS.
- Update `README.md` / `CLAUDE.md` if your change alters behavior, endpoints, config, or the caching model.

## Submitting changes

1. Fork, branch from `main`.
2. Run the unit tests: `python3 -m unittest discover -s tests` (fast, no network). Add cases for any parsing/cleaning logic you change — that's where regressions hide.
3. Also verify against the live APIs by running the app and exercising the UI, including `/api/fills` seat-status coloring if you touched it.
4. Open a PR with a short description of what changed and why. Screenshots appreciated for UI changes.
