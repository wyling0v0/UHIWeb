#!/usr/bin/env python3
"""Build assets/js/cities-data.js for the UHI-Bench website.

Joins, per city: Köppen class/group/role (common/climate_zones.py), coordinates
(koeppenanalysis CSV; OOD4 hand-set), grid counts / temporal range / missing
rates (parsed from the paper's appendix dataset-summary longtable), and
dual-source UHI statistics where available (3d_uhi7_analysis summary CSV).

Re-run whenever any source file changes:  python3 build_data.py
"""
import csv
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parents[3]  # /Users/ling/Desktop/benchmark
sys.path.insert(0, str(BENCH))

from common.climate_zones import CITIES, DE_SOURCE, INTL_TARGET  # noqa: E402

KOPPEN_CSV = BENCH / "koeppenanalysis" / "koppen_city_representativeness_summary.csv"
UHI7_CSV = BENCH / "3d_uhi7_analysis" / "fig_uhi7_summary_stats.csv"
APPENDIX_TEX = HERE.parent / "727KDD" / "appendix_dataset_summary_from_source.tex"
OUT = HERE / "assets" / "js" / "cities-data.js"

# City centres for cities absent from the Köppen representativeness CSV.
MANUAL_COORDS = {
    "tehran": (35.69, 51.39),
    "khartoum": (15.50, 32.56),
    "casablanca": (33.57, -7.59),
    "istanbul": (41.01, 28.98),
    "rome": (41.89, 12.48),
    "temuco": (-38.74, -72.59),
}

DISPLAY = {
    "berlin": ("Berlin", "DE"), "hamburg": ("Hamburg", "DE"),
    "munich": ("Munich", "DE"), "cologne": ("Cologne", "DE"),
    "dortmund": ("Dortmund", "DE"), "dusseldorf": ("Düsseldorf", "DE"),
    "frankfurt": ("Frankfurt", "DE"), "stuttgart": ("Stuttgart", "DE"),
    "warsaw": ("Warsaw", "PL"), "bucharest": ("Bucharest", "RO"),
    "sao_paulo": ("São Paulo", "BR"), "buenos_aires": ("Buenos Aires", "AR"),
    "johannesburg": ("Johannesburg", "ZA"), "lagos": ("Lagos", "NG"),
    "cairo": ("Cairo", "EG"), "riyadh": ("Riyadh", "SA"),
    "tehran": ("Tehran", "IR"), "khartoum": ("Khartoum", "SD"),
    "casablanca": ("Casablanca", "MA"), "istanbul": ("Istanbul", "TR"),
    "rome": ("Rome", "IT"), "temuco": ("Temuco", "CL"),
}
# Map "CityName, CC" in the appendix table back to our slugs.
TEX_NAME_TO_SLUG = {name: slug for slug, (name, _cc) in DISPLAY.items()}

STATION_CITIES = {
    "rome": {"n_stations": 17, "years": "JJA 2019–2020"},
    "temuco": {"n_stations": 50, "years": "2017–2018"},
}


def parse_appendix(path):
    """Walk the longtable and return {slug: {...}} per data block.

    Rows look like:  [Grid & Air Temperature UHI &] City, CC & 2{,}817 &
    2015/01/01 -- 2025/12/31 & 96{,}432 & 74.6% \\
    The first two cells only appear on the first row of each block.
    """
    text = path.read_text()
    text = text.split(r"\endlastfoot", 1)[-1]
    # Normalise LaTeX accent escapes so names match DISPLAY.
    text = text.replace('D\\"{u}sseldorf', "Düsseldorf")
    text = text.replace("S\\~{a}o Paulo", "São Paulo")
    rows = re.findall(
        r"([A-Za-zÀ-ž .]+),\s*([A-Z]{2})\s*\n"      # city, country
        r"&\s*([\d{},]+|[\d]+ stations)\s*\n"        # grids/stations
        r"&\s*(.+?)\s*\n"                            # temporal range
        r"&\s*[\d{},]+\s*\n"                         # temporal steps
        r"&\s*(\S+?)\\%",                            # missing rate
        text,
    )
    # Determine which block each row belongs to by scanning block headers in order.
    blocks = []
    for m in re.finditer(
        r"(Air Temperature UHI|LST-UHI|Static:|Annual:|Seasonal:|"
        r"Meteorological)", text
    ):
        blocks.append((m.start(), m.group(1)))

    out = {}
    for m in re.finditer(
        r"([A-Za-zÀ-ž .]+),\s*([A-Z]{2})\s*\n"
        r"&\s*([\d{},]+ stations|[\d{},]+)\s*\n"
        r"&\s*(.+?)\s*\n"
        r"&\s*([\d{},]+)\s*\n"
        r"&\s*(.+?)\s*\\\\",
        text,
    ):
        pos = m.start()
        block = None
        for bpos, bname in blocks:
            if bpos < pos:
                block = bname
            else:
                break
        city_name = m.group(1).strip()
        slug = TEX_NAME_TO_SLUG.get(city_name)
        if slug is None:
            continue
        n = m.group(3).replace("{,}", "").replace(" stations", "")
        rng = (m.group(4).replace("{,}", "").replace(" -- ", "–")
               .replace("--", "–").replace("$\\approx$", "≈"))
        missing = m.group(6).replace("\\%", "%").replace("$\\approx$", "≈").strip()
        rec = out.setdefault(slug, {})
        if block == "Air Temperature UHI":
            rec["n_grids_airt"] = int(n)
            rec["airt_range"] = rng
            rec["airt_missing"] = missing
        elif block == "LST-UHI":
            rec["n_grids_lst"] = int(n)
            rec["lst_range"] = rng
            rec["lst_missing"] = missing
    return out, rows


def load_coords():
    coords = {}
    with open(KOPPEN_CSV) as f:
        for r in csv.DictReader(f):
            coords[r["city"]] = (float(r["lat_mean"]), float(r["lon_mean"]),
                                 int(r["n_pixels"]))
    return coords


def load_uhi_stats():
    """{slug: {lst: {...}, airt: {...}}} from the 7-city dual-source summary."""
    stats = {}
    if not UHI7_CSV.exists():
        return stats
    with open(UHI7_CSV) as f:
        for r in csv.DictReader(f):
            slug = r["city"]
            key = "lst" if r["modality"] == "LST-UHI" else "airt"
            stats.setdefault(slug, {})[key] = {
                "mean_K": round(float(r["mean_uhi_K"]), 2),
                "p95_K": round(float(r["p95"]), 2),
                "p05_K": round(float(r["p05"]), 2),
            }
    return stats


def main():
    appendix, _ = parse_appendix(APPENDIX_TEX)
    coords = load_coords()
    uhi = load_uhi_stats()

    cities = []
    for slug, (koppen, group, dist) in CITIES.items():
        name, cc = DISPLAY[slug]
        if slug in DE_SOURCE:
            role = "de_source"
        elif slug in INTL_TARGET:
            role = "intl"
        else:
            role = "ood_lst"
        if slug in coords:
            lat, lon, npx = coords[slug]
        else:
            lat, lon = MANUAL_COORDS[slug]
            npx = None
        rec = {
            "slug": slug, "name": name, "country": cc,
            "lat": round(lat, 4), "lon": round(lon, 4),
            "koppen": koppen, "koppen_group": group.replace("-", " "),
            "role": role,
        }
        rec.update(appendix.get(slug, {}))
        if slug in uhi:
            rec["uhi_stats"] = uhi[slug]
        cities.append(rec)

    for slug, info in STATION_CITIES.items():
        name, cc = DISPLAY[slug]
        lat, lon = MANUAL_COORDS[slug]
        cities.append({
            "slug": slug, "name": name, "country": cc,
            "lat": lat, "lon": lon,
            "koppen": None, "koppen_group": None, "role": "station",
            "n_stations": info["n_stations"], "airt_range": info["years"],
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "// Generated by build_data.py — do not edit by hand.\n"
        "window.UHI_CITIES = "
        + json.dumps(cities, ensure_ascii=False, indent=1)
        + ";\n"
    )
    n_grid = sum(1 for c in cities if c["role"] != "station")
    print(f"Wrote {OUT} — {len(cities)} cities ({n_grid} gridded + "
          f"{len(cities) - n_grid} station).")
    missing_fields = [c["slug"] for c in cities
                      if c["role"] in ("de_source", "intl")
                      and "lst_missing" not in c]
    if missing_fields:
        print(f"WARNING: no LST appendix row parsed for: {missing_fields}")


if __name__ == "__main__":
    main()
