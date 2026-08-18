/* UHI-Bench site: map explorer + page behaviors.
 * Data inputs: window.UHI_CITIES (cities-data.js, generated) and
 * assets/layers/manifest.json (make_web_layers.py on the data server, or
 * make_demo_layers.py for the synthetic preview). */
"use strict";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const KOPPEN_COLORS = {
  Aw:  "#ffb14e", BWh: "#fa5f49", BSh: "#ff8a5c", Csa: "#ffd166",
  Cfa: "#a8d84f", Cfb: "#31c48d", Cwb: "#3fb8af", Dfb: "#5b8ff9",
};
const STATION_COLOR = "#9aa4b2";
const KOPPEN_NAMES = {
  Aw: "tropical savanna", BWh: "hot desert", BSh: "hot semi-arid",
  Csa: "mediterranean", Cfa: "humid subtropical", Cfb: "temperate oceanic",
  Cwb: "highland subtropical", Dfb: "humid continental",
};
const ROLE_LABELS = {
  de_source: "German core (Task-3 source)",
  intl: "International core",
  ood_lst: "LST-only transfer target",
  station: "Station AirT (supplementary)",
};
// CSS gradient stops approximating the matplotlib colormaps used on export.
const CMAP_GRADIENTS = {
  magma:   ["#000004", "#3b0f70", "#8c2981", "#de4968", "#fe9f6d", "#fcfdbf"],
  inferno: ["#000004", "#420a68", "#932667", "#dd513a", "#fca50a", "#fcffa4"],
  viridis: ["#440154", "#414487", "#2a788e", "#22a884", "#7ad151", "#fde725"],
  cividis: ["#00224e", "#35456c", "#666970", "#948e77", "#c8b866", "#fee838"],
  RdBu_r:  ["#2166ac", "#67a9cf", "#f7f7f7", "#ef8a62", "#b2182b"],
  BrBG:    ["#8c510a", "#d8b365", "#f5f5f5", "#5ab4ac", "#01665e"],
  PuOr_r:  ["#542788", "#998ec3", "#f7f7f7", "#f1a340", "#b35806"],
  Greens:  ["#f7fcf5", "#c7e9c0", "#74c476", "#238b45", "#00441b"],
  Blues:   ["#f7fbff", "#c6dbef", "#6baed6", "#2171b5", "#08306b"],
};
const GROUP_ORDER = ["UHI", "Change", "Static", "ERA5"];
const WORLD_VIEW = { center: [24, 12], zoom: 2.4 };
const CITY_ZOOM = 11;
const LAYER_MIN_ZOOM = 9;

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const cities = window.UHI_CITIES || [];
const bySlug = Object.fromEntries(cities.map((c) => [c.slug, c]));
let manifest = null;            // layers manifest (or null when absent)
let activeCity = null;          // slug of city whose layers are shown
let activeLayer = null;         // layer id
let overlay = null;             // L.imageOverlay
let overlayOpacity = 0.85;
const valueCache = {};          // "city/layer" -> value grid JSON

// ---------------------------------------------------------------------------
// Map setup
// ---------------------------------------------------------------------------
const map = L.map("map", {
  zoomSnap: 0.2, worldCopyJump: true, attributionControl: true, maxZoom: 19,
}).setView(WORLD_VIEW.center, WORLD_VIEW.zoom);
// Basemap choices: dark (site look), satellite (building footprints + water
// visible when zoomed in), OSM standard (building outlines drawn at z16+).
const basemaps = {
  "Dark": L.tileLayer(
    "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    { attribution: "© OpenStreetMap contributors © CARTO", maxZoom: 19 }
  ).addTo(map),
  "Satellite": L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { attribution: "Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics",
      maxZoom: 19 }
  ),
  "OSM": L.tileLayer(
    "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    { attribution: "© OpenStreetMap contributors", maxZoom: 19 }
  ),
};
L.control.layers(basemaps, {}, { position: "topleft" }).addTo(map);

function cityColor(c) {
  return c.role === "station" ? STATION_COLOR : (KOPPEN_COLORS[c.koppen] || "#ccc");
}

function popupHtml(c) {
  const color = cityColor(c);
  const pill = c.koppen
    ? `<span class="koppen-pill" style="background:${color}">${c.koppen}</span>` : "";
  const lines = [];
  lines.push(`<b>${c.name}, ${c.country}</b>${pill}`);
  if (c.koppen_group)
    lines.push(`<span style="color:#9aa1bd">${c.koppen_group} · ${ROLE_LABELS[c.role] || ""}</span>`);
  else if (ROLE_LABELS[c.role])
    lines.push(`<span style="color:#9aa1bd">${ROLE_LABELS[c.role]}</span>`);
  if (c.n_grids_lst) lines.push(`LST grid: ${c.n_grids_lst.toLocaleString()} px · missing ${c.lst_missing}`);
  if (c.n_grids_airt) lines.push(`AirT grid: ${c.n_grids_airt.toLocaleString()} px`);
  if (c.n_stations) lines.push(`${c.n_stations} stations · ${c.airt_range}`);
  const hasLayers = manifest && manifest.cities[c.slug];
  lines.push(hasLayers
    ? `<a href="#explorer" data-fly="${c.slug}">Zoom to explore pixel layers →</a>`
    : `<span style="color:#666f8f;font-size:.78rem">Pixel layers pending data export</span>`);
  return `<div class="popup-city">${lines.join("<br/>")}</div>`;
}

const markers = {};
cities.forEach((c) => {
  const m = L.circleMarker([c.lat, c.lon], {
    radius: c.role === "station" ? 5 : 7,
    color: c.role === "ood_lst" ? "#e8eaf2" : "#0a0a1a",
    weight: 1.5,
    fillColor: cityColor(c),
    fillOpacity: 0.92,
    dashArray: c.role === "ood_lst" ? "2,3" : null,
  }).addTo(map);
  m.bindPopup(() => popupHtml(c));
  m.bindTooltip(`${c.name}`, { direction: "top", offset: [0, -6] });
  markers[c.slug] = m;
});

// Popup "zoom to explore" links.
map.on("popupopen", (e) => {
  const a = e.popup.getElement().querySelector("[data-fly]");
  if (a) a.addEventListener("click", (ev) => {
    ev.preventDefault();
    flyToCity(a.getAttribute("data-fly"));
  });
});

function flyToCity(slug) {
  const c = bySlug[slug];
  if (!c) return;
  map.closePopup();
  const hasLayers = manifest && manifest.cities[slug];
  map.flyTo([c.lat, c.lon], hasLayers ? CITY_ZOOM : 10, { duration: 1.4 });
}

// ---------------------------------------------------------------------------
// Köppen legend under the map
// ---------------------------------------------------------------------------
(function buildKoppenLegend() {
  const el = document.getElementById("koppen-legend");
  const present = [...new Set(cities.map((c) => c.koppen).filter(Boolean))];
  el.innerHTML =
    present.map((k) =>
      `<span class="chip"><i style="background:${KOPPEN_COLORS[k]}"></i>${k} ${KOPPEN_NAMES[k] || ""}</span>`
    ).join("") +
    `<span class="chip"><i style="background:${STATION_COLOR}"></i>station AirT</span>` +
    `<span class="chip" style="opacity:.75"><i style="background:transparent;border:1.5px dashed #e8eaf2;border-radius:50%"></i>LST-only transfer city</span>`;
})();

// ---------------------------------------------------------------------------
// City dropdown
// ---------------------------------------------------------------------------
const citySelect = document.getElementById("city-select");
cities
  .slice()
  .sort((a, b) => a.name.localeCompare(b.name))
  .forEach((c) => {
    const o = document.createElement("option");
    o.value = c.slug;
    o.textContent = `${c.name}, ${c.country}`;
    citySelect.appendChild(o);
  });
citySelect.addEventListener("change", () => {
  if (!citySelect.value) map.flyTo(WORLD_VIEW.center, WORLD_VIEW.zoom, { duration: 1.2 });
  else flyToCity(citySelect.value);
});

// ---------------------------------------------------------------------------
// Manifest + layer panel
// ---------------------------------------------------------------------------
const layerList = document.getElementById("layer-list");
const legendBlock = document.getElementById("legend-block");
const readout = document.getElementById("readout");
const hint = document.getElementById("map-hint");

fetch("assets/layers/manifest.json")
  .then((r) => (r.ok ? r.json() : null))
  .catch(() => null)
  .then((m) => {
    manifest = m;
    if (m && m.demo) document.getElementById("demo-badge").hidden = false;
    if (!m) showHint("Pixel layers not exported yet — city annotations only.");
    updateActiveCity();
  });

function showHint(text) {
  hint.textContent = text;
  hint.hidden = false;
}

function buildLayerList(slug) {
  const cityLayers = manifest.cities[slug].layers;
  const defs = manifest.layer_defs;
  const groups = {};
  Object.keys(defs).forEach((lid) => {
    const g = defs[lid].group;
    (groups[g] = groups[g] || []).push(lid);
  });
  layerList.innerHTML = "";
  GROUP_ORDER.filter((g) => groups[g]).forEach((g) => {
    const div = document.createElement("div");
    div.className = "layer-group";
    div.innerHTML = `<b>${g}</b>`;
    groups[g].forEach((lid) => {
      const available = !!cityLayers[lid];
      const item = document.createElement("div");
      item.className = "layer-item" + (available ? "" : " disabled");
      item.innerHTML =
        `<input type="radio" name="layer" id="ly-${lid}" value="${lid}" ${available ? "" : "disabled"} ${lid === activeLayer ? "checked" : ""}/>` +
        `<label for="ly-${lid}">${defs[lid].label}</label>`;
      div.appendChild(item);
    });
    layerList.appendChild(div);
  });
  layerList.querySelectorAll("input[name=layer]").forEach((inp) =>
    inp.addEventListener("change", () => setLayer(activeCity, inp.value))
  );
}

function clearLayerPanel(message) {
  layerList.innerHTML = `<p style="color:var(--text-dim)">${message}</p>`;
  legendBlock.hidden = true;
  document.getElementById("city-info").hidden = true;
}

function setLayer(slug, layerId) {
  const entry = manifest.cities[slug].layers[layerId];
  if (!entry) return;
  activeLayer = layerId;
  if (overlay) map.removeLayer(overlay);
  overlay = L.imageOverlay("assets/layers/" + entry.png,
    manifest.cities[slug].bounds,
    { opacity: overlayOpacity, interactive: false, className: "pixel-layer" });
  overlay.addTo(map);
  // Legend
  const def = manifest.layer_defs[layerId];
  const stops = CMAP_GRADIENTS[def.cmap] || CMAP_GRADIENTS.viridis;
  document.getElementById("legendbar").style.background =
    `linear-gradient(to right, ${stops.join(",")})`;
  document.getElementById("legend-min").textContent = entry.min;
  document.getElementById("legend-max").textContent = entry.max;
  document.getElementById("legend-units").textContent = def.units || "";
  legendBlock.hidden = false;
  readout.textContent = "Click a pixel on the layer to read its value.";
}

function showCityInfo(slug) {
  const c = bySlug[slug];
  const el = document.getElementById("city-info");
  const rows = [];
  rows.push(["Climate", `${c.koppen || "—"} ${c.koppen_group || ""}`]);
  rows.push(["Role", ROLE_LABELS[c.role]]);
  if (c.lst_range) rows.push(["LST coverage", c.lst_range]);
  if (c.n_grids_lst) rows.push(["LST pixels", c.n_grids_lst.toLocaleString()]);
  if (c.lst_missing) rows.push(["LST missing", c.lst_missing]);
  if (c.n_grids_airt) rows.push(["AirT pixels", c.n_grids_airt.toLocaleString()]);
  if (c.uhi_stats && c.uhi_stats.lst)
    rows.push(["LST-UHI mean / p95", `${c.uhi_stats.lst.mean_K} / ${c.uhi_stats.lst.p95_K} K`]);
  if (c.uhi_stats && c.uhi_stats.airt)
    rows.push(["AirT-UHI mean / p95", `${c.uhi_stats.airt.mean_K} / ${c.uhi_stats.airt.p95_K} K`]);
  el.innerHTML = `<h4>${c.name}, ${c.country}</h4><table>` +
    rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("") +
    "</table>";
  el.hidden = false;
}

// Determine which city's layers to show based on the current view.
function updateActiveCity() {
  if (!manifest) return;
  const zoom = map.getZoom();
  const center = map.getCenter();
  let best = null, bestDist = Infinity;
  Object.keys(manifest.cities).forEach((slug) => {
    const c = bySlug[slug];
    if (!c) return;
    const d = Math.hypot(c.lat - center.lat, c.lon - center.lng);
    if (d < bestDist) { bestDist = d; best = slug; }
  });
  const eligible = zoom >= LAYER_MIN_ZOOM && best && bestDist < 1.2;
  if (eligible && best !== activeCity) {
    activeCity = best;
    activeLayer = manifest.cities[best].layers[activeLayer]
      ? activeLayer
      : Object.keys(manifest.cities[best].layers)[0];
    buildLayerList(best);
    setLayer(best, activeLayer);
    showCityInfo(best);
    citySelect.value = best;
    hint.hidden = true;
  } else if (!eligible && activeCity) {
    activeCity = null;
    if (overlay) { map.removeLayer(overlay); overlay = null; }
    clearLayerPanel("Zoom into a city with exported layers to enable.");
    if (zoom < LAYER_MIN_ZOOM) citySelect.value = "";
  } else if (zoom >= LAYER_MIN_ZOOM && !eligible) {
    // Zoomed in over a city without exported layers.
    const nearest = cities.reduce((acc, c) => {
      const d = Math.hypot(c.lat - center.lat, c.lon - center.lng);
      return d < acc.d ? { d, c } : acc;
    }, { d: Infinity, c: null });
    if (nearest.c && nearest.d < 1.2)
      showHint(`Pixel layers pending data export for ${nearest.c.name}.`);
  }
}
map.on("moveend zoomend", updateActiveCity);

// Opacity slider.
document.getElementById("opacity").addEventListener("input", (e) => {
  overlayOpacity = e.target.value / 100;
  if (overlay) overlay.setOpacity(overlayOpacity);
});

// ---------------------------------------------------------------------------
// Pixel inspector
// ---------------------------------------------------------------------------
map.on("click", (e) => {
  if (!activeCity || !activeLayer || !manifest) return;
  const [[latS, lonW], [latN, lonE]] = manifest.cities[activeCity].bounds;
  const { lat, lng } = e.latlng;
  if (lat < latS || lat > latN || lng < lonW || lng > lonE) return;
  const key = `${activeCity}/${activeLayer}`;
  const entry = manifest.cities[activeCity].layers[activeLayer];
  const show = (grid) => {
    const row = Math.floor(((latN - lat) / (latN - latS)) * grid.nrows);
    const col = Math.floor(((lng - lonW) / (lonE - lonW)) * grid.ncols);
    const v = (grid.values[row] || [])[col];
    const def = manifest.layer_defs[activeLayer];
    readout.innerHTML = v === null || v === undefined
      ? `<span>No data at this pixel.</span>`
      : `<b>${v}</b> ${def.units || ""}<br/><span style="font-size:.78rem">${def.label} · ${lat.toFixed(3)}°, ${lng.toFixed(3)}°</span>`;
  };
  if (valueCache[key]) show(valueCache[key]);
  else fetch("assets/layers/" + entry.values)
    .then((r) => r.json())
    .then((g) => { valueCache[key] = g; show(g); })
    .catch(() => { readout.textContent = "Could not load value grid."; });
});

// ---------------------------------------------------------------------------
// Nav highlight + BibTeX copy
// ---------------------------------------------------------------------------
const navLinks = [...document.querySelectorAll(".nav-links a")];
const observer = new IntersectionObserver((entries) => {
  entries.forEach((en) => {
    if (en.isIntersecting) {
      navLinks.forEach((a) =>
        a.classList.toggle("active", a.getAttribute("href") === "#" + en.target.id));
    }
  });
}, { rootMargin: "-40% 0px -55% 0px" });
["overview", "explorer", "data", "tasks", "access"].forEach((id) => {
  const el = document.getElementById(id);
  if (el) observer.observe(el);
});

document.getElementById("copy-bib").addEventListener("click", (e) => {
  const text = document.getElementById("bibtex").innerText.replace(/Copy$/, "").trim();
  navigator.clipboard.writeText(text).then(() => {
    e.target.textContent = "Copied ✓";
    setTimeout(() => (e.target.textContent = "Copy"), 1600);
  });
});
