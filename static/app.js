"use strict";

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/static/sw.js").catch(console.error);
}

const $ = id => document.getElementById(id);

function fillClass(pct) {
  if (pct === null) return "";
  if (pct < 50) return "fill-low";
  if (pct < 80) return "fill-med";
  return "fill-high";
}

function renderFill(pct) {
  if (pct === null || pct === undefined) {
    return `<div class="fill-meter fill-pending"><span class="fill-pct">…</span></div>`;
  }
  const cls = fillClass(pct);
  return `
    <div class="fill-meter ${cls}">
      <span class="fill-pct">${pct}%</span>
      <div class="fill-bar-track">
        <div class="fill-bar-fill" style="width:${pct}%"></div>
      </div>
    </div>`;
}

function renderMovie(movie) {
  const rating = movie.lb_rating !== "N/A"
    ? `<span class="lb-badge"><span class="star">★</span> ${movie.lb_rating}</span>`
    : `<span class="lb-badge lb-na">N/A</span>`;

  const synopsis = movie.synopsis
    ? `<p class="synopsis">${movie.synopsis.length > 180 ? movie.synopsis.slice(0, 177) + "…" : movie.synopsis}</p>`
    : "";

  const rows = movie.showings.map(s => {
    const fmt = s.format && s.format !== "Standard"
      ? `<span class="format-tag">${s.format.replace(" at AMC", "")}</span>`
      : "";
    const times = s.times.map(t => `<span class="time-chip">${t}</span>`).join("");
    return `
      <div class="showing-row" data-fill-key="${s.fill_key}">
        <div class="showing-date">${s.date_label}</div>
        <div>
          <div class="showing-venue">${s.theatre_short}${fmt}</div>
          <div class="times">${times}</div>
        </div>
        ${renderFill(undefined)}
      </div>`;
  }).join("");

  return `
    <div class="movie">
      <div class="movie-header">
        <div class="movie-title-row">
          <span class="movie-title">${movie.title}</span>
          ${rating}
        </div>
        ${synopsis}
      </div>
      <div class="showings">${rows}</div>
    </div>`;
}

function setSubtitle(text) {
  $("subtitle").textContent = text;
}

async function loadFills() {
  try {
    const res = await fetch("/api/fills");
    const data = await res.json();
    if (!data.ok) return;

    for (const [key, pct] of Object.entries(data.fills)) {
      const row = document.querySelector(`.showing-row[data-fill-key="${CSS.escape(key)}"]`);
      if (!row) continue;
      const meter = row.querySelector(".fill-meter");
      if (meter) meter.outerHTML = renderFill(pct);
    }
  } catch (err) {
    console.error("Failed to load seat fills:", err);
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
      await fetch("/api/refresh", { method: "POST" });
    }

    const res = await fetch("/api/showtimes");
    const data = await res.json();

    if (!data.ok) throw new Error(data.error || "Unknown error");

    const movies = data.movies;
    if (!movies.length) {
      $("movies").innerHTML = `<div class="empty">No evening showtimes found for the next 7 days.</div>`;
    } else {
      $("movies").innerHTML = movies.map(renderMovie).join("");
    }

    const ts = new Date(data.cached_at * 1000);
    setSubtitle(`${movies.length} movies · updated ${ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`);

    if (movies.length) loadFills();
  } catch (err) {
    $("error").textContent = `Failed to load: ${err.message}`;
    $("error").classList.remove("hidden");
    setSubtitle("Error loading showtimes");
  } finally {
    $("loading").classList.add("hidden");
    btn.classList.remove("spinning");
  }
}

$("refreshBtn").addEventListener("click", () => load(true));
load();
