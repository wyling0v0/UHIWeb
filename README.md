# UHI-Bench Website (exploreweb)

Static single-page site showcasing the UHI-Bench dataset, styled after
geotessera.org. No build framework — deployable as-is to GitHub Pages later.

## Preview locally

```bash
cd exploreweb
python3 -m http.server 8123
# open http://localhost:8123
```

(The map basemap tiles load from CARTO, so an internet connection is needed.)

## Files

| File | Role |
|---|---|
| `index.html` + `assets/css/style.css` + `assets/js/main.js` | The site |
| `assets/js/cities-data.js` | Generated city metadata — regenerate with `python3 build_data.py` |
| `assets/layers/` | Pixel-layer data (manifest + per-city PNG/JSON) |
| `assets/vendor/leaflet/` | Vendored Leaflet 1.9.4 |
| `build_data.py` | Joins climate_zones.py + Köppen CSV + appendix table + UHI stats → cities-data.js |
| `make_web_layers.py` | **Server-side** layer export (see below) |
| `make_demo_layers.py` | Synthetic demo layers for UI preview (manifest flagged `demo: true`) |

## Real pixel layers (server export)

The current `assets/layers/` content is **synthetic demo data** (the site shows
a "DEMO DATA" banner). To produce real layers, on the server that hosts
`/data/home/go59fow/workspace/dataset`:

```bash
# deps: numpy pandas pyarrow pyproj matplotlib
python3 make_web_layers.py --out web_layers            # UHI + Change + Static
python3 make_web_layers.py --out web_layers --era5     # additionally ERA5 means (heavy IO)
```

Then replace the demo data locally:

```bash
rm -rf exploreweb/assets/layers/*
scp -r <server>:.../web_layers/* exploreweb/assets/layers/
```

The site picks up the new manifest automatically; the DEMO banner disappears
(`"demo": false`). Layers per city: LST/AirT-UHI 10-yr means & OLS trends
(K/decade), day/night LST-UHI means, NDVI/nightlight change, the 10 Tier-1
static morphology features, and optionally 6 ERA5 climatology fields.
Notes: the `change_layers()` FeatureLoader call in `make_web_layers.py` is a
best-effort guess at the loader API — adjust it against
`dataset/scripts/feature_loader.py` if it warns on first run. OOD4 cities have
no AirT layers (expected).
