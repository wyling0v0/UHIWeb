// Coverage globe (homepage) — MapLibre GL v5 globe projection.
// City markers are coloured by data coverage class; hovering or clicking a
// marker fills the #globe-info panel with the per-city coverage summary.
(function () {
  const el = document.getElementById("globe");
  if (!el || typeof maplibregl === "undefined" || !window.UHI_CITIES) return;

  const ROLE_LABELS = {
    de_source: "German source city",
    intl: "International source city",
    ood_lst: "Transfer city (LST only)",
    station: "Station network",
  };

  const yearsOf = (range) => {
    // "2015/01/01–2025/12/31" -> 11
    const m = /(\d{4}).*?(\d{4})/.exec(range || "");
    return m ? (+m[2] - +m[1] + 1) : 0;
  };
  const spanOf = (range) => (range || "").replace(/\/\d\d\/\d\d/g, "");

  const fc = {
    type: "FeatureCollection",
    features: window.UHI_CITIES.map((c) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [c.lon, c.lat] },
      properties: {
        slug: c.slug, name: c.name, country: c.country,
        koppen: c.koppen, koppen_group: c.koppen_group, role: c.role,
        class: c.role === "station" ? "station"
             : c.airt_range ? "dual" : "lst",
        lst_years: yearsOf(c.lst_range), airt_years: yearsOf(c.airt_range),
        lst_range: c.lst_range, airt_range: c.airt_range,
        n_grids_lst: c.n_grids_lst, n_grids_airt: c.n_grids_airt,
        n_stations: c.n_stations,
        lst_missing: c.lst_missing, airt_missing: c.airt_missing,
        weight: Math.sqrt(c.n_grids_lst || c.n_grids_airt || c.n_stations * 40 || 500),
      },
    })),
  };

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
    center: [12, 26], zoom: 0.9, minZoom: 0.5, maxZoom: 5,
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

  map.on("load", () => {
    map.addSource("cities", { type: "geojson", data: fc });
    // Soft halo behind each marker.
    map.addLayer({
      id: "city-halo", type: "circle", source: "cities",
      paint: {
        "circle-color": [
          "match", ["get", "class"],
          "dual", "#31c48d", "lst", "#ff6b4a", "#9aa1bd"],
        "circle-opacity": 0.18,
        "circle-radius": [
          "interpolate", ["linear"], ["zoom"],
          0, ["+", 6, ["*", 0.55, ["get", "weight"]]],
          4, ["+", 16, ["*", 1.4, ["get", "weight"]]],
        ],
      },
    });
    map.addLayer({
      id: "city-dot", type: "circle", source: "cities",
      paint: {
        "circle-color": [
          "match", ["get", "class"],
          "dual", "#31c48d", "lst", "#ff6b4a", "#c6cbe0"],
        "circle-opacity": 0.95,
        "circle-radius": [
          "interpolate", ["linear"], ["zoom"],
          0, ["+", 2, ["*", 0.14, ["get", "weight"]]],
          4, ["+", 5, ["*", 0.36, ["get", "weight"]]],
        ],
        "circle-stroke-color": "#0a0a1a",
        "circle-stroke-width": 1,
      },
    });
    // Hovered marker grows + white stroke.
    map.addLayer({
      id: "city-dot-hover", type: "circle", source: "cities",
      filter: ["==", ["get", "slug"], ""],
      paint: {
        "circle-color": "rgba(0,0,0,0)",
        "circle-radius": ["+", 3, ["*", 0.36, ["get", "weight"]]],
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 1.6,
      },
    });

    const info = document.getElementById("globe-info");
    const show = (p) => {
      const rows = [];
      rows.push(["Role", ROLE_LABELS[p.role] || p.role]);
      if (p.koppen) rows.push(["Climate", `${p.koppen} · ${p.koppen_group}`]);
      if (p.lst_years)
        rows.push(["LST-UHI", `${spanOf(p.lst_range)} — ${p.lst_years} yr · ` +
          `${p.n_grids_lst.toLocaleString()} px · ${p.lst_missing} cloud-gapped`]);
      if (p.airt_years)
        rows.push(["AirT-UHI", `${spanOf(p.airt_range)} — ${p.airt_years} yr · ` +
          `${p.n_grids_airt.toLocaleString()} px · ${p.airt_missing} missing`]);
      else if (p.n_stations)
        rows.push(["AirT", `${p.n_stations} stations · ${p.airt_range}`]);
      info.innerHTML = `<h4>${p.name}<span>${p.country}</span></h4><table>` +
        rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("") +
        `</table><a href="#explorer" class="globe-explore">Open in the explorer ↓</a>`;
      info.hidden = false;
    };
    const pick = (e) => {
      const fs = map.queryRenderedFeatures(e.point, { layers: ["city-dot"] });
      if (!fs.length) return null;
      map.setFilter("city-dot-hover", ["==", ["get", "slug"], fs[0].properties.slug]);
      return fs[0].properties;
    };
    const clear = () => {
      map.setFilter("city-dot-hover", ["==", ["get", "slug"], ""]);
      info.hidden = true;
    };
    map.on("mousemove", "city-dot", (e) => {
      map.getCanvas().style.cursor = "pointer";
      if (e.features?.length) show(e.features[0].properties);
    });
    map.on("mouseleave", "city-dot", () => {
      map.getCanvas().style.cursor = "";
      clear();
    });
    map.on("click", "city-dot", (e) => {
      if (e.features?.length) show(e.features[0].properties);
    });
  });
})();
