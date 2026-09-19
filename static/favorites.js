"use strict";
let ownerSession = {owner: false, csrf: ""};
let favoriteTheaters = [];
let draftFavorites = [];
let searchResults = [];
let browsingTheater = null;
let loadGeneration = 0;
let visibleMovies = [];

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options, headers: {"Content-Type": "application/json", "X-CSRF-Token": ownerSession.csrf, ...options.headers}
  });
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(result.error || "Request failed");
  return result;
}

function settingsMessage(message) { $("settingsStatus").textContent = message; }
function renderSettings() {
  $("ownerLogin").classList.toggle("hidden", ownerSession.owner);
  $("ownerControls").classList.toggle("hidden", !ownerSession.owner);
  const renderRows = (target, rows) => {
    $(target).replaceChildren();
    for (const theater of rows) {
      const row = document.createElement("div");
      row.className = "theater-option";
      const browse = document.createElement("button");
      browse.type = "button";
      browse.textContent = theater.name + (theater.city ? ` · ${theater.city}` : "");
      browse.addEventListener("click", () => { browsingTheater = theater.id; loadVercel(); });
      row.append(browse);
      const selected = draftFavorites.some(t => t.id === theater.id);
      const star = document.createElement(ownerSession.owner ? "button" : "span");
      star.textContent = selected ? "★" : "☆";
      if (ownerSession.owner) {
        star.type = "button";
        star.setAttribute("aria-label", `${selected ? "Remove" : "Favorite"} ${theater.name}`);
        star.setAttribute("aria-pressed", String(selected));
        star.addEventListener("click", () => {
          if (selected) draftFavorites = draftFavorites.filter(t => t.id !== theater.id);
          else if (draftFavorites.length < 3) draftFavorites.push(theater);
          else { settingsMessage("Choose up to three favorites."); return; }
          settingsMessage("Unsaved changes — select Save favorites when ready.");
          renderSettings();
        });
      } else {
        star.setAttribute("aria-label", selected ? "Favorite" : "Not a favorite");
      }
      row.append(star);
      $(target).append(row);
    }
  };
  renderRows("favoriteList", draftFavorites);
  renderRows("theaterResults", searchResults);
}

function mergeVisible(movies) {
  const grouped = new Map();
  for (const m of movies) {
    const key = m.id || m.title;
    if (!grouped.has(key)) grouped.set(key, {...m, showings: []});
    grouped.get(key).showings.push(...m.showings);
  }
  return [...grouped.values()].sort((a, b) => (parseFloat(b.lb_rating) || 0) - (parseFloat(a.lb_rating) || 0) || a.title.localeCompare(b.title));
}

function renderVisible() {
  visibleMovies = mergeVisible(visibleMovies);
  $("movies").innerHTML = visibleMovies.length ? visibleMovies.map(renderMovie).join("") : '<div class="empty">No cached showtimes yet.</div>';
  renderTheatreFilters([...new Set(visibleMovies.flatMap(m => m.showings.map(s => s.theatre_short)))].sort());
  attachMovieToggleHandlers();
  applyFilters();
  // Availability is from the displayed snapshot, not advertised as live.
  for (const movie of visibleMovies) for (const showing of movie.showings) {
    const venue = document.querySelector(`.venue-block[data-fill-key="${CSS.escape(showing.fill_key)}"]`);
    if (!venue) continue;
    venue.querySelectorAll('.time-chip').forEach(chip => {
      const status = showing.statuses?.[chip.dataset.t24];
      if (["open", "almost", "sold_out"].includes(status)) chip.classList.add(`status-${status}`);
    });
  }
}

async function loadVercel(force = false) {
  const generation = ++loadGeneration;
  $("refreshBtn").disabled = true;
  $("error").classList.add("hidden");
  $("loading").classList.remove("hidden");
  setLoadingText("Loading saved showtimes…");
  try {
    const data = await api('/api/showtimes' + (browsingTheater ? `?theater=${browsingTheater}` : ''));
    if (generation !== loadGeneration) return;
    visibleMovies = data.movies;
    renderVisible();
    let jobs = data.pending;
    if (force && ownerSession.owner) jobs = data.dates.flatMap(date => data.theaters.map(t => ({date, theater: t.id, name: t.name})));
    const warnings = [];
    let newest = data.cached_at;
    for (const [index, job] of jobs.entries()) {
      if (generation !== loadGeneration) return;
      setLoadingText(`Loading ${job.name} · ${job.date} (${index + 1}/${jobs.length})…`);
      setLoadingProgress(100 * index / jobs.length);
      try {
        const result = await api('/api/theater-day', {method: 'POST', body: JSON.stringify({...job, refresh: force && ownerSession.owner})});
        if (generation !== loadGeneration) return;
        if (result.warning) warnings.push(result.warning);
        if (result.cached_at) {
          visibleMovies = visibleMovies.map(m => ({...m, showings: m.showings.filter(s => !(s.date === job.date && s.theatre === job.name))})).filter(m => m.showings.length);
          visibleMovies.push(...result.movies);
          newest = Math.max(newest, result.cached_at);
          renderVisible();
        }
      } catch (err) { warnings.push(err.message); }
    }
    if (!visibleMovies.length) $("movies").innerHTML = `<div class="empty">${warnings.length ? "Showtimes could not be loaded. Try again in a few minutes." : (data.theaters.length ? "No showtimes listed for the next seven days." : "No favorites selected. Find a theater above to browse.")}</div>`;
    if (warnings.length) {
      $("error").textContent = "Some dates could not refresh. Saved results remain visible. Retry in a few minutes.";
      $("error").classList.remove("hidden");
    }
    const stamps = visibleMovies.flatMap(m => m.showings.map(s => s.cached_at)).filter(Boolean);
    const oldest = stamps.length ? Math.min(...stamps) : newest;
    const stamp = oldest ? new Date(oldest * 1000).toLocaleString([], {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}) : "not yet loaded";
    setSubtitle(`${visibleMovies.length} movies · oldest data ${stamp} · availability may have changed`);
  } catch (err) {
    $("error").textContent = err.message;
    $("error").classList.remove("hidden");
  } finally {
    if (generation === loadGeneration) {
      $("loading").classList.add("hidden");
      $("refreshBtn").disabled = false;
    }
  }
}

async function startShowtimes() {
  let health;
  try { health = await api('/api/health'); } catch (_) { /* Existing Cloud Run backend. */ }
  if (health?.mode !== 'vercel') {
    $("refreshBtn").addEventListener("click", () => load(true));
    load();
    return;
  }
  $("theaterSettings").classList.remove("hidden");
  $("refreshBtn").addEventListener("click", () => loadVercel(true));
  $("showFavorites").addEventListener("click", () => { browsingTheater = null; loadVercel(); });
  $("theaterSearchForm").addEventListener("submit", async e => {
    e.preventDefault();
    settingsMessage("Finding theaters…");
    try {
      searchResults = (await api('/api/theatres?q=' + encodeURIComponent($("theaterSearch").value))).theaters;
      renderSettings();
      settingsMessage(searchResults.length ? "Select a theater to browse." : "No theaters found.");
    } catch (err) { settingsMessage(err.message); }
  });
  $("ownerLogin").addEventListener("submit", async e => {
    e.preventDefault();
    const key = $("ownerKey").value;
    $("ownerKey").value = "";
    try {
      const data = await api('/api/login', {method: 'POST', body: JSON.stringify({key})});
      ownerSession = {owner: true, csrf: data.csrf};
      renderSettings(); settingsMessage("Favorites unlocked.");
    } catch (err) { settingsMessage(err.message); }
  });
  $("ownerLogout").addEventListener("click", async () => {
    try {
      await api('/api/logout', {method: 'POST'});
      ownerSession = {owner: false, csrf: ''};
      draftFavorites = [...favoriteTheaters];
      renderSettings(); settingsMessage("Signed out.");
    } catch (err) { settingsMessage(err.message); }
  });
  $("saveFavorites").addEventListener("click", async () => {
    $("saveFavorites").disabled = true;
    try {
      const data = await api('/api/favorites', {method: 'PUT', body: JSON.stringify({ids: draftFavorites.map(t => t.id)})});
      favoriteTheaters = data.theaters; draftFavorites = [...favoriteTheaters];
      renderSettings(); settingsMessage("Favorites saved. Loading their showtimes now.");
      browsingTheater = null;
      await loadVercel();
    } catch (err) { settingsMessage(err.message); }
    finally { $("saveFavorites").disabled = false; }
  });
  try {
    ownerSession = await api('/api/session');
    const data = await api('/api/favorites');
    favoriteTheaters = data.theaters; draftFavorites = [...favoriteTheaters];
    renderSettings();
    await loadVercel();
  } catch (err) {
    settingsMessage(err.message);
    $("loading").classList.add("hidden");
  }
}
startShowtimes();
