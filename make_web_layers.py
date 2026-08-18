#!/usr/bin/env python3
"""Export per-city, per-pixel web layers for the UHI-Bench website.

Run ON THE SERVER that hosts the dataset (paths per benchmark/draft/data.md):

    python3 make_web_layers.py --dataset-root /data/home/go59fow/workspace/dataset \
        --out web_layers [--cities cairo berlin ...] [--era5]

Then copy the output back into the website:

    scp -r <server>:.../web_layers/* <local>/exploreweb/assets/layers/

For every city it writes  {out}/{city}/{layer}.png  (colored overlay, NaN
transparent) +  {layer}.json  (value grid for click-to-inspect) and a global
{out}/manifest.json consumed by assets/js/main.js.

Layer groups
  UHI    : lst_uhi_mean, lst_uhi_trend, lst_uhi_day_mean, lst_uhi_night_mean,
           airt_uhi_mean, airt_uhi_trend        (trend = OLS slope of yearly
           means, K/decade; red = warming/worsening, blue = improving)
  Change : ndvi_change, nightlight_change       (mean of last 3 available years
           minus mean of first 3; needs per-year features, else skipped)
  Static : the 10 Tier-1 features from static_features.npz
  ERA5   : wind_speed, tcc, d2m, blh, ssrd 10-yr means  (--era5; heavy IO)

Dependencies: numpy, pandas, pyarrow, pyproj, matplotlib.
Failures are per-city / per-layer: a warning is printed and the export
continues, so one malformed file never kills the run.
"""
import argparse
import json
import sys
import traceback
from datetime import date
from pathlib import Path

import numpy as np

CORE16 = ["berlin", "hamburg", "munich", "cologne", "dortmund", "dusseldorf",
          "frankfurt", "stuttgart", "warsaw", "bucharest", "sao_paulo",
          "buenos_aires", "johannesburg", "lagos", "cairo", "riyadh"]
OOD4 = ["tehran", "khartoum", "casablanca", "istanbul"]
DE8 = CORE16[:8]

TIER1 = ["BCR", "road_density", "poi_density", "nightlight", "ndvi",
         "water_ratio", "distance_to_waterbody", "mean_height", "dem",
         "wind_exposure_proxy"]

# layer id -> (label, group, units, matplotlib cmap, diverging?)
LAYER_DEFS = {
    "lst_uhi_mean":       ("LST-UHI mean",             "UHI",    "K",      "magma",    False),
    "lst_uhi_day_mean":   ("LST-UHI daytime mean",     "UHI",    "K",      "magma",    False),
    "lst_uhi_night_mean": ("LST-UHI nighttime mean",   "UHI",    "K",      "magma",    False),
    "lst_uhi_trend":      ("LST-UHI trend",            "UHI",    "K/decade", "RdBu_r", True),
    "airt_uhi_mean":      ("AirT-UHI mean",            "UHI",    "K",      "inferno",  False),
    "airt_uhi_trend":     ("AirT-UHI trend",           "UHI",    "K/decade", "RdBu_r", True),
    "ndvi_change":        ("NDVI change (greening)",   "Change", "ΔNDVI",  "BrBG",     True),
    "nightlight_change":  ("Nightlight change",        "Change", "Δradiance", "PuOr_r", True),
    "BCR":                ("Building coverage ratio",  "Static", "",       "viridis",  False),
    "road_density":       ("Road density",             "Static", "",       "viridis",  False),
    "poi_density":        ("POI density",              "Static", "",       "viridis",  False),
    "nightlight":         ("Nighttime light",          "Static", "",       "cividis",  False),
    "ndvi":               ("NDVI",                     "Static", "",       "Greens",   False),
    "water_ratio":        ("Water fraction",           "Static", "",       "Blues",    False),
    "distance_to_waterbody": ("Distance to water",     "Static", "km",     "viridis",  False),
    "mean_height":        ("Mean building height",     "Static", "m",      "viridis",  False),
    "dem":                ("Elevation (DEM)",          "Static", "m",      "cividis",  False),
    "wind_exposure_proxy": ("Wind exposure proxy",     "Static", "",       "viridis",  False),
    "wind_speed":         ("10 m wind speed, mean",    "ERA5",   "m/s",    "viridis",  False),
    "tcc":                ("Total cloud cover, mean",  "ERA5",   "",       "Blues",    False),
    "d2m":                ("2 m dewpoint, mean",       "ERA5",   "K",      "magma",    False),
    "blh":                ("Boundary-layer height, mean", "ERA5", "m",     "viridis",  False),
    "ssrd":               ("Surface solar radiation, mean", "ERA5", "W/m²", "inferno", False),
}


# ----------------------------------------------------------------------------
# Grid + export helpers (also imported by make_demo_layers.py)
# ----------------------------------------------------------------------------

class CityGrid:
    """Regular lat/lon grid wrapping a set of 1 km pixel centres."""

    def __init__(self, lats, lons, pixel_ids):
        self.pixel_ids = np.asarray(pixel_ids)
        # Median neighbour spacing sets the cell size; ~1 km ≈ 0.009° lat.
        self.dlat = max(np.median(np.diff(np.unique(np.round(lats, 5)))), 1e-4)
        self.dlon = max(np.median(np.diff(np.unique(np.round(lons, 5)))), 1e-4)
        self.lat0, self.lon0 = lats.min(), lons.min()
        self.rows = np.round((lats - self.lat0) / self.dlat).astype(int)
        self.cols = np.round((lons - self.lon0) / self.dlon).astype(int)
        self.nrows = int(self.rows.max()) + 1
        self.ncols = int(self.cols.max()) + 1
        self.id_to_idx = {int(p): i for i, p in enumerate(self.pixel_ids)}

    @property
    def bounds(self):
        """[[south, west], [north, east]] of cell edges for L.imageOverlay."""
        return [[float(self.lat0 - self.dlat / 2),
                 float(self.lon0 - self.dlon / 2)],
                [float(self.lat0 + (self.nrows - 0.5) * self.dlat),
                 float(self.lon0 + (self.ncols - 0.5) * self.dlon)]]

    def rasterize(self, values):
        """[N] pixel values -> [nrows, ncols] grid, NaN where no pixel.
        Row 0 = southernmost; flipped to north-up at export time."""
        g = np.full((self.nrows, self.ncols), np.nan, dtype=float)
        g[self.rows, self.cols] = values
        return g


class MetreGrid:
    """Regular EPSG:3034 metre grid — DE cities, whose static xy is a clean
    ~1 km raster. (Round-tripping to WGS84 would break regularity: each row
    drifts slightly in lon.) Bounds come from projecting the four outer
    cell-edge corners to WGS84."""

    def __init__(self, xy, pixel_ids):
        self.pixel_ids = np.asarray(pixel_ids)
        x = np.asarray(xy[:, 0], float)
        y = np.asarray(xy[:, 1], float)
        self.dx = float(np.median(np.diff(np.unique(np.round(x, 1)))))
        self.dy = float(np.median(np.diff(np.unique(np.round(y, 1)))))
        if not (self.dx > 100 and self.dy > 100):
            raise ValueError(f"not a regular km-scale metre grid "
                             f"(dx={self.dx}, dy={self.dy})")
        self.x0, self.y0 = float(x.min()), float(y.min())
        self.cols = np.round((x - self.x0) / self.dx).astype(int)
        self.rows = np.round((y - self.y0) / self.dy).astype(int)
        self.nrows = int(self.rows.max()) + 1
        self.ncols = int(self.cols.max()) + 1
        # Coordinate jitter (sub-cell) multiplies unique values and explodes
        # the grid — catch that here before it produces noise PNGs.
        if self.nrows * self.ncols > 4 * len(self.pixel_ids):
            raise ValueError(f"degenerate metre grid {self.nrows}x{self.ncols} "
                             f"for N={len(self.pixel_ids)}")
        self.id_to_idx = {int(p): i for i, p in enumerate(self.pixel_ids)}
        from pyproj import Transformer
        tr = Transformer.from_crs(3034, 4326, always_xy=True)
        xs = [self.x0 - self.dx / 2, self.x0 + (self.ncols - 0.5) * self.dx]
        ys = [self.y0 - self.dy / 2, self.y0 + (self.nrows - 0.5) * self.dy]
        lon, lat = tr.transform(np.array([xs[0], xs[0], xs[1], xs[1]]),
                                np.array([ys[0], ys[1], ys[0], ys[1]]))
        self._bounds = [[float(lat.min()), float(lon.min())],
                        [float(lat.max()), float(lon.max())]]

    @property
    def bounds(self):
        return self._bounds

    def rasterize(self, values):
        g = np.full((self.nrows, self.ncols), np.nan, dtype=float)
        g[self.rows, self.cols] = values
        return g


class SnapGrid:
    """Grid anchored on the city's clean reference lattice
    (static_features/{city}/grid_centers.csv, a native 0.01-deg WGS84
    raster) for cities whose projected static xy carries sub-cell jitter.
    pixel_id sets match static_features.npz exactly, so rows/cols are taken
    from grid_centers per pixel_id — no coordinate snapping needed."""

    def __init__(self, gc_pid, gc_lat, gc_lon, pixel_ids):
        self.pixel_ids = np.asarray(pixel_ids)
        la = np.round(np.asarray(gc_lat, float), 4)
        lo = np.round(np.asarray(gc_lon, float), 4)
        self.dlat = float(np.median(np.diff(np.unique(la))))
        self.dlon = float(np.median(np.diff(np.unique(lo))))
        if not (1e-5 < self.dlat < 0.1 and 1e-5 < self.dlon < 0.1):
            raise ValueError(f"irregular reference grid "
                             f"(dlat={self.dlat}, dlon={self.dlon})")
        self.lat0, self.lon0 = float(la.min()), float(lo.min())
        self.nrows = int(np.round((la.max() - self.lat0) / self.dlat)) + 1
        self.ncols = int(np.round((lo.max() - self.lon0) / self.dlon)) + 1
        gc_row = np.round((la - self.lat0) / self.dlat).astype(int)
        gc_col = np.round((lo - self.lon0) / self.dlon).astype(int)
        pid_to_rc = {int(p): (int(r), int(c))
                     for p, r, c in zip(gc_pid, gc_row, gc_col)}
        try:
            rcs = [pid_to_rc[int(p)] for p in self.pixel_ids]
        except KeyError as e:
            raise ValueError(f"pixel_id {e} missing from grid_centers.csv")
        self.rows = np.array([r for r, _ in rcs])
        self.cols = np.array([c for _, c in rcs])
        if len(np.unique(self.rows.astype(np.int64) * self.ncols
                         + self.cols)) != len(self.pixel_ids):
            raise ValueError("pixels collide on the reference grid")
        self.id_to_idx = {int(p): i for i, p in enumerate(self.pixel_ids)}
        self._bounds = [[self.lat0 - self.dlat / 2, self.lon0 - self.dlon / 2],
                        [self.lat0 + (self.nrows - 0.5) * self.dlat,
                         self.lon0 + (self.ncols - 0.5) * self.dlon]]

    @property
    def bounds(self):
        return self._bounds

    def rasterize(self, values):
        g = np.full((self.nrows, self.ncols), np.nan, dtype=float)
        g[self.rows, self.cols] = values
        return g


def export_layer(grid, values, layer_id, city_dir, manifest_city,
                 vmin=None, vmax=None):
    """Write {layer}.png + {layer}.json and register in manifest_city."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import colors as mcolors
    import matplotlib.pyplot as plt

    label, group, units, cmap_name, diverging = LAYER_DEFS[layer_id]
    g = grid.rasterize(values)
    finite = g[np.isfinite(g)]
    if finite.size == 0:
        print(f"    ! {layer_id}: all-NaN, skipped")
        return
    if vmin is None or vmax is None:
        lo, hi = np.percentile(finite, [2, 98])
        if diverging:
            m = max(abs(lo), abs(hi)) or 1.0
            lo, hi = -m, m
        if hi - lo < 1e-9:
            hi = lo + 1e-9
        vmin = lo if vmin is None else vmin
        vmax = hi if vmax is None else vmax

    north_up = np.flipud(g)
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
    rgba = plt.get_cmap(cmap_name)(norm(north_up))
    rgba[..., 3] = np.where(np.isfinite(north_up), 0.88, 0.0)
    city_dir.mkdir(parents=True, exist_ok=True)
    plt.imsave(city_dir / f"{layer_id}.png", rgba)

    vals = [[None if not np.isfinite(v) else round(float(v), 3)
             for v in row] for row in north_up]
    (city_dir / f"{layer_id}.json").write_text(json.dumps(
        {"nrows": grid.nrows, "ncols": grid.ncols, "values": vals},
        separators=(",", ":")))

    manifest_city["layers"][layer_id] = {
        "png": f"{city_dir.name}/{layer_id}.png",
        "values": f"{city_dir.name}/{layer_id}.json",
        "min": round(float(vmin), 3), "max": round(float(vmax), 3),
        "units": units,
    }
    print(f"    + {layer_id}  [{vmin:.2f}, {vmax:.2f}] {units}")


def manifest_layer_defs():
    return {lid: {"label": lbl, "group": grp, "units": u,
                  "cmap": cmap, "diverging": div}
            for lid, (lbl, grp, u, cmap, div) in LAYER_DEFS.items()}


def yearly_trend(per_year):
    """per_year: {year: [N] mean values} -> per-pixel OLS slope * 10 (per decade).
    Needs >= 4 years with data at a pixel; NaN elsewhere."""
    years = sorted(per_year)
    Y = np.stack([per_year[y] for y in years])          # [n_years, N]
    t = np.asarray(years, dtype=float)[:, None]
    valid = np.isfinite(Y)
    n = valid.sum(axis=0)
    out = np.full(Y.shape[1], np.nan)
    ok = n >= 4
    if not ok.any():
        return out
    Ym = np.where(valid, Y, 0.0)
    tm = np.where(valid, t, 0.0)
    sy = Ym.sum(axis=0); st = tm.sum(axis=0)
    syt = (Ym * tm).sum(axis=0); stt = (tm * tm).sum(axis=0)
    denom = n * stt - st * st
    with np.errstate(invalid="ignore", divide="ignore"):
        slope = (n * syt - st * sy) / denom
    out[ok] = slope[ok] * 10.0
    return out


# ----------------------------------------------------------------------------
# Server-side data readers
# ----------------------------------------------------------------------------

def load_static(root, city):
    """Returns (grid, features [N, F], feat_names, xy).

    Grid choice: DE cities have a clean ~1 km raster in EPSG:3034 metres
    (MetreGrid); LST cities are natively defined on a 0.01-deg WGS84 grid
    whose projected xy jitters, so they are snapped to the clean lattice in
    lstuhi_1km_hourly/{city}/grid_centers.csv (SnapGrid)."""
    npz = np.load(root / "static_features" / city / "static_features.npz",
                  allow_pickle=True)
    xy = npz["xy"]                       # [N, 2] EPSG:3034 metres
    pixel_ids = npz["pixel_ids"]
    try:
        grid = MetreGrid(xy, pixel_ids)
    except ValueError as e:
        import pandas as pd
        gc = pd.read_csv(root / "static_features" / city / "grid_centers.csv")
        print(f"    (metre grid rejected: {e}; "
              f"using static_features grid_centers.csv)")
        grid = SnapGrid(gc["pixel_id"].to_numpy(), gc["lat"].to_numpy(),
                        gc["lon"].to_numpy(), pixel_ids)
    feat_names = [str(n) for n in npz["feat_names"]]
    return grid, npz["features"], feat_names, xy


def city_years(city):
    return range(2019, 2026) if city in OOD4 else range(2015, 2026)


def lst_layers(root, city, grid, city_dir, mcity):
    import pandas as pd
    per_year, day_sum, day_n, night_sum, night_n = {}, None, None, None, None
    n = len(grid.pixel_ids)
    tz_shift = 0  # datetimes are UTC; use solar offset from longitude
    lon_c = (grid.bounds[0][1] + grid.bounds[1][1]) / 2
    tz_shift = int(round(lon_c / 15.0))
    for year in city_years(city):
        f = (root / "processed" / "lstuhi_1km_hourly" / city /
             f"lst_uhi_1km_hourly_{year}.parquet")
        if not f.exists():
            print(f"    ! LST {year}: missing file")
            continue
        df = pd.read_parquet(f, columns=["pixel_id", "datetime", "lst_uhi_K"])
        df = df.dropna(subset=["lst_uhi_K"])
        idx = df["pixel_id"].map(grid.id_to_idx)
        df = df[idx.notna()]
        idx = idx[idx.notna()].astype(int).to_numpy()
        v = df["lst_uhi_K"].to_numpy()
        cnt = np.bincount(idx, minlength=n)
        s = np.bincount(idx, weights=v, minlength=n)
        with np.errstate(invalid="ignore"):
            per_year[year] = np.where(cnt > 0, s / cnt, np.nan)
        hour_local = (df["datetime"].dt.hour + tz_shift) % 24
        is_day = ((hour_local >= 10) & (hour_local <= 16)).to_numpy()
        for mask, tag in ((is_day, "day"), (~is_day, "night")):
            si = np.bincount(idx[mask], weights=v[mask], minlength=n)
            ci = np.bincount(idx[mask], minlength=n)
            if tag == "day":
                day_sum = si if day_sum is None else day_sum + si
                day_n = ci if day_n is None else day_n + ci
            else:
                night_sum = si if night_sum is None else night_sum + si
                night_n = ci if night_n is None else night_n + ci
    if not per_year:
        print("    ! LST: no data at all")
        return
    Y = np.stack(list(per_year.values()))
    with np.errstate(invalid="ignore"):
        overall = np.nanmean(Y, axis=0)
    export_layer(grid, overall, "lst_uhi_mean", city_dir, mcity)
    export_layer(grid, yearly_trend(per_year), "lst_uhi_trend", city_dir, mcity)
    if day_n is not None:
        with np.errstate(invalid="ignore"):
            export_layer(grid, np.where(day_n > 0, day_sum / day_n, np.nan),
                         "lst_uhi_day_mean", city_dir, mcity)
            export_layer(grid, np.where(night_n > 0, night_sum / night_n, np.nan),
                         "lst_uhi_night_mean", city_dir, mcity)


def airt_layers(root, city, grid, xy, city_dir, mcity):
    import pandas as pd
    n = len(grid.pixel_ids)
    per_year = {}
    if city in DE8:
        # HOSTRADA monthly parquet, keyed by EPSG:3034 coords -> align to xy.
        base = root / "processed" / "hostrada_uhi" / city / "monthly_uhi"
        if not base.exists():
            print("    ! AirT: no hostrada monthly_uhi dir")
            return
        key = {(round(float(x)), round(float(y))): i
               for i, (x, y) in enumerate(xy)}
        for year in city_years(city):
            sums = np.zeros(n); cnts = np.zeros(n)
            for month in range(1, 13):
                f = base / f"uhi_{year}{month:02d}.parquet"
                if not f.exists():
                    continue
                df = pd.read_parquet(
                    f, columns=["x_epsg3034", "y_epsg3034", "uhi"]).dropna()
                ii = [key.get((round(float(x)), round(float(y))))
                      for x, y in zip(df["x_epsg3034"], df["y_epsg3034"])]
                ok = np.array([i is not None for i in ii])
                if not ok.any():
                    continue
                iarr = np.array([i for i in ii if i is not None])
                v = df["uhi"].to_numpy()[ok]
                sums += np.bincount(iarr, weights=v, minlength=n)
                cnts += np.bincount(iarr, minlength=n)
            if cnts.sum() > 0:
                with np.errstate(invalid="ignore"):
                    per_year[year] = np.where(cnts > 0, sums / cnts, np.nan)
    else:
        base = root / "processed" / "atuhi_corrected_1km_hourly" / city
        for year in city_years(city):
            f = base / f"atuhi_corrected_1km_hourly_{year}.parquet"
            if not f.exists():
                continue
            df = pd.read_parquet(f, columns=["pixel_id", "uhi"]).dropna()
            idx = df["pixel_id"].map(grid.id_to_idx)
            df = df[idx.notna()]
            idx = idx[idx.notna()].astype(int).to_numpy()
            cnt = np.bincount(idx, minlength=n)
            s = np.bincount(idx, weights=df["uhi"].to_numpy(), minlength=n)
            with np.errstate(invalid="ignore"):
                per_year[year] = np.where(cnt > 0, s / cnt, np.nan)
    if not per_year:
        print("    ! AirT: no data (expected for OOD4)")
        return
    Y = np.stack(list(per_year.values()))
    with np.errstate(invalid="ignore"):
        export_layer(grid, np.nanmean(Y, axis=0), "airt_uhi_mean",
                     city_dir, mcity)
    export_layer(grid, yearly_trend(per_year), "airt_uhi_trend",
                 city_dir, mcity)


def change_layers(root, city, grid, city_dir, mcity):
    """NDVI / nightlight change from static_features/{city}/temporal_static.

    Reads the per-year NPZs directly (keys: nightlight, ndvi_DJF/MAM/JJA/SON,
    pixel_ids) instead of FeatureLoader — FeatureLoader._load_temporal falls
    back to the nearest available year, which would duplicate snapshots and
    bias the early/late means. Arrays are aligned to the static grid by
    pixel_ids (temporal and static NPZs may differ in pixel count).
    """
    n = len(grid.pixel_ids)
    ts_dir = root / "static_features" / city / "temporal_static"
    if not ts_dir.exists():
        print("    ! Change layers: no temporal_static dir")
        return
    per_year = {"ndvi": {}, "nightlight": {}}
    for f in sorted(ts_dir.glob("*.npz")):
        year = int(f.stem)
        data = np.load(f, allow_pickle=True)
        t_ids = data["pixel_ids"].astype(np.int64)
        id_to_tidx = {int(p): i for i, p in enumerate(t_ids)}
        s_to_t = np.array([id_to_tidx.get(int(p), -1)
                           for p in grid.pixel_ids.astype(np.int64)])
        valid = s_to_t >= 0
        if not valid.any():
            continue
        align = lambda arr: np.where(valid, arr[np.clip(s_to_t, 0, None)],
                                     np.nan).astype(float)
        if "nightlight" in data.files:
            per_year["nightlight"][year] = align(data["nightlight"])
        ndvi_keys = [k for k in data.files if k.startswith("ndvi_")]
        if ndvi_keys:
            per_year["ndvi"][year] = np.nanmean(
                np.stack([align(data[k]) for k in ndvi_keys]), axis=0)
    for feat, out_id in (("ndvi", "ndvi_change"),
                         ("nightlight", "nightlight_change")):
        ys = sorted(per_year[feat])
        if len(ys) < 4:
            print(f"    ! {out_id}: <4 yearly snapshots, skipped")
            continue
        early = np.nanmean(np.stack([per_year[feat][y] for y in ys[:3]]), 0)
        late = np.nanmean(np.stack([per_year[feat][y] for y in ys[-3:]]), 0)
        export_layer(grid, late - early, out_id, city_dir, mcity)


def era5_layers(root, city, grid, city_dir, mcity):
    import pandas as pd
    n = len(grid.pixel_ids)
    cols = ["u10", "v10", "tcc", "d2m", "blh", "ssrd"]
    sums = {c: np.zeros(n) for c in cols}
    cnts = {c: np.zeros(n) for c in cols}
    for year in city_years(city):
        f = root / "temporal_weather" / city / f"era5_hourly_{year}.parquet"
        if not f.exists():
            continue
        df = pd.read_parquet(f, columns=["pixel_id"] + cols)
        idx = df["pixel_id"].map(grid.id_to_idx)
        df = df[idx.notna()]
        idx = idx[idx.notna()].astype(int).to_numpy()
        for c in cols:
            v = df[c].to_numpy()
            ok = np.isfinite(v)
            sums[c] += np.bincount(idx[ok], weights=v[ok], minlength=n)
            cnts[c] += np.bincount(idx[ok], minlength=n)
        print(f"    era5 {year} ok")
    with np.errstate(invalid="ignore"):
        means = {c: np.where(cnts[c] > 0, sums[c] / cnts[c], np.nan)
                 for c in cols}
    if all(np.all(~np.isfinite(m)) for m in means.values()):
        print("    ! ERA5: no data")
        return
    export_layer(grid, np.hypot(means["u10"], means["v10"]), "wind_speed",
                 city_dir, mcity)
    for c in ("tcc", "d2m", "blh", "ssrd"):
        export_layer(grid, means[c], c, city_dir, mcity)


# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset-root", type=Path,
                    default=Path("/data/home/go59fow/workspace/dataset"))
    ap.add_argument("--out", type=Path, default=Path("web_layers"))
    ap.add_argument("--cities", nargs="*", default=CORE16 + OOD4)
    ap.add_argument("--era5", action="store_true",
                    help="also export ERA5 10-yr means (heavy IO)")
    args = ap.parse_args()
    root = args.dataset_root

    manifest = {"demo": False, "generated": str(date.today()),
                "layer_defs": manifest_layer_defs(), "cities": {}}
    for city in args.cities:
        print(f"[{city}]")
        try:
            grid, feats, feat_names, xy = load_static(root, city)
        except Exception as e:
            print(f"    ! static_features failed ({e}); city skipped")
            continue
        city_dir = args.out / city
        mcity = {"bounds": grid.bounds, "layers": {}}
        for i, fname in enumerate(feat_names[:len(TIER1)]):
            lid = fname if fname in LAYER_DEFS else (
                TIER1[i] if i < len(TIER1) else None)
            if lid is None:
                continue
            try:
                export_layer(grid, feats[:, i].astype(float), lid,
                             city_dir, mcity)
            except Exception:
                traceback.print_exc()
        for fn, needs_xy in ((lst_layers, False), (airt_layers, True),
                             (change_layers, False)):
            try:
                if needs_xy:
                    fn(root, city, grid, xy, city_dir, mcity)
                else:
                    fn(root, city, grid, city_dir, mcity)
            except Exception:
                traceback.print_exc()
        if args.era5:
            try:
                era5_layers(root, city, grid, city_dir, mcity)
            except Exception:
                traceback.print_exc()
        if mcity["layers"]:
            manifest["cities"][city] = mcity

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"\nWrote {args.out}/manifest.json with "
          f"{len(manifest['cities'])} cities.")
    print("Copy into the website with:\n"
          f"  scp -r {args.out}/* <local>/exploreweb/assets/layers/")


if __name__ == "__main__":
    main()
