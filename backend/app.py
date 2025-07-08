"""Flask API for Sheltr smart evacuation routing"""
from __future__ import annotations

import os
from dotenv import load_dotenv
from functools import lru_cache
from typing import Tuple

from flask import Flask, jsonify, request, render_template

from .routing import risk_map_geojson, safest_route, alternative_routes

load_dotenv()
app = Flask(__name__, static_folder="static", template_folder="templates")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _default_bbox() -> Tuple[float, float, float, float]:
    """Default bbox read from env vars or fall back to global coverage."""
    north = float(os.getenv("SHELTR_NORTH", 90))
    south = float(os.getenv("SHELTR_SOUTH", -90))
    east = float(os.getenv("SHELTR_EAST", 180))
    west = float(os.getenv("SHELTR_WEST", -180))
    return north, south, east, west


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def index() -> "flask.Response":  # type: ignore[override]
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    return render_template("index.html", api_key=api_key)


@app.route("/api")
def api_root() -> tuple:  # type: ignore[override]
    """Health/info JSON endpoint"""
    return jsonify(
        {
            "message": "Sheltr API running",
            "endpoints": {
                "/route": "GET with start=lat,lon&end=lat,lon",
                "/riskmap": "GET risk GeoJSON for default bbox",
            },
        }
    )


@app.route("/routes")
def routes() -> tuple:  # type: ignore[override]
    start = request.args.get("start")
    end = request.args.get("end")
    k = int(request.args.get("k", 5))
    if not start or not end:
        return jsonify({"error": "start and end query params required"}), 400
    try:
        slat, slon = map(float, start.split(","))
        elat, elon = map(float, end.split(","))
    except ValueError:
        return jsonify({"error": "Invalid coordinates"}), 400
    try:
        routes = alternative_routes(slat, slon, elat, elon, k)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({
        "routes": [
            {"path": coords, "total_risk": risk} for coords, risk in routes
        ]
    })

@app.route("/route")
def route() -> tuple:  # type: ignore[override]
    try:
        start = request.args.get("start")
        end = request.args.get("end")
        if not start or not end:
            return jsonify({"error": "start and end query params required"}), 400

        slat, slon = map(float, start.split(","))
        elat, elon = map(float, end.split(","))
    except ValueError:
        return jsonify({"error": "Invalid coordinates"}), 400

    try:
        path, risk = safest_route(slat, slon, elat, elon)
    except Exception as exc:  # pragma: no cover
        return jsonify({"error": str(exc)}), 500

    return jsonify({"path": path, "total_risk": risk})


@app.route("/riskmap")
def riskmap() -> tuple:  # type: ignore[override]
    north, south, east, west = _default_bbox()
    geojson = risk_map_geojson(north, south, east, west)
    return app.response_class(geojson, mimetype="application/geo+json")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    # Useful for local dev: FLASK_DEBUG=1 python -m Sheltr.backend.app
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
