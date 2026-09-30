// Run the real filters against a small DOM and a controllable device clock.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let current = '2026-09-30T01:05:00';
class Clock extends Date {
  constructor(...args) { super(...(args.length ? args : [current])); }
}
const classes = () => {
  const values = new Set();
  return {add: x => values.add(x), toggle: (x, on) => on ? values.add(x) : values.delete(x), contains: x => values.has(x)};
};
const chip = {dataset: {t24: '00:30'}, classList: classes()};
const venue = {dataset: {date: '2026-09-30', dow: '3', theatre: 'Lincoln'}, classList: classes(), querySelectorAll: () => [chip]};
const row = {classList: classes(), querySelectorAll: () => [venue]};
const movie = {classList: classes(), querySelectorAll: () => [row]};
const empty = {classList: classes()};
const events = {};
const context = vm.createContext({Date: Clock, console, navigator: {},
  localStorage: {getItem: () => null},
  window: {addEventListener: (name, fn) => { events[name] = fn; }},
  document: {getElementById: id => id === 'filteredEmpty' ? empty : null,
    querySelectorAll: () => [movie], querySelector: () => movie,
    addEventListener: (name, fn) => { events[name] = fn; }} });
vm.runInContext(fs.readFileSync('static/app.js', 'utf8').replace(/renderDayFilters\(\);\s*$/, ''), context);
const evaluate = code => vm.runInContext(code, context);
assert.equal(evaluate('browserDate()'), '2026-09-30');
assert.equal(evaluate("isUpcomingShowing('2026-09-29', '23:59')"), false);
assert.equal(evaluate("isUpcomingShowing('2026-09-30', '00:30')"), false);
assert.equal(evaluate("isUpcomingShowing('2026-09-30', '01:05')"), false);
assert.equal(evaluate("isUpcomingShowing('2026-09-30', '01:06')"), true);
assert.equal(evaluate("isUpcomingShowing('2026-10-06', '23:59')"), true);
assert.equal(evaluate("isUpcomingShowing('2026-10-07', '00:00')"), false);
evaluate("filterState[3].mode = 'open'; applyFilters()");
for (const element of [chip, venue, row, movie]) assert.ok(element.classList.contains('hidden'));
assert.equal(empty.classList.contains('hidden'), false);
chip.dataset.t24 = '01:06';
evaluate('applyFilters()');
for (const element of [chip, venue, row, movie]) assert.equal(element.classList.contains('hidden'), false);
current = '2026-09-30T01:06:00';
evaluate('applyFilters()');
assert.ok(movie.classList.contains('hidden'));
current = '2026-10-01T00:00:00';
chip.dataset.t24 = '23:59';
evaluate('applyFilters()');
assert.ok(movie.classList.contains('hidden'));
// Calendar windows span seven dates even across the DST fallback.
current = '2026-11-01T00:00:00';
assert.equal(evaluate("isUpcomingShowing('2026-11-07', '23:59')"), true);
assert.equal(evaluate("isUpcomingShowing('2026-11-08', '00:00')"), false);
let reloads = 0, tick;
context.setInterval = fn => { tick = fn; };
vm.runInContext(fs.readFileSync('static/favorites.js', 'utf8').replace(/startShowtimes\(\);\s*$/, ''), context);
context.reload = () => reloads++;
evaluate('vercelMode = true; loadVercel = reload');
current = '2026-11-02T00:00:00';
tick(); tick();
assert.equal(reloads, 1);
current = '2026-11-03T12:00:00';
events.visibilitychange();
assert.equal(reloads, 2);
console.log(`Browser clock, expiry, midnight and DST passed (${process.env.TZ})`);
