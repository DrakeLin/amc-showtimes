# Showtimes deployment — 2026-09-19

Production: https://showtimes-one.vercel.app/

- Vercel Hobby project: `drakelin18-5415/showtimes`.
- GitHub repository connected: `DrakeLin/amc-showtimes`; production branch: `main`.
- PR #18 merged as `78d4b233e9911c01ac06bed36e865c0a10d5a450` and deployed successfully.
- Private Blob connected; AMC_VENDOR_KEY, BLOB_READ_WRITE_TOKEN and CRON_SECRET configured.
- No owner key or login is needed. All visitors can edit the shared refresh list.
- Default selection remains AMC NewPark 12 (456) and AMC Mercado 20 (447).
- Live verification: schedules, posters and Letterboxd ratings load; real poster images render.
- Settings search, adding/removing a draft theater, public save, and main-screen ×/+ filters verified in the browser.
- Shared save verified through the API without authentication, keeping the two selected theaters unchanged.
- 78 offline backend tests pass; JavaScript syntax checks pass.
- Scheduled route is protected and configured for 12:00 UTC daily; the actual next scheduled execution has not yet been observed.
- Existing Google services remain untouched.

See README.md for architecture, setup, cache limits and operations.
