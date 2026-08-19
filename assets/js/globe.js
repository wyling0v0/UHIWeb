// Coverage globe (homepage) — MapLibre GL v5 globe projection.
// Each gridded city is drawn as its true 1 km data footprint (union of valid
// pixels, from assets/data/coverage.geojson): a soft halo, a lit fill and a
// glowing edge — patches, not point markers. Hover or click a patch to fill
// the #globe-info panel. The globe slowly auto-rotates until first interaction.
(function () {
  const el = document.getElementById("globe");
  if (!el || typeof maplibregl === "undefined" || !window.UHI_CITIES) return;

  const ROLE_LABELS = {
    de_source: "German source city",
    intl: "International source city",
    ood_lst: "Transfer city (LST only)",
    station: "Station network",
  };
  const COLORS = { dual: "#31c48d", lst: "#ff6b4a", station: "#c6cbe0" };
  const GLOWS = { dual: "#8df0c2", lst: "#ffb09a", station: "#ffffff" };

  const yearsOf = (range) => {
    const m = /(\d{4}).*?(\d{4})/.exec(range || "");
    return m ? (+m[2] - +m[1] + 1) : 0;
  };
  const spanOf = (range) => (range || "").replace(/\/\d\d\/\d\d/g, "");

  const byslug = {};
  window.UHI_CITIES.forEach((c) => { byslug[c.slug] = c; });
  const classOf = (c) => c.role === "station" ? "station"
    : c.airt_range ? "dual" : "lst";
  const propsOf = (c) => ({
    slug: c.slug, name: c.name, country: c.country,
    koppen: c.koppen, koppen_group: c.koppen_group, role: c.role,
    class: classOf(c),
    lst_range: c.lst_range, airt_range: c.airt_range,
    lst_years: yearsOf(c.lst_range), airt_years: yearsOf(c.airt_range),
    n_grids_lst: c.n_grids_lst, n_grids_airt: c.n_grids_airt,
    n_stations: c.n_stations,
    lst_missing: c.lst_missing, airt_missing: c.airt_missing,
  });

  const map = new maplibregl.Map({
    container: "globe",
    style: {
      version: 8,
      projection: { type: "globe" },
      sources: {
        base: {
          type: "raster",
          tiles: ["a", "b", "c", "d"].map(
            (s) => `https://${s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png`),
          tileSize: 256,
          maxzoom: 14,
          attribution: "© OpenStreetMap contributors © CARTO",
        },
      },
      layers: [{ id: "base", type: "raster", source: "base" }],
    },
    center: [12, 26], zoom: 1.15, minZoom: 0.6, maxZoom: 5,
    attributionControl: false,
  });
  map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
  map.on("style.load", () => {
    try {
      map.setSky({
        "sky-color": "#05050f",
        "horizon-color": "#141634",
        "fog-color": "#0a0a1a",
        "sky-horizon-blend": 0.6,
        "horizon-fog-blend": 0.6,
        "fog-ground-blend": 0.8,
      });
    } catch (e) { /* sky is cosmetic */ }
  });

  // Gentle auto-rotation, stopped by the first user interaction.
  let spinning = true, last = performance.now();
  const spin = (now) => {
    if (!spinning) return;
    const dt = Math.min(now - last, 100); last = now;
    const c = map.getCenter();
    map.setCenter([c.lng + dt * 0.0022, c.lat]);
    requestAnimationFrame(spin);
  };
  requestAnimationFrame(spin);
  ["pointerdown", "wheel", "touchstart"].forEach((ev) =>
    el.addEventListener(ev, () => { spinning = false; }, { once: true, passive: true }));

  const info = document.getElementById("globe-info");
  const show = (p) => {
    const rows = [];
    rows.push(["Role", ROLE_LABELS[p.role] || p.role]);
    if (p.koppen) rows.push(["Climate", `${p.koppen} · ${p.koppen_group}`]);
    if (p.lst_years)
      rows.push(["LST-UHI", `${spanOf(p.lst_range)} — ${p.lst_years} yr · ` +
        `${(+p.n_grids_lst || 0).toLocaleString()} px · ${p.lst_missing} cloud-gapped`]);
    if (p.airt_years)
      rows.push(["AirT-UHI", `${spanOf(p.airt_range)} — ${p.airt_years} yr · ` +
        `${(+p.n_grids_airt || 0).toLocaleString()} px · ${p.airt_missing} missing`]);
    else if (p.n_stations)
      rows.push(["AirT", `${p.n_stations} stations · ${p.airt_range}`]);
    info.innerHTML = `<h4>${p.name}<span>${p.country}</span></h4><table>` +
      rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("") +
      `</table><a href="#explorer" class="globe-explore">Open in the explorer ↓</a>`;
    info.hidden = false;
  };
  const hide = () => { info.hidden = true; };
  const setHover = (slug) => {
    ["patch-hover", "halo-hover"].forEach((id) =>
      map.getLayer(id) && map.setFilter(id, ["==", ["get", "slug"], slug || ""]));
  };

  map.on("load", async () => {
    let fc;
    try {
      fc = await (await fetch("assets/data/coverage.geojson")).json();
    } catch (e) { return; }
    const patches = [], halos = [], stations = [];
    fc.features.forEach((f) => {
      const c = byslug[f.properties.slug];
      if (!c) return;
      const p = { ...propsOf(c), n_px: f.properties.n_px };
      patches.push({ type: "Feature", geometry: f.geometry, properties: p });
      halos.push({ type: "Feature", geometry: f.properties.halo, properties: p });
    });
    window.UHI_CITIES.filter((c) => c.role === "station").forEach((c) => {
      stations.push({
        type: "Feature",
        geometry: { type: "Point", coordinates: [c.lon, c.lat] },
        properties: propsOf(c),
      });
    });

    const color = ["match", ["get", "class"],
      "dual", COLORS.dual, "lst", COLORS.lst, COLORS.station];
    const glow = ["match", ["get", "class"],
      "dual", GLOWS.dual, "lst", GLOWS.lst, GLOWS.station];

    // Station networks stay as small dots (they have no grid footprint).
    map.addSource("stations", { type: "geojson", data: { type: "FeatureCollection", features: stations } });
    map.addLayer({
      id: "station-dot", type: "circle", source: "stations",
      paint: {
        "circle-color": COLORS.station, "circle-opacity": 0.9,
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 0, 2.2, 4, 6],
        "circle-stroke-color": "#0a0a1a", "circle-stroke-width": 1,
      },
    });

    // Gridded cities: halo -> fill -> glow edge, all from the true footprint.
    map.addSource("halos", { type: "geojson", data: { type: "FeatureCollection", features: halos } });
    map.addLayer({
      id: "city-halo", type: "fill", source: "halos",
      paint: { "fill-color": color, "fill-opacity": 0.14 },
    });
    map.addLayer({
      id: "halo-hover", type: "fill", source: "halos",
      filter: ["==", ["get", "slug"], ""],
      paint: { "fill-color": color, "fill-opacity": 0.32 },
    });
    map.addSource("patches", { type: "geojson", data: { type: "FeatureCollection", features: patches } });
    map.addLayer({
      id: "city-patch", type: "fill", source: "patches",
      paint: { "fill-color": color, "fill-opacity": 0.82 },
    });
    map.addLayer({
      id: "patch-glow", type: "line", source: "patches",
      paint: {
        "line-color": glow,
        "line-width": ["interpolate", ["linear"], ["zoom"], 0, 2.4, 4, 5],
        "line-blur": ["interpolate", ["linear"], ["zoom"], 0, 2.4, 4, 5],
        "line-opacity": 0.55,
      },
    });
    map.addLayer({
      id: "patch-edge", type: "line", source: "patches",
      paint: {
        "line-color": glow,
        "line-width": ["interpolate", ["linear"], ["zoom"], 0, 0.9, 4, 2.2],
        "line-opacity": 0.95,
      },
    });
    map.addLayer({
      id: "patch-hover", type: "line", source: "patches",
      filter: ["==", ["get", "slug"], ""],
      paint: {
        "line-color": "#ffffff",
        "line-width": ["interpolate", ["linear"], ["zoom"], 0, 1.6, 4, 3.2],
      },
    });

    const PICK = ["city-patch", "city-halo", "station-dot"];
    const pick = (e) => {
      const fs = map.queryRenderedFeatures(e.point, { layers: PICK });
      if (!fs.length) return null;
      return fs[0].properties;
    };
    map.on("mousemove", (e) => {
      const p = pick(e);
      map.getCanvas().style.cursor = p ? "pointer" : "";
      if (p) { setHover(p.slug); show(p); } else { setHover(null); hide(); }
    });
    map.on("click", (e) => {
      const p = pick(e);
      if (p) { spinning = false; show(p); }
    });
    map.on("mouseout", () => { setHover(null); hide(); });
  });
})();
