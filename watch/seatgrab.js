// Usage: node seatgrab.js <showtimeId> [showtimeId...]
// For each AMC showtime id, loads the amctheatres.com seat picker headlessly
// and prints a JSON array: {id, total, available, middle} per showtime.
// "middle" = available non-wheelchair seats in the center cell of a 3x3 grid
// over the auditorium: middle third of rows front-to-back, middle third of
// each row's seat numbers side-to-side.
//
// Needs playwright-core (npm install --no-save playwright-core) and a
// Chromium binary (PW_CHROMIUM, default /opt/pw-browsers/chromium).
// When HTTPS_PROXY is set (Claude Code remote sessions), Chromium is forced
// to TLS1.2/HTTP1.1 — the MITM egress proxy resets TLS1.3 ClientHellos —
// and the proxy CA must be in the NSS store (odyssey_watch.sh handles that).
const { chromium } = require('playwright-core');

// BACKOFF=<seconds> (default 300): how long to wait before retrying a 429'd
// showtime, twice max. Unattended runs want the patient default; set BACKOFF=0
// on interactive runs to fail fast instead of sitting in multi-minute sleeps.
const BACKOFF_MS = (process.env.BACKOFF === undefined ? 300 : +process.env.BACKOFF) * 1000;

(async () => {
  const ids = process.argv.slice(2);
  const proxied = !!process.env.HTTPS_PROXY;
  const browser = await chromium.launch({
    executablePath: process.env.PW_CHROMIUM || '/opt/pw-browsers/chromium',
    headless: true,
    proxy: proxied ? { server: process.env.HTTPS_PROXY } : undefined,
    args: ['--no-sandbox', ...(proxied ? ['--ssl-version-max=tls1.2', '--disable-http2'] : [])],
  });
  const ctx = await browser.newContext();
  const out = [];
  for (const id of ids) {
    const page = await ctx.newPage();
    try {
      // amctheatres.com rate-limits page loads (429), which serves a shell
      // page whose seat map never renders. The block is sticky (minutes, not
      // seconds — observed still limited after a 5 min quiet period), so back
      // off long between attempts; anything else that leaves the map missing
      // is a real failure.
      let data;
      for (let attempt = 0; ; attempt++) {
        const resp = await page.goto(`https://www.amctheatres.com/showtimes/${id}/seats`, { waitUntil: 'load', timeout: 90000 });
        if (resp && resp.status() === 429 && attempt < 2 && BACKOFF_MS > 0) {
          console.error(`429 ${id}: backing off ${BACKOFF_MS / 1000}s (attempt ${attempt + 1})`);
          await page.waitForTimeout(BACKOFF_MS);
          continue;
        }
        if (resp && resp.status() === 429) throw new Error('rate limited (429)');
        await page.waitForSelector('[aria-label="Seat Selection Map"] input', { timeout: 30000 });
        data = await page.evaluate(() =>
          [...document.querySelectorAll('[aria-label="Seat Selection Map"] input')].map(i => ({
            name: i.name,
            disabled: i.disabled,
            label: i.getAttribute('aria-label') || '',
          })));
        break;
      }
      const rows = {};
      for (const s of data) {
        const m = s.name.match(/^([A-Z]+)(\d+)$/);
        if (!m) continue;
        (rows[m[1]] ||= []).push({ n: +m[2], free: !s.disabled, label: s.label });
      }
      const rowNames = Object.keys(rows); // DOM order: front row first
      const middle = [];
      rowNames.forEach((r, idx) => {
        const depth = rowNames.length > 1 ? idx / (rowNames.length - 1) : 0.5;
        if (depth < 1 / 3 || depth > 2 / 3) return;
        const nums = rows[r].map(s => s.n);
        const lo = Math.min(...nums), hi = Math.max(...nums);
        const c1 = lo + (hi - lo) / 3, c2 = hi - (hi - lo) / 3;
        for (const s of rows[r]) {
          if (s.free && s.n >= c1 && s.n <= c2 && !/wheelchair/i.test(s.label)) middle.push(r + s.n);
        }
      });
      out.push({ id, total: data.length, available: data.filter(s => !s.disabled).map(s => s.name), middle });
      console.error(`ok ${id}: ${data.filter(s => !s.disabled).length}/${data.length} free, middle: ${middle.length}`);
    } catch (e) {
      out.push({ id, error: e.message.split('\n')[0] });
      console.error(`err ${id}: ${e.message.split('\n')[0]}`);
    }
    await page.close();
    // Pace consecutive loads so a multi-showtime run doesn't trip the limiter.
    if (id !== ids[ids.length - 1]) await new Promise(r => setTimeout(r, 15000));
  }
  console.log(JSON.stringify(out));
  await browser.close();
})();
