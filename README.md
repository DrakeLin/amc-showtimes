# AMC SF Showtimes

A small Flask PWA: movies playing 3–8pm at AMC Metreon 16 / Kabuki 8, sorted by Letterboxd rating, with live seat-fill %.

## Repository layout

```
amc-showtimes/
├── amc.py            # Shared AMC Theatres / Letterboxd fetch + parse helpers
├── server.py         # Flask app: schedule + fill API, serves static/
├── static/           # PWA frontend (HTML/CSS/JS, manifest, service worker)
├── Dockerfile         # Cloud Run build
├── Procfile          # Kept for local/alt-PaaS use (gunicorn command)
└── requirements.txt
```

## Configuration

| Var | Required | Default | Purpose |
|-----|----------|---------|---------|
| `AMC_VENDOR_KEY` | yes | — | AMC Theatres API vendor key |
| `AMC_EVENING_START` | no | `15` | Earliest hour (24h) to include |
| `AMC_EVENING_END` | no | `20` | Latest hour, exclusive |
| `PORT` | no | `8080` | Set automatically by Cloud Run |

Theatres are hardcoded in `amc.py`:
```python
THEATRES = {"AMC Metreon 16": 2325, "AMC Kabuki 8": 4145}
```

## Local development

```bash
pip install -r requirements.txt
AMC_VENDOR_KEY="your-key" python3 server.py
```

Visit `http://localhost:8080`.

## Deploying

See `CLAUDE.md` for the Cloud Run deploy command and caching model.

## Failure modes

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `ERROR: AMC_VENDOR_KEY env var is not set` | Env var missing | Redeploy with `--set-env-vars AMC_VENDOR_KEY=...`, or `gcloud run services update` |
| Schedule loads but no movies | Network/theatre IDs wrong, or nothing playing 3-8pm | Check `THEATRES` IDs in `amc.py`, try widening `AMC_EVENING_START/END` |
| AMC returns 401/403 | Vendor key invalid | Regenerate key, update env var |
| All Letterboxd ratings are N/A | Letterboxd HTML changed | Update `_LB_RATING_RE` / `_LB_LD_RE` in `amc.py` |
| Seat fill always shows `…` | AMC response doesn't include `totalSeatsCount`/`seatsRemaining` (unverified — see CLAUDE.md TODOs) | Inspect a live `/api/fills` response, adjust `_seat_fill()` in `server.py` |

## Known fragility

- **Letterboxd** is unofficial scraping. If they restructure the page, all ratings silently go N/A.
- **AMC API** is stable in practice but undocumented publicly. Schema changes will surface as `KeyError`.
