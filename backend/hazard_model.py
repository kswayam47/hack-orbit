"""Modular hazard model computing composite earthquake-evacuation risk.

This file defines `compute_edge_risk`, invoked from `routing.py` for every
edge. It combines multiple hazard layers into a single risk score using the
formula provided by the user:

    Risk_Score = w1 * Structural_Risk +
                 w2 * Liquefaction_Risk +
                 w3 * Blockage_Status +
                 w4 * Population_Density +
                 w5 * Distance_to_Safe_Zone

Each factor is normalised to the range [0, 1]. If a data layer is missing the
factor returns 0.0, so the function degrades gracefully until proper data is
added.

Add your GeoJSON / raster files inside `backend/data/` using the following
names (can be changed below):
    structural_risk.geojson      – polygons with a `risk` field (0-1)
    liquefaction.geojson         – polygons (risk assumed 1 within)
    blocked_roads.geojson        – LineString roads currently blocked
    population_density.tif       – raster of persons/km², will be rescaled
    shelters.geojson             – Point safe zones

Tuning weights:
    Adjust `WEIGHTS` dict or read from env vars.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

import math
import random

from shapely.geometry import LineString, Point
from shapely.ops import nearest_points

try:
    import geopandas as gpd
except ImportError:  # pragma: no cover – optional dependency
    gpd = None

try:
    import rasterio
except ImportError:  # pragma: no cover
    rasterio = None

DATA_DIR = Path(__file__).parent / "data"

# ----------------------------- load vector layers ---------------------------

def _load_vector(name: str):
    if gpd is None:
        return None
    path = DATA_DIR / name
    return gpd.read_file(path) if path.exists() else None

_struct_risk_gdf = _load_vector("structural_risk.geojson")
_liquefaction_gdf = _load_vector("liquefaction.geojson")
_blocked_gdf = _load_vector("blocked_roads.geojson")
_shelters_gdf = _load_vector("shelters.geojson")

# ----------------------------- load raster ----------------------------------
if rasterio is not None:
    pop_path = DATA_DIR / "population_density.tif"
    _pop_ds = rasterio.open(pop_path) if pop_path.exists() else None
else:
    _pop_ds = None

# ----------------------------- weights --------------------------------------
WEIGHTS: Dict[str, float] = {
    "w1": 0.30,  # Structural risk
    "w2": 0.20,  # Liquefaction risk
    "w3": 0.25,  # Blockage status
    "w4": 0.15,  # Population density
    "w5": 0.10,  # Distance to safe zone
}

# ----------------------------- helpers --------------------------------------

_random = random.Random(42)

def _norm(val: float, min_v: float, max_v: float) -> float:
    """Clamp then normalise to [0, 1]."""
    if max_v == min_v:
        return 0.0
    return max(0.0, min(1.0, (val - min_v) / (max_v - min_v)))


def _structural_risk(ls: LineString) -> float:
    if _struct_risk_gdf is None:
        return 0.0
    intersecting = _struct_risk_gdf[_struct_risk_gdf.intersects(ls)]
    if intersecting.empty:
        return 0.0
    # average provided risk field if present else assume 1.0
    if "risk" in intersecting.columns:
        return float(intersecting["risk"].mean())
    return 1.0


def _liquefaction_risk(ls: LineString) -> float:
    if _liquefaction_gdf is None:
        return 0.0
    return 1.0 if not _liquefaction_gdf[_liquefaction_gdf.intersects(ls)].empty else 0.0


def _blockage_status(ls: LineString) -> float:
    if _blocked_gdf is None:
        return 0.0
    return 1.0 if not _blocked_gdf[_blocked_gdf.intersects(ls)].empty else 0.0


def _population_density(ls: LineString) -> float:
    if _pop_ds is None:
        return 0.0
    # sample midpoint
    midpt: Point = ls.interpolate(0.5, normalized=True)
    try:
        row, col = _pop_ds.index(midpt.x, midpt.y)
        density = _pop_ds.read(1)[row, col]
        # Assume 0-10,000 persons/km² typical range -> normalise
        return _norm(density, 0, 10000)
    except Exception:  # pragma: no cover – coords outside raster etc.
        return 0.0


def _dist_to_safe_zone(ls: LineString) -> float:
    if _shelters_gdf is None or _shelters_gdf.empty:
        return 0.0
    midpt: Point = ls.interpolate(0.5, normalized=True)
    # find nearest shelter (cheap linear scan since low counts)
    nearest = _shelters_gdf.geometry.distance(midpt).min()
    # normalise: 0m -> 0 risk, ≥1000m -> 1 risk
    return _norm(nearest, 0, 1000)

# ----------------------------- main function --------------------------------

def compute_edge_risk(linestring: LineString, length: float) -> float:  # noqa: D401
    """Return risk ∈ [0,1] for an edge using weighted sum of factors.

    Parameters
    ----------
    linestring : LineString (lon, lat)
        Geometry of the road segment.
    length : float
        Segment length in metres (can be used for normalisation if needed).
    """
    factors = {
        "Structural_Risk": _structural_risk(linestring),
        "Liquefaction_Risk": _liquefaction_risk(linestring),
        "Blockage_Status": _blockage_status(linestring),
        "Population_Density": _population_density(linestring),
        "Distance_to_Safe_Zone": _dist_to_safe_zone(linestring),
    }

    risk = (
        WEIGHTS["w1"] * factors["Structural_Risk"] +
        WEIGHTS["w2"] * factors["Liquefaction_Risk"] +
        WEIGHTS["w3"] * factors["Blockage_Status"] +
        WEIGHTS["w4"] * factors["Population_Density"] +
        WEIGHTS["w5"] * factors["Distance_to_Safe_Zone"]
    )

    # Clamp to [0,1]
    return max(0.0, min(1.0, risk))


# ----------------------------- CLI (debug) ----------------------------------
if __name__ == "__main__":
    # quick self-test with a random segment in Pune (lon,lat)
    test_ls = LineString([(73.85, 18.45), (73.86, 18.46)])
    print("Risk:", compute_edge_risk(test_ls, 100))
