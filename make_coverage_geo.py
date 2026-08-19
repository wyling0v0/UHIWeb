#!/usr/bin/env python3
"""Generate assets/data/coverage.geojson from web_layers/ grids.

For each gridded city, the union of valid pixels in lst_uhi_mean becomes a
polygon footprint (the true 1 km data extent), plus a slightly buffered halo
polygon so each city reads as a lit patch on the homepage globe at world zoom.

Run on the server next to web_layers/:
    python3 make_coverage_geo.py
Output is committed to the repo (a few hundred KB), web_layers/ itself is not.
"""
import json
import math
from pathlib import Path

from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

WEB = Path(__file__).parent / "web_layers"
OUT = Path(__file__).parent / "assets" / "data" / "coverage.geojson"


def round_coords(coords, nd=4):
    if isinstance(coords, (int, float)):
        return round(coords, nd)
    return [round_coords(c, nd) for c in coords]


def clean(geom, tol=0.004):
    """Simplify + drop slivers, keep MultiPolygon structure."""
    g = geom.simplify(tol)
    if g.geom_type == "Polygon":
        g = g.buffer(0)  # fix self-intersections
    return g


def main():
    manifest = json.loads((WEB / "manifest.json").read_text())
    feats = []
    for slug, city in manifest["cities"].items():
        layers = city.get("layers", {})
        grid_file = None
        for key in ("lst_uhi_mean", "airt_uhi_mean"):
            if key in layers:
                grid_file = WEB / layers[key]["values"]
                break
        if grid_file is None:
            print(f"[skip] {slug}: no mean-UHI grid")
            continue
        d = json.loads(grid_file.read_text())
        (south, west), (north, east) = city["bounds"]
        nrows, ncols = d["nrows"], d["ncols"]
        dlat = (north - south) / nrows
        dlon = (east - west) / ncols
        cells = []
        for r, row in enumerate(d["values"]):
            for c, v in enumerate(row):
                if v is None:
                    continue
                cells.append(box(west + c * dlon, north - (r + 1) * dlat,
                                 west + (c + 1) * dlon, north - r * dlat))
        if not cells:
            print(f"[skip] {slug}: empty mask")
            continue
        footprint = clean(unary_union(cells))
        # Concentric halos (deg) so each city reads as a lit blob on the globe:
        # bright core -> three soft rings fading outward (~3 / 7 / 14 km).
        halos = []
        for buf, tol in ((0.012, 0.004), (0.03, 0.008), (0.065, 0.012), (0.125, 0.02)):
            h = clean(footprint.buffer(buf, join_style=2), tol=tol)
            halos.append({"type": h.geom_type,
                          "coordinates": round_coords(mapping(h)["coordinates"])})
        n_px = len(cells)
        feats.append({
            "type": "Feature",
            "geometry": {
                "type": footprint.geom_type,
                "coordinates": round_coords(mapping(footprint)["coordinates"]),
            },
            "properties": {
                "slug": slug, "n_px": n_px,
                "halos": halos,
            },
        })
        print(f"[ok] {slug}: {n_px} px, footprint {footprint.area:.3f} deg^2")

    fc = {"type": "FeatureCollection", "features": feats}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(fc, separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size/1024:.0f} KB, {len(feats)} cities)")


if __name__ == "__main__":
    main()
