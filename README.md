# AMC SF Showtimes

Weekly digest routine: evening showtimes at AMC Metreon 16 and AMC Kabuki 8 for the upcoming Tue–Thu, enriched with Letterboxd ratings, delivered as a Gmail draft.

## What this does

Every week the routine:
1. Hits the AMC Theatres API for showtimes at both SF locations on the next Tue/Wed/Thu
2. Filters to evening showtimes (4–9 PM by default)
3. Looks up each movie's Letterboxd rating
4. Renders a plain-text digest sorted by Letterboxd rating
5. Creates a Gmail draft to drakelin18@gmail.com — nothing is sent automatically

## Repository layout

```
amc-showtimes/
├── amc_notify.py    # The script. Stdlib only.
├── README.md
└── .gitignore
```

No dependencies, no virtualenv, no build step.

## Configuration

| Var | Required | Default | Purpose |
|-----|----------|---------|---------|
| `AMC_VENDOR_KEY` | yes | — | AMC Theatres API vendor key |
| `AMC_EVENING_START` | no | `16` | Earliest hour (24h) to include |
| `AMC_EVENING_END` | no | `21` | Latest hour, exclusive |

The AMC key is set as a routine secret. Never commit it.

Theatres are hardcoded in `amc_notify.py`:
```python
THEATRES = {"AMC Metreon 16": 2325, "AMC Kabuki 8": 4145}
```

## Routine setup (claude.ai/code/routines)

1. **New routine** → name it "AMC SF Weekly Digest"
2. **Repository**: `DrakeLin/amc-showtimes`, read-only
3. **Environment variables**: add `AMC_VENDOR_KEY`
4. **Network allowlist**: `api.amctheatres.com` and `letterboxd.com`
5. **Connectors**: enable Gmail
6. **Schedule**: weekly (Sunday evening recommended)
7. **Prompt**:

```
Run:
python3 amc_notify.py 2>/tmp/err.log

If the exit code is non-zero, print the contents of /tmp/err.log and stop.

Parse the JSON printed to stdout. It has two keys:
- "subject": the email subject line
- "text": the plain-text email body

Use the Gmail connector to create a draft:
To: drakelin18@gmail.com
subject: [the "subject" value]
body: [the "text" value]
```

## Local development

```bash
cd ~/Documents/test/amc-showtimes
AMC_VENDOR_KEY="your-key" python3 amc_notify.py 2>/tmp/err.log
echo "Exit: $?"
cat /tmp/err.log
```

Check the output:
```bash
python3 amc_notify.py 2>/dev/null | python3 -m json.tool
```

## How the script works

**Date selection** — `get_target_dates()` returns the upcoming Tue/Wed/Thu strictly in the future. If today is Wednesday, it returns next week's Tue/Wed/Thu.

**AMC pagination** — `fetch_showtimes()` walks `pageNumber` until the running total reaches `count`. Hard-capped at 20 pages.

**Evening filter** — `_evening()` parses `showDateTimeLocal` and checks the hour against `EVENING_START` (inclusive) and `EVENING_END` (exclusive).

**Format detection** — `get_format()` prefers `premiumFormat` from the API, then scans `attributes` for known codes (IMAX, Dolby, etc).

**Letterboxd slugs** — `lb_slug()` strips format markers, lowercases, removes non-alphanumerics. `get_lb_rating()` tries up to four candidate URLs (`/film/{slug}/`, then with current year and two prior years). Rating is parsed from the `twitter:data2` meta tag, with a JSON-LD fallback. 0.3s sleep between requests.

**HTTP retries** — `_get()` retries on 5xx, 429, and network errors with exponential backoff. 4xx errors (other than 429) raise immediately.

**Output** — `main()` prints a single JSON line to stdout: `{"subject": "...", "text": "..."}`. All progress logging goes to stderr.

## Failure modes

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `ERROR: AMC_VENDOR_KEY env var is not set` | Env var missing | Add it under routine environment settings |
| All AMC requests fail with timeout | Network not allowlisted | Add `api.amctheatres.com` to routine's network allowlist |
| AMC returns 401/403 | Vendor key invalid | Regenerate key, update env var |
| All Letterboxd ratings are N/A | Letterboxd HTML changed | Update `_LB_RATING_RE` and `_LB_LD_RE` |
| Some movies show N/A | Slug mismatch | Extend candidate list in `get_lb_rating` |
| No Gmail draft appears | Connector not enabled or auth lapsed | Re-authorize Gmail in routine config |

## Known fragility

- **Letterboxd** is unofficial scraping. If they restructure the page, all ratings silently go N/A.
- **AMC API** is stable in practice but undocumented publicly. Schema changes will surface as `KeyError`.
- **Routine sandbox is ephemeral** — each run starts fresh, no cross-run caching.
