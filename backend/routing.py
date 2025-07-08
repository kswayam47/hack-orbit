"""Routing utilities for Sheltr
Build and cache an OSM road network graph enriched with safety-centric risk weights.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
import math
from shapely.geometry import LineString, mapping

# Import hazard model
try:
    from . import hazard_model
except ImportError:
    hazard_model = None
from typing import List, Tuple

import networkx as nx
import numpy as np

try:
    import osmnx as ox
except ImportError:  # pragma: no cover
    raise ImportError(
        "`osmnx` is required for Sheltr routing. Install via `pip install osmnx`."
    )

try:
    import geopandas as gpd
    from shapely.geometry import LineString
except ImportError:  # pragma: no cover
    gpd = None  # geopandas is optional but recommended


# ---------------------------------------------------------------------------
# Constants & configuration
# ---------------------------------------------------------------------------

# Expand the requested bounding box by this margin (degrees) to ensure the
# network contains both start and end points.
BBOX_MARGIN_DEGREES = 0.02

# We separate the travel cost (metres) and risk penalty.
LENGTH_WEIGHT = 1.0          # metres weight in overall cost
RISK_PENALTY_WEIGHT = 500.0  # penalty per unit risk (tune higher to favour safety)

# ---------------------------------------------------------------------------
# In-memory cache to avoid rebuilding graphs for the same bounding box.
# Keyed by (north, south, east, west) rounded to 4 dp.
# ---------------------------------------------------------------------------
_graph_cache: dict[Tuple[float, float, float, float], nx.MultiDiGraph] = {}

def _round_bbox(north: float, south: float, east: float, west: float) -> Tuple[float, float, float, float]:
    return tuple(round(x, 4) for x in (north, south, east, west))


# ---------------------------------------------------------------------------
# Graph construction & risk enrichment
# ---------------------------------------------------------------------------

def _build_graph(north: float, south: float, east: float, west: float) -> nx.MultiDiGraph:
    """Download street network for the specified bbox and enrich with risk."""
    key = _round_bbox(north, south, east, west)
    if key in _graph_cache:
        return _graph_cache[key]

    # 1. Download the drivable street network from OSM.
    G = ox.graph_from_bbox(north, south, east, west, network_type="drive")

    # 2. Calculate a naive risk score for every edge. Replace this logic with
    #    domain-specific computations that incorporate hazard zones, blockages,
    #    etc. Risk is in [0,1].
    rng = random.Random(42)  # deterministic for reproducibility

    # iterate over a list snapshot because we may remove edges
    for u, v, k, data in list(G.edges(keys=True, data=True)):
        # Skip bridges/tunnels assumed collapsed/unusable
        if data.get("bridge") not in (None, "no") or data.get("tunnel") not in (None, "no"):
            G.remove_edge(u, v, key=k)
            continue
        length = data.get("length", 1.0)
        ls = LineString([
            (G.nodes[u]["x"], G.nodes[u]["y"]),
            (G.nodes[v]["x"], G.nodes[v]["y"]),
        ])
        # If a hazard model is available, use it; otherwise fallback to tiny random risk
        if hazard_model is not None:
            risk_score = hazard_model.compute_edge_risk(ls, length)
        else:
            risk_score = rng.random() * 0.05

        # Extra penalty for bridges and tunnels unless explicitly cleared
        if data.get("bridge") not in (None, "no") or data.get("tunnel") not in (None, "no"):
            risk_score = max(risk_score, 0.8)  # treat as very risky
        data["risk_score"] = risk_score
        data["risk_weight"] = length * LENGTH_WEIGHT + risk_score * RISK_PENALTY_WEIGHT

    _graph_cache[key] = G
    return G


def load_hazard_zones() -> gpd.GeoDataFrame | None:
    """Load optional hazard polygons to compute more realistic risk scores."""
    if gpd is None:
        return None
    hz_path = Path(__file__).parent / "data" / "hazard_zones.geojson"
    if hz_path.exists():
        return gpd.read_file(hz_path)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _path_coords_and_risk(path_nodes, G):
    """Convert list of nodes to (lat,lon) coords and accumulate risk.

    Works for both MultiDiGraph (edge data under key) and DiGraph (edge data dict).
    """
    total_risk = 0.0
    coords: list[tuple[float, float]] = []
    for idx, (u, v) in enumerate(zip(path_nodes[:-1], path_nodes[1:])):
        data = G.get_edge_data(u, v)
        # MultiDiGraph returns {key: attr_dict}; DiGraph returns attr_dict directly
        if data is None:
            continue  # should not happen
        if "risk_weight" in data:
            edge = data  # DiGraph
        else:
            # pick the first key (lowest risk); values are attr_dicts
            first_key = next(iter(data))
            edge = data[first_key]
        total_risk += edge.get("risk_weight", 0.0)

        if "geometry" in edge:
            seg = [(lat, lon) for lon, lat in edge["geometry"].coords]
        else:
            seg = [
                (G.nodes[u]["y"], G.nodes[u]["x"]),
                (G.nodes[v]["y"], G.nodes[v]["x"]),
            ]
        # avoid duplicating vertices between segments
        if idx > 0:
            seg = seg[1:]
        coords.extend(seg)
    return coords, float(total_risk)



def alternative_routes(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    k: int = 5,
) -> List[Tuple[List[Tuple[float, float]], float]]:
    """Return up to *k* safest distinct routes sorted by risk."""
    # Build graph and locate nodes similar to safest_route
    north = max(start_lat, end_lat) + BBOX_MARGIN_DEGREES
    south = min(start_lat, end_lat) - BBOX_MARGIN_DEGREES
    east = max(start_lon, end_lon) + BBOX_MARGIN_DEGREES
    west = min(start_lon, end_lon) - BBOX_MARGIN_DEGREES
    G_multi = _build_graph(north, south, east, west)
    orig = ox.nearest_nodes(G_multi, start_lon, start_lat)
    dest = ox.nearest_nodes(G_multi, end_lon, end_lat)

    # Convert MultiDiGraph to simple DiGraph keeping lowest-risk edge between each node pair
    G = nx.DiGraph()
    G.add_nodes_from(G_multi.nodes(data=True))

    for u, v, data in G_multi.edges(data=True):
        w = data["risk_weight"]
        existing = G.get_edge_data(u, v)
        if existing is None or w < existing["risk_weight"]:
            # overwrite (or add) edge with lower risk weight and full attributes
            G.add_edge(u, v, **data)

    # Generate up to *k* simple paths ordered by total risk (Yen's algorithm)
    try:
        path_gen = nx.shortest_simple_paths(G, orig, dest, weight="risk_weight")
        graph_for_path = G
    except nx.NetworkXNoPath:
        # fallback: ignore one-way restrictions using an undirected graph
        G_u = G.to_undirected(as_view=False)
        if not nx.has_path(G_u, orig, dest):
            raise ValueError("No viable evacuation path between the selected points.")
        path_gen = nx.shortest_simple_paths(G_u, orig, dest, weight="risk_weight")
        graph_for_path = G_u

    routes: list[Tuple[List[Tuple[float, float]], float]] = []
    for path_nodes in path_gen:
        coords, risk = _path_coords_and_risk(path_nodes, graph_for_path)
        routes.append((coords, risk))
        if len(routes) >= k:
            break
    # sort by risk ascending (generator already does, but to be sure)
    routes.sort(key=lambda x: x[1])
    return routes


def safest_route(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
) -> Tuple[List[Tuple[float, float]], float]:
    """Compute the safest path between two coordinates.

    Returns
    -------
    list of (lat, lon): Path coordinates for visualisation.
    float: Total accumulated risk weight.
    """
    # Expand bbox with margin
    north = max(start_lat, end_lat) + BBOX_MARGIN_DEGREES
    south = min(start_lat, end_lat) - BBOX_MARGIN_DEGREES
    east = max(start_lon, end_lon) + BBOX_MARGIN_DEGREES
    west = min(start_lon, end_lon) - BBOX_MARGIN_DEGREES

    G = _build_graph(north, south, east, west)

    # Map coords to nearest graph nodes
    orig = ox.nearest_nodes(G, start_lon, start_lat)
    dest = ox.nearest_nodes(G, end_lon, end_lat)

    # NetworkX shortest path using risk_weight
    def _heuristic(a: int, b: int) -> float:
        # straight-line haversine distance (metres)
        lat1, lon1 = G.nodes[a]["y"], G.nodes[a]["x"]
        lat2, lon2 = G.nodes[b]["y"], G.nodes[b]["x"]
        R = 6371000  # Earth radius metres
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        h = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dl/2)**2
        dist = 2*R*math.asin(math.sqrt(h))
        # Minimum possible risk weight per metre
        return dist * (1.0)

    try:
        path_nodes = nx.astar_path(G, orig, dest, heuristic=_heuristic, weight="risk_weight")
    except nx.NetworkXNoPath:
        # fallback: ignore one-way by using undirected graph
        G_u = G.to_undirected(as_view=False)
        try:
            path_nodes = nx.shortest_path(G_u, orig, dest, weight="risk_weight")
        except nx.NetworkXNoPath as e:
            raise ValueError("No viable evacuation path between the selected points even after relaxing direction.") from e

    path_coords, total_risk = _path_coords_and_risk(path_nodes, G)
    return path_coords, total_risk


def risk_map_geojson(north: float, south: float, east: float, west: float) -> str:
    """Return a GeoJSON FeatureCollection of edges with risk score attribute."""
    G = _build_graph(north, south, east, west)

    features = []
    for u, v, k, data in G.edges(keys=True, data=True):
        coords = [
            (G.nodes[u]["x"], G.nodes[u]["y"]),
            (G.nodes[v]["x"], G.nodes[v]["y"]),
        ]
        linestring = LineString(coords)
        feature = {
            "type": "Feature",
            "geometry": mapping(linestring),
            "properties": {
                "risk_score": data.get("risk_score", 0.0),
                "risk_weight": data.get("risk_weight", 0.0),
            },
        }
        features.append(feature)

    collection = {"type": "FeatureCollection", "features": features}
    return json.dumps(collection)
