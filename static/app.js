"use strict";

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/static/sw.js").catch(console.error);
}

const $ = id => document.getElementById(id);

// ---------------------------------------------------------------------------
// Day/time filter settings
// ---------------------------------------------------------------------------
const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const DAY_ORDER = [1, 2, 3, 4, 5, 6, 0]; // Mon..Sun display order, JS getDay() index
const FILTER_STORE_KEY = "dayFilters.v1";
const MOVIE_VISIBLE_KEY = "movie.visible.v1";
// Default to beginning of day through 11:59 PM; weekend/open mode uses the full day range.
const DEFAULT_RANGE = { start: "00:00", end: "23:59" };

// state[dow] = { mode: "open" | "filtered" | "rejected", start: "HH:MM", end: "HH:MM" }
function loadFilterState() {
  let state = {};
  try {
    state = JSON.parse(localStorage.getItem(FILTER_STORE_KEY) || "{}");
  } catch (err) {
    state = {};
  }
  for (const dow of DAY_ORDER) {
    if (!state[dow] || typeof state[dow] !== "object") {
      // Default weekdays (Mon-Fri) to 4 PM - 9 PM and mark as filtered.
      // Default weekend (Sat/Sun) to full day (00:00 - 23:59) and keep open.
      if (dow >= 1 && dow <= 5) {
        state[dow] = { mode: "filtered", start: "16:00", end: "21:00" };
      } else {
        state[dow] = { mode: "open", start: DEFAULT_RANGE.start, end: DEFAULT_RANGE.end };
      }
    }
  }
  return state;
}

function saveFilterState(state) {
  localStorage.setItem(FILTER_STORE_KEY, JSON.stringify(state));
}

function loadMovieVisibility() {
  try {
    return JSON.parse(localStorage.getItem(MOVIE_VISIBLE_KEY) || "{}");
  } catch (err) {
    return {};
  }
}

function saveMovieVisibility(map) {
  localStorage.setItem(MOVIE_VISIBLE_KEY, JSON.stringify(map));
}

function isMovieShowingsVisible(title) {
  const map = loadMovieVisibility();
  return map[title] !== 0;
}

let filterState = loadFilterState();
let activePopoverDow = null;

// -- Theatre picker: hide/show theatres client-side, persisted like the day
// filters. Chips are built from whatever theatres the schedule contains, so
// deployments with a different AMC_THEATRES set need no frontend changes.
const THEATRE_STORE_KEY = "theatreFilters.v1";

function loadTheatreState() {
  try {
    return JSON.parse(localStorage.getItem(THEATRE_STORE_KEY) || "{}");
  } catch (err) {
    return {};
  }
}

let theatreState = loadTheatreState(); // short name -> false when hidden

function isTheatreEnabled(short) {
  return theatreState[short] !== false;
}

function renderTheatreFilters(shorts) {
  const container = $("theatreFilters");
  container.replaceChildren();
  if (!shorts.length) return;
  const label = document.createElement("span");
  label.className = "theater-filter-label";
  label.textContent = "Theaters:";
  container.append(label);
  const toggle = (short, enabled) => {
    theatreState[short] = enabled;
    localStorage.setItem(THEATRE_STORE_KEY, JSON.stringify(theatreState));
    renderTheatreFilters(shorts);
    applyFilters();
  };
  for (const short of shorts.filter(isTheatreEnabled)) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "theatre-chip";
    chip.textContent = `${short} ×`;
    chip.setAttribute("aria-label", `Hide ${short} showtimes`);
    chip.addEventListener("click", () => toggle(short, false));
    container.append(chip);
  }
  const hidden = shorts.filter(short => !isTheatreEnabled(short));
  const picker = document.createElement("details");
  picker.className = "theater-add";
  const add = document.createElement("summary");
  add.textContent = "+";
  add.setAttribute("aria-label", "Add theater to view");
  picker.append(add);
  const menu = document.createElement("div");
  menu.className = "theater-add-menu";
  if (!hidden.length) {
    const note = document.createElement("p");
    note.textContent = "All included theaters are shown.";
    menu.append(note);
  }
  for (const short of hidden) {
    const item = document.createElement("button");
    item.type = "button";
    item.textContent = short;
    item.addEventListener("click", () => toggle(short, true));
    menu.append(item);
  }
  if ($("settingsBtn") && !$("settingsBtn").classList.contains("hidden")) {
    const manage = document.createElement("button");
    manage.type = "button";
    manage.textContent = "Manage refresh list…";
    manage.addEventListener("click", () => { picker.open = false; $("settingsBtn").click(); });
    menu.append(manage);
  }
  picker.append(menu);
  container.append(picker);
}

// ---------------------------------------------------------------------------
// Time-range popover (two-knob slider, opened from a day chip)
// ---------------------------------------------------------------------------
function timeOptions() {
  const opts = [];
  for (let h = 0; h < 24; h++) {
    for (const m of [0, 30]) {
      const hh = String(h).padStart(2, "0");
      const mm = String(m).padStart(2, "0");
      const value = `${hh}:${mm}`;
      const ampm = h >= 12 ? "PM" : "AM";
      const h12 = h % 12 || 12;
      const label = m === 0 ? `${h12} ${ampm}` : `${h12}:${mm} ${ampm}`;
      opts.push({ value, label });
    }
  }
  // Add the final minute of the day so the end slider can reach 11:59 PM.
  opts.push({ value: "23:59", label: "11:59 PM" });
  return opts;
}
const TIME_OPTIONS = timeOptions();
const TIME_INDEX_BY_VALUE = Object.fromEntries(TIME_OPTIONS.map((o, i) => [o.value, i]));
const TIME_LABEL_BY_VALUE = Object.fromEntries(TIME_OPTIONS.map(o => [o.value, o.label]));

function fmtRangeShort(start, end) {
  // Use the human-friendly labels from TIME_OPTIONS (e.g. "3 AM", "4:30 PM").
  const sLabel = TIME_LABEL_BY_VALUE[start] || start;
  const eLabel = TIME_LABEL_BY_VALUE[end] || end;
  return `${sLabel} – ${eLabel}`;
}

function renderDayFilters() {
  const container = $("dayFilters");
  container.innerHTML = DAY_ORDER.map(dow => {
    const entry = filterState[dow];
    const cls = entry.mode === "open" ? "" : entry.mode;
    const rangeLabel = entry.mode === "rejected"
      ? "Off"
      : fmtRangeShort(entry.start, entry.end);
    return `
      <div class="day-chip ${cls}" data-dow="${dow}">
        <button class="day-chip-main" type="button" data-dow="${dow}">${DAY_NAMES[dow]}<span class="day-chip-range">${rangeLabel}</span></button>
        <button class="day-chip-skip" type="button" data-dow="${dow}" aria-label="Skip ${DAY_NAMES[dow]}">✕</button>
      </div>
    `;
  }).join("");

  container.querySelectorAll(".day-chip-main").forEach(btn => {
    btn.addEventListener("click", () => handleDayChipTap(Number(btn.dataset.dow)));
  });
  container.querySelectorAll(".day-chip-skip").forEach(btn => {
    btn.addEventListener("click", () => toggleDaySkip(Number(btn.dataset.dow)));
  });
}

function handleDayChipTap(dow) {
  const entry = filterState[dow];
  entry.mode = "filtered";
  saveFilterState(filterState);
  renderDayFilters();
  openTimePopover(dow);
  applyFilters();
}

function toggleDaySkip(dow) {
  const entry = filterState[dow];
  entry.mode = entry.mode === "rejected" ? "open" : "rejected";
  saveFilterState(filterState);
  renderDayFilters();
  hideTimePopover();
  applyFilters();
}

function positionTimePopover() {
  const pop = $("timePopover");
  if (pop.classList.contains("hidden") || activePopoverDow === null) return;

  const chip = document.querySelector(`.day-chip[data-dow="${activePopoverDow}"]`);
  if (!chip) return;

  const rect = chip.getBoundingClientRect();
  const popWidth = Math.min(320, window.innerWidth - 24);
  const left = Math.min(Math.max(rect.left, 12), window.innerWidth - popWidth - 12);
  const top = Math.min(rect.bottom + 8, window.innerHeight - 96);

  pop.style.left = `${left}px`;
  pop.style.top = `${top}px`;
  pop.style.width = `${popWidth}px`;
}

function openTimePopover(dow) {
  const pop = $("timePopover");
  const entry = filterState[dow];
  activePopoverDow = dow;
  pop.querySelector(".time-popover-label").textContent = DAY_NAMES[dow];

  const startSel = pop.querySelector(".time-start");
  const endSel = pop.querySelector(".time-end");
  const startValue = pop.querySelector(".time-start-value");
  const endValue = pop.querySelector(".time-end-value");
  // Map stored start/end values to the nearest slider index. If the stored
  // value isn't in the 30-minute OPTIONS list (e.g. "23:59"), fall back
  // to the first/last index so the sliders remain usable.
  const startIndex = TIME_INDEX_BY_VALUE[entry.start] ?? 0;
  const endIndex = TIME_INDEX_BY_VALUE[entry.end] ?? (TIME_OPTIONS.length - 1);
  // Ensure slider range covers the full TIME_OPTIONS length.
  startSel.min = 0;
  endSel.min = 0;
  startSel.max = String(TIME_OPTIONS.length - 1);
  endSel.max = String(TIME_OPTIONS.length - 1);
  startSel.step = 1;
  endSel.step = 1;
  startSel.value = String(startIndex);
  endSel.value = String(endIndex);

  const updatePopoverValues = () => {
    const startIndex = Number(startSel.value);
    const endIndex = Number(endSel.value);
    startValue.textContent = TIME_OPTIONS[startIndex].label;
    endValue.textContent = TIME_OPTIONS[endIndex].label;
  };

  const onChange = () => {
    let s = Number(startSel.value), e = Number(endSel.value);
    if (s > e) {
      e = s;
      endSel.value = e;
    }
    updatePopoverValues();
    entry.start = TIME_OPTIONS[s].value;
    entry.end = TIME_OPTIONS[e].value;
    saveFilterState(filterState);
    renderDayFilters();
    applyFilters();
  };
  startSel.oninput = onChange;
  endSel.oninput = onChange;
  updatePopoverValues();

  pop.querySelector(".time-popover-done").onclick = hideTimePopover;
  pop.classList.remove("hidden");
  positionTimePopover();
}

function hideTimePopover() {
  activePopoverDow = null;
  $("timePopover").classList.add("hidden");
}

window.addEventListener("scroll", positionTimePopover, { passive: true });
window.addEventListener("resize", positionTimePopover);

// ---------------------------------------------------------------------------
// Client-side filtering (the server always returns full-day showtimes)
// ---------------------------------------------------------------------------
function dowFromDateStr(dateStr) {
  // dateStr is "YYYY-MM-DD"
  const [y, m, d] = dateStr.split("-").map(Number);
  return new Date(y, m - 1, d).getDay();
}

function timeInRange(t24, start, end) {
  // When start === end in filtered mode, treat that as an exact-time match.
  if (start === end) return t24 === start;
  // Normal non-wrapping interval
  if (start < end) return t24 >= start && t24 <= end;
  // Wrapping interval across midnight (e.g., 22:00 - 02:00)
  return t24 >= start || t24 <= end;
}

/**
 * Apply the current day/time filter settings to the rendered movie cards:
 * hide individual time chips, hide a showing-row/venue-block when empty,
 * and hide a whole movie card when nothing remains visible.
 */
function applyFilters() {
  document.querySelectorAll(".movie").forEach(movieEl => {
    let anyVisibleRow = false;

    movieEl.querySelectorAll(".showing-row").forEach(rowEl => {
      let anyVisibleVenue = false;

      rowEl.querySelectorAll(".venue-block").forEach(venueEl => {
        const dow = Number(venueEl.dataset.dow);
        const entry = filterState[dow];
        let venueVisible = false;

        if (!isTheatreEnabled(venueEl.dataset.theatre)) {
          venueEl.querySelectorAll(".time-chip").forEach(chip => chip.classList.add("hidden"));
        } else if (entry.mode !== "rejected") {
          venueEl.querySelectorAll(".time-chip").forEach(chip => {
            const t24 = chip.dataset.t24;
            const visible = entry.mode === "open" || timeInRange(t24, entry.start, entry.end);
            chip.classList.toggle("hidden", !visible);
            if (visible) venueVisible = true;
          });
        } else {
          venueEl.querySelectorAll(".time-chip").forEach(chip => chip.classList.add("hidden"));
        }

        venueEl.classList.toggle("hidden", !venueVisible);
        if (venueVisible) anyVisibleVenue = true;
      });

      rowEl.classList.toggle("hidden", !anyVisibleVenue);
      if (anyVisibleVenue) anyVisibleRow = true;
    });

    movieEl.classList.toggle("hidden", !anyVisibleRow);
  });
}

// ---------------------------------------------------------------------------
// Movie cards
// ---------------------------------------------------------------------------
function renderMovie(movie) {
  // Letterboxd badge display rules:
  // - If we have a Letterboxd page (`lb_url`) show "Letterboxd: ★ <rating>".
  // - If we have a page but rating is N/A show "Letterboxd: ★ N/A".
  // - If we don't have a Letterboxd page at all, just display plain "N/A".
  let rating = "";
  if (movie.lb_url) {
    const text = movie.lb_rating && movie.lb_rating !== "N/A"
      ? `Letterboxd: <span class="star">★</span> ${escapeHtml(movie.lb_rating)}`
      : `Letterboxd: <span class="star">★</span> N/A`;
    rating = `<a class="lb-link" href="${safeUrl(movie.lb_url)}" target="_blank" rel="noopener"><span class="lb-badge ${movie.lb_rating === "N/A" ? "lb-na" : ""}">${text}</span></a>`;
  } else {
    rating = `<span class="lb-badge lb-na">N/A</span>`;
  }

  const synopsis = movie.synopsis
    ? `<p class="synopsis">${escapeHtml(movie.synopsis.length > 280 ? movie.synopsis.slice(0, 277) + "…" : movie.synopsis)}</p>`
    : "";

  const creditParts = [];
  if (movie.director) creditParts.push(`<span class="credit-label">Dir.</span> ${escapeHtml(movie.director)}`);
  if (movie.cast) creditParts.push(`<span class="credit-label">Cast</span> ${escapeHtml(movie.cast)}`);
  const credits = creditParts.length
    ? `<p class="credits">${creditParts.join('<span class="credit-sep">·</span>')}</p>`
    : "";

  const poster = movie.poster
    ? `<img class="poster" src="${safeUrl(movie.poster)}" alt="" loading="lazy">`
    : `<div class="poster poster-placeholder" aria-hidden="true">🎬</div>`;

  const showingsVisible = isMovieShowingsVisible(movie.title);

  // Group showings by date so both theatres share one row per day.
  const byDate = new Map();
  for (const s of movie.showings) {
    if (!byDate.has(s.date)) byDate.set(s.date, { label: s.date_label, items: [] });
    byDate.get(s.date).items.push(s);
  }

  const rows = [...byDate.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([, g]) => {
      const dow = dowFromDateStr(g.items[0].date);
      const venues = g.items.map(s => {
        const fmt = s.format && s.format !== "Standard"
          ? `<span class="format-tag">${escapeHtml(s.format.replace(" at AMC", ""))}</span>`
          : "";
        const times24 = s.times24 || [];
        const times = s.times.map((t, i) => `<span class="time-chip" data-t24="${times24[i] || ""}">${escapeHtml(t)}</span>`).join("");
        return `
          <div class="venue-block" data-fill-key="${escapeHtml(s.fill_key)}" data-dow="${dow}" data-theatre="${escapeHtml(s.theatre_short)}">
            <div>
              <div class="showing-venue">${escapeHtml(s.theatre_short)}${fmt}</div>
              <div class="times">${times}</div>
            </div>
          </div>`;
      }).join("");
      return `
        <div class="showing-row">
          <div class="showing-date">${escapeHtml(g.label)}</div>
          <div class="venue-list">${venues}</div>
        </div>`;
    }).join("");

  return `
    <div class="movie" data-movie-title="${escapeHtml(movie.title)}">
      <div class="movie-header">
        ${poster}
        <div class="movie-info">
          <div class="movie-title-row">
            <span class="movie-title">${escapeHtml(movie.title)}</span>
            ${rating}
          </div>
          ${credits}
          ${synopsis}
        </div>
      </div>
      <div class="showings-header">
        <button class="movie-toggle${showingsVisible ? ' expanded' : ''}" type="button" aria-expanded="${showingsVisible ? 'true' : 'false'}" aria-label="${showingsVisible ? 'Hide showtimes' : 'Show showtimes'}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="8 6 14 12 8 18"/>
          </svg>
          <span>Showtimes</span>
        </button>
      </div>
      <div class="showings${showingsVisible ? '' : ' hidden'}">${rows}</div>
    </div>`;
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? escapeHtml(url.href) : "";
  } catch (_) { return ""; }
}

// Simple HTML escaper for attribute safety when injecting movie titles
function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function setSubtitle(text) {
  $("subtitle").textContent = text;
}

function setLoadingText(text) {
  const p = $("loading").querySelector("p");
  if (p) p.textContent = text;
}

function setLoadingProgress(progress) {
  const bar = $("loading").querySelector(".progress-bar-fill");
  if (bar) bar.style.width = progress + "%";
}

function attachMovieToggleHandlers() {
  document.querySelectorAll('.movie-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
      const movieEl = btn.closest('.movie');
      if (!movieEl) return;
      const title = movieEl.dataset.movieTitle;
      const map = loadMovieVisibility();
      const visible = btn.classList.toggle('expanded');
      btn.setAttribute('aria-expanded', visible ? 'true' : 'false');
      btn.setAttribute('aria-label', visible ? 'Hide showtimes' : 'Show showtimes');
      const showings = movieEl.querySelector('.showings');
      if (showings) showings.classList.toggle('hidden', !visible);
      if (visible) {
        map[title] = 1;
      } else {
        map[title] = 0;
      }
      saveMovieVisibility(map);
    });
  });
}

// ---------------------------------------------------------------------------
// Data loading (schedule, build-progress polling, seat status)
// ---------------------------------------------------------------------------
async function pollStatus() {
  /**
   * Poll /api/status while the initial schedule load is in progress.
   * Continue polling until status returns "done" or after a max timeout.
   */
  const maxPolls = 120; // 60 sec at 500ms intervals
  let pollCount = 0;

  while (pollCount < maxPolls) {
    try {
      const res = await fetch("/api/status");
      const data = await res.json();
      if (data.ok) {
        const msg = data.detail || {
          "amc": "Fetching AMC schedule…",
          "letterboxd": "Getting ratings…",
          "done": "Processing…",
          "idle": "Loading…",
        }[data.stage] || "Loading…";
        setLoadingText(msg);
        setLoadingProgress(data.progress);

        // Stop polling if build is complete
        if (data.stage === "done") {
          return;
        }
      }
    } catch (err) {
      // Silently ignore status poll errors; main load will handle the real error.
    }
    await new Promise(r => setTimeout(r, 500));
    pollCount++;
  }
}

const SEAT_STATUS_CLASSES = ["status-open", "status-almost", "status-sold_out"];

async function loadFills(movieCount, ts) {
  setSubtitle(`${movieCount} movies · updated ${ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · loading seat status…`);
  try {
    const res = await fetch("/api/fills");
    const data = await res.json();
    if (!data.ok) return;

    for (const [key, statuses] of Object.entries(data.fills)) {
      const venue = document.querySelector(`.venue-block[data-fill-key="${CSS.escape(key)}"]`);
      if (!venue) continue;
      venue.querySelectorAll(".time-chip").forEach(chip => {
        const status = statuses[chip.dataset.t24];
        chip.classList.remove(...SEAT_STATUS_CLASSES);
        if (status) chip.classList.add(`status-${status}`);
      });
    }
  } catch (err) {
    console.error("Failed to load seat fills:", err);
  } finally {
    setSubtitle(`${movieCount} movies · updated ${ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`);
  }
}

async function load(forceRefresh = false) {
  const btn = $("refreshBtn");
  btn.classList.add("spinning");
  $("loading").classList.remove("hidden");
  $("error").classList.add("hidden");
  $("movies").innerHTML = "";

  try {
    if (forceRefresh) {
      setLoadingText("Refreshing schedule…");
      setSubtitle("Refreshing…");
      setLoadingProgress(0);
      // Start progress polling BEFORE awaiting the refresh: /api/refresh
      // rebuilds synchronously (30-60s when server caches are cold), and
      // the polls are what keep the loading UI alive during it.
      pollStatus();
      await fetch("/api/refresh", { method: "POST" });
    } else {
      setLoadingText("Fetching showtimes…");
      setSubtitle("Loading…");
      setLoadingProgress(0);
      // Fire-and-forget: poll /api/status for progress text while the main
      // fetch is in flight; it stops on its own once the build reports "done".
      pollStatus();
    }

    const res = await fetch("/api/showtimes");
    const data = await res.json();

    if (!data.ok) throw new Error(data.error || "Unknown error");

    const movies = data.movies;
    if (!movies.length) {
      $("movies").innerHTML = `<div class="empty">No showtimes found for the next 7 days.</div>`;
    } else {
      $("movies").innerHTML = movies.map(renderMovie).join("");
      renderTheatreFilters([...new Set(
        movies.flatMap(m => m.showings.map(s => s.theatre_short))
      )].sort());
      applyFilters();
      attachMovieToggleHandlers();
    }

    const ts = new Date(data.cached_at * 1000);

    if (movies.length) {
      loadFills(movies.length, ts);
    } else {
      setSubtitle(`0 movies · updated ${ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`);
    }
  } catch (err) {
    $("error").textContent = `Failed to load: ${err.message}`;
    $("error").classList.remove("hidden");
    setSubtitle("Error loading showtimes");
  } finally {
    $("loading").classList.add("hidden");
    btn.classList.remove("spinning");
  }
}

renderDayFilters();

