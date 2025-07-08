# Sheltr 🛟 – Safety-First Evacuation Router

Sheltr is a Python 3.11+ reference project that computes *safest* routes during natural-disaster evacuations. Instead of minimising travel time, Sheltr assigns **risk scores** to every road segment (blocked roads, proximity to flood zones, etc.) and finds the path with minimal accumulated risk.

---

## Features

🔸 OSM-based street network automatically downloaded for requested area  
🔸 Pluggable risk-scoring pipeline (flood zones, landslides, blockages)  
🔸 `/route` API returns safest path polyline + total risk  
🔸 `/riskmap` API returns GeoJSON heatmap of edge-level risk  
🔸 Stateless Flask backend – can be containerised or serverless  
🔸 Extensible: replace the toy random risk generator with real GeoTIFF layers or live sensor data

---

## Quickstart

```bash
# 1. Clone & install deps (ideally inside venv)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Run the API
python -m Sheltr.backend.app  # listens on :8000

# 3. Example: fetch safest route (Bangalore MG Road → Lalbagh)
curl "http://localhost:8000/route?start=12.9753,77.6065&end=12.9507,77.5848"
```

---

## Simulating Risk Data 🧪

If you lack real hazard datasets, the default implementation assigns a deterministic random risk ∈ [0,1] scaled by road length. To prototype more realistic scenarios:

1. Create `backend/data/hazard_zones.geojson` with polygons for flood/quake zones.  
2. Replace `routing._build_graph` risk logic with intersection tests between each edge `LineString` and polygons, assigning higher risk where they overlap.

---

## File Structure

```
Sheltr/
├── backend/
│   ├── __init__.py         # declares package
│   ├── app.py              # Flask API
│   ├── routing.py          # graph build + risk routing
│   └── data/               # optional hazard GeoJSON / rasters
├── requirements.txt
└── README.md
```

---

## Customising Risk Factors

Open `backend/routing.py` and tweak:

* `RISK_FACTOR_WEIGHT` – amplify or dampen risk penalisation.
* `_build_graph` – plug your hazard layers, live blockage feeds, or structural-integrity scores. Compute `risk_score ∈ [0,1]` for each edge then set `edge["risk_weight"] = length * (1 + weight * risk_score)`.

The router uses NetworkX shortest-path with these weights.

---

## Optional Frontend

You can rapidly prototype a Leaflet map:

```html
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />

<div id="map" style="height: 100vh;"></div>
<script>
  const map = L.map('map').setView([12.97, 77.59], 13);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);

  async function fetchRisk() {
    const res = await fetch('/riskmap');
    const geo = await res.json();
    L.geoJSON(geo, {
      style: f => ({ color: '#f00', opacity: f.properties.risk_score })
    }).addTo(map);
  }

  map.on('click', async e => {
    if (!window.start) {
      window.start = e.latlng;
      L.marker(e.latlng).addTo(map);
    } else {
      const url = `/route?start=${window.start.lat},${window.start.lng}&end=${e.latlng.lat},${e.latlng.lng}`;
      const res = await fetch(url);
      const data = await res.json();
      L.polyline(data.path, { color: 'blue' }).addTo(map);
      window.start = null;
    }
  });

  fetchRisk();
</script>
```

---

## Deployment

Sheltr is stateless; scale horizontally behind a load-balancer. Set environment vars `SHELTR_NORTH/SOUTH/EAST/WEST` to cache a pre-computed network covering your AOI.

```bash
SHELTR_NORTH=13.1 SHELTR_SOUTH=12.8 SHELTR_EAST=77.8 SHELTR_WEST=77.4 \
    gunicorn -m Sheltr.backend.app:app -b 0.0.0.0:8000 -w 4
```

---

## License

MIT © 2025 Sheltr Contributors
