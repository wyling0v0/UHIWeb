# Handoff: export real pixel layers on the data server

For the agent working on the server that hosts
`/data/home/go59fow/workspace/dataset`. Goal: run `make_web_layers.py` from
this repo and send the output back so the website can replace its synthetic
demo layers.

## Task

```bash
# deps: numpy pandas pyarrow pyproj matplotlib  (no GPU needed)
python3 make_web_layers.py \
    --dataset-root /data/home/go59fow/workspace/dataset \
    --out web_layers
# optionally add ERA5 climatology layers afterwards (heavy IO, ~11 years of
# hourly parquet per city):
python3 make_web_layers.py --dataset-root ... --out web_layers --era5
```

Expected output: `web_layers/manifest.json` + `web_layers/{city}/{layer}.png|json`
for 20 cities (16 core + 4 OOD). Total size should be a few tens of MB.
Deliver the `web_layers/` directory back (scp/rsync); it becomes the site's
`assets/layers/` (replace contents wholesale — the manifest carries
`"demo": false`, which switches the site out of demo mode).

## What the script does (and where it may need fixes)

The script was written from the schema documented in `benchmark/draft/data.md`
but has NOT run against the real data. Per-city / per-layer failures only warn
and continue, so let it finish, then fix warnings. Known weak points, in order
of likelihood:

1. **`change_layers()` — FeatureLoader API is a guess.** It tries
   `from feature_loader import FeatureLoader; fl = FeatureLoader(root);
   fl.load(city, feat, year=...)` against `dataset/scripts/feature_loader.py`.
   The real API almost certainly differs. Adapt the call so it returns a
   per-pixel `[N]` array of `ndvi` and `nightlight` for a given year, aligned
   with `static_features.npz` `pixel_ids` order. If per-year features are only
   available for some years, that's fine — it needs ≥4 yearly snapshots, and
   uses (mean of last 3) − (mean of first 3).
2. **`airt_layers()` DE cities — HOSTRADA coordinate join.** It matches
   `monthly_uhi/uhi_YYYYMM.parquet` rows to `static_features.npz` `xy` by
   `round()` of EPSG:3034 metres. If coverage comes back near-zero, the grids
   are probably offset by half a cell — mirror the canonical pivot logic in
   `benchmark/common/data.py::load_ta_field` instead.
3. **Static feature names.** It assumes the first 10 columns are the Tier-1
   features in this order: BCR, road_density, poi_density, nightlight, ndvi,
   water_ratio, distance_to_waterbody, mean_height, dem, wind_exposure_proxy.
   It first tries to match by `feat_names`; check the printed layer list per
   city looks right.
4. LST parquet columns expected: `pixel_id`, `datetime`, `lst_uhi_K`; intl
   AirT: `pixel_id`, `uhi`; ERA5: `pixel_id`, `u10 v10 tcc d2m blh ssrd`.

## Sanity checks before sending back

- `manifest.json` lists ~20 cities; each core city has `lst_uhi_mean`,
  `lst_uhi_trend`, `airt_uhi_mean` + 10 static layers. OOD4 (tehran, khartoum,
  casablanca, istanbul) have **no** airt layers — expected.
- Spot-check magnitudes against the paper: city-mean LST-UHI roughly −0.5…+10 K
  (e.g. Johannesburg ≈ +9.9 K, Hamburg ≈ 0 K); trends are small (|slope|
  typically < 1 K/decade); AirT-UHI means are ~0.1–0.4 K.
- PNGs should show a coherent urban footprint, not noise or a single row
  (a stripe artifact would mean the lat/lon → grid mapping broke; see
  `CityGrid` — grid step is the median unique-coordinate spacing).
- Bounds in the manifest are `[[south, west], [north, east]]` in WGS84 and
  should bracket the city centre.

## Context

- The website consuming this lives in this repo (`index.html`,
  `assets/js/main.js`); layer contract is documented in `README.md`.
- Do not commit `web_layers/` output to this repo from the server — just
  transfer the files; the website side will replace `assets/layers/` and
  commit there.
