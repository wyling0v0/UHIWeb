#!/usr/bin/env python3
"""Generate SYNTHETIC demo layers so the website's layer explorer can be
previewed before the real export (make_web_layers.py) has been run on the
data server. The manifest is flagged "demo": true and the UI shows a
"DEMO DATA" banner. Real output copied into assets/layers/ replaces this.

    python3 make_demo_layers.py
"""
import json
from datetime import date
from pathlib import Path

import numpy as np

from make_web_layers import (CityGrid, LAYER_DEFS, export_layer,
                             manifest_layer_defs)

HERE = Path(__file__).resolve().parent
OUT = HERE / "assets" / "layers"

# slug -> (lat, lon) city centre; ~55 x 45 cells of ~0.009 deg (≈1 km)
DEMO_CITIES = {"cairo": (30.075, 31.25), "berlin": (52.55, 13.40)}
NROWS, NCOLS, CELL = 45, 55, 0.009
RNG = np.random.default_rng(7)


def smooth_field(shape, scale=6):
    """Low-frequency random field in [0, 1]."""
    coarse = RNG.normal(size=(shape[0] // scale + 2, shape[1] // scale + 2))
    # Bilinear upsample the coarse noise.
    ry = np.linspace(0, coarse.shape[0] - 1.001, shape[0])
    rx = np.linspace(0, coarse.shape[1] - 1.001, shape[1])
    y0 = ry.astype(int); x0 = rx.astype(int)
    fy = (ry - y0)[:, None]; fx = (rx - x0)[None, :]
    f = (coarse[y0][:, x0] * (1 - fy) * (1 - fx)
         + coarse[y0 + 1][:, x0] * fy * (1 - fx)
         + coarse[y0][:, x0 + 1] * (1 - fy) * fx
         + coarse[y0 + 1][:, x0 + 1] * fy * fx)
    f = f - f.min()
    return f / (f.max() or 1)


def radial(shape):
    """1 at centre -> 0 at edge (urban-core gradient)."""
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    cy, cx = (shape[0] - 1) / 2, (shape[1] - 1) / 2
    r = np.hypot((yy - cy) / cy, (xx - cx) / cx)
    return np.clip(1 - r, 0, 1)


def main():
    manifest = {"demo": True, "generated": str(date.today()),
                "layer_defs": manifest_layer_defs(), "cities": {}}
    shape = (NROWS, NCOLS)
    for slug, (clat, clon) in DEMO_CITIES.items():
        lats = np.repeat(clat - NROWS / 2 * CELL + np.arange(NROWS) * CELL, NCOLS)
        lons = np.tile(clon - NCOLS / 2 * CELL * 1.2
                       + np.arange(NCOLS) * CELL * 1.2, NROWS)
        # Irregular urban footprint: drop ~20% of edge pixels.
        core = radial(shape).ravel()
        keep = core + smooth_field(shape).ravel() * 0.5 > 0.35
        grid = CityGrid(lats[keep], lons[keep], np.arange(keep.sum()))
        mcity = {"bounds": grid.bounds, "layers": {}}
        city_dir = OUT / slug

        base = (radial(shape) * 4 + smooth_field(shape) * 2)[
            *np.unravel_index(np.where(keep)[0], shape)]
        fields = {
            "lst_uhi_mean": base + RNG.normal(0, .2, keep.sum()),
            "lst_uhi_day_mean": base * 1.5,
            "lst_uhi_night_mean": base * 0.5,
            "lst_uhi_trend": (smooth_field(shape).ravel()[keep] - .45) * 1.2,
            "airt_uhi_mean": base * 0.12,
            "airt_uhi_trend": (smooth_field(shape).ravel()[keep] - .5) * .3,
            "ndvi_change": (smooth_field(shape).ravel()[keep] - .55) * .3,
            "nightlight_change": (smooth_field(shape).ravel()[keep] - .4) * 8,
            "BCR": np.clip(base / 5 + RNG.normal(0, .05, keep.sum()), 0, .9),
            "road_density": base / 4,
            "poi_density": np.clip(base / 4 - .2, 0, None) * 3,
            "nightlight": base * 12,
            "ndvi": np.clip(.7 - base / 7, 0, 1),
            "water_ratio": np.clip(smooth_field(shape).ravel()[keep] - .7, 0, 1),
            "distance_to_waterbody": smooth_field(shape).ravel()[keep] * 5000,
            "mean_height": np.clip(base * 6 - 3, 0, None),
            "dem": 20 + smooth_field(shape).ravel()[keep] * 90,
            "wind_exposure_proxy": smooth_field(shape).ravel()[keep],
            "wind_speed": 2 + smooth_field(shape).ravel()[keep] * 3,
            "tcc": .2 + smooth_field(shape).ravel()[keep] * .5,
            "d2m": 278 + smooth_field(shape).ravel()[keep] * 6,
            "blh": 400 + smooth_field(shape).ravel()[keep] * 700,
            "ssrd": (5 + smooth_field(shape).ravel()[keep] * 3) * 1e5,
        }
        for lid, vals in fields.items():
            assert lid in LAYER_DEFS, lid
            export_layer(grid, vals, lid, city_dir, mcity)
        manifest["cities"][slug] = mcity
        print(f"[{slug}] {len(mcity['layers'])} demo layers")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"Wrote {OUT}/manifest.json (demo=true)")


if __name__ == "__main__":
    main()
