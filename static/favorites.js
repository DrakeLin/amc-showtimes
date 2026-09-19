"use strict";
let favoriteTheaters = [];
let draftFavorites = [];
let searchResults = [];
let browsingTheater = null;
let loadGeneration = 0;
let visibleMovies = [];

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options, headers: {"Content-Type": "application/json", ...options.headers}
  });
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(result.error || "Request failed");
  return result;
}

function settingsMessage(message) { $("settingsStatus").textContent = message; }
function renderSettings() {
  const renderRows = (target, rows) => {
    $(target).replaceChildren();
    for (const theater of rows) {
      const row = document.createElement("div");
      row.className = "theater-option";
      const browse = document.createElement("span");
      browse.textContent = theater.name + (theater.city ? ` · ${theater.city}` : "");
      row.append(browse);
      const selected = draftFavorites.some(t => t.id === theater.id);
      const star = document.createElement("button");
      star.textContent = selected ? "×" : "+";
      {
        star.type = "button";
        star.setAttribute("aria-label", `${selected ? "Remove" : "Add"} ${theater.name}`);
        star.setAttribute("aria-pressed", String(selected));
        star.addEventListener("click", () => {
          if (selected) draftFavorites = draftFavorites.filter(t => t.id !== theater.id);
          else if (draftFavorites.length < 10) draftFavorites.push(theater);
          else { settingsMessage("Choose up to 10 theaters."); return; }
          settingsMessage("Unsaved changes. Save theaters to update the refresh list.");
          renderSettings();
        });
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
    const pendingIds = new Set(data.pending.map(job => job.theater));
    const jobs = data.theaters.filter(t => force || pendingIds.has(t.id));
    const metadataIds = new Set(data.metadata_pending || []);
    const warnings = [];
    let newest = data.cached_at;
    for (const [index, job] of jobs.entries()) {
      if (generation !== loadGeneration) return;
      setLoadingText(`Loading ${job.name} (${index + 1}/${jobs.length})…`);
      setLoadingProgress(100 * index / jobs.length);
      try {
        const result = await api('/api/theater-week', {method: 'POST', body: JSON.stringify({theater: job.id, refresh: force})});
        if (generation !== loadGeneration) return;
        if (result.warning) warnings.push(result.warning);
        if (result.metadata_pending) metadataIds.add(job.id);
        if (result.cached_at) {
          visibleMovies = visibleMovies.map(m => ({...m, showings: m.showings.filter(s => s.theatre !== job.name)})).filter(m => m.showings.length);
          visibleMovies.push(...result.movies);
          newest = Math.max(newest, result.cached_at);
          renderVisible();
        }
      } catch (err) { warnings.push(err.message); }
    }
    // Show schedules first, then progressively fill posters and verified ratings.
    for (const theater of data.theaters.filter(t => metadataIds.has(t.id))) {
      for (let batch = 0; batch < 30; batch++) {
        if (generation !== loadGeneration) return;
        setLoadingText(`Loading posters & ratings · ${theater.name}…`);
        try {
          const result = await api('/api/metadata', {method: 'POST', body: JSON.stringify({theater: theater.id})});
          if (generation !== loadGeneration) return;
          visibleMovies = visibleMovies.map(movie => ({...movie, ...(result.metadata[movie.id] || {})}));
          renderVisible();
          if (!result.pending) break;
          if (result.retry_after) {
            // Another visitor is populating the same cache. Keep this page usable.
            if (batch >= 5) break;
            await new Promise(resolve => setTimeout(resolve, result.retry_after * 1000));
          }
        } catch (_) {
          $("error").textContent = "Some posters or ratings could not load. Refresh to retry; showtimes are still available.";
          $("error").classList.remove("hidden");
          break;
        }
      }
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
  $("settingsBtn").classList.remove("hidden");
  $("settingsBtn").addEventListener("click", () => { renderSettings(); $("theaterSettings").showModal(); });
  $("closeSettings").addEventListener("click", () => $("theaterSettings").close());
  $("refreshBtn").addEventListener("click", () => loadVercel(true));
  $("theaterSearchForm").addEventListener("submit", async e => {
    e.preventDefault();
    settingsMessage("Finding theaters…");
    try {
      searchResults = (await api('/api/theatres?q=' + encodeURIComponent($("theaterSearch").value))).theaters;
      renderSettings();
      settingsMessage(searchResults.length ? "Use + to include a theater." : "No theaters found.");
    } catch (err) { settingsMessage(err.message); }
  });
  $("saveFavorites").addEventListener("click", async () => {
    $("saveFavorites").disabled = true;
    try {
      const data = await api('/api/favorites', {method: 'PUT', body: JSON.stringify({ids: draftFavorites.map(t => t.id)})});
      favoriteTheaters = data.theaters; draftFavorites = [...favoriteTheaters];
      renderSettings(); settingsMessage("Theaters saved. Loading showtimes.");
      browsingTheater = null;
      $("theaterSettings").close();
      await loadVercel();
    } catch (err) { settingsMessage(err.message); }
    finally { $("saveFavorites").disabled = false; }
  });
  try {
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
