from flask import Flask, request, jsonify
import requests
from math import radians, sin, cos, sqrt, atan2

app = Flask(__name__)

# Función para calcular distancia entre dos coordenadas (Haversine)
def calcular_distancia(lat1, lon1, lat2, lon2):
    R = 6371  # radio de la Tierra en km
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

# --- 1. Geocodificación ---
@app.route("/api/geocode")
def geocode():
    address = request.args.get("address")
    if not address:
        return jsonify({"error": "Falta parámetro address"}), 400

    url = f"https://nominatim.openstreetmap.org/search?q={address}&format=json&limit=1"
    headers = {"User-Agent": "SpainRoom/1.0"}  # Nominatim exige User-Agent
    r = requests.get(url, headers=headers)

    if r.status_code != 200 or not r.json():
        return jsonify({"error": "No se pudo geocodificar"}), 500

    data = r.json()[0]
    return jsonify({
        "lat": float(data["lat"]),
        "lng": float(data["lon"])
    })

# --- 2. Búsqueda de empleos (mock inicial con cálculo real de distancias) ---
@app.route("/api/jobs/search")
def search_jobs():
    try:
        lat = float(request.args.get("lat"))
        lng = float(request.args.get("lng"))
        radius = float(request.args.get("radius_km", 2))
        keyword = request.args.get("q", "").lower()
    except:
        return jsonify({"error": "Parámetros inválidos"}), 400

    # Base de ofertas ficticias (simulamos posiciones reales en la ciudad)
    ofertas = [
        {"id": 1, "titulo": "Camarero/a", "empresa": "Bar Central", "lat": lat + 0.01, "lng": lng + 0.01},
        {"id": 2, "titulo": "Dependiente/a", "empresa": "Tienda Local", "lat": lat + 0.015, "lng": lng},
        {"id": 3, "titulo": "Administrativo/a", "empresa": "Gestoría", "lat": lat - 0.02, "lng": lng - 0.01},
        {"id": 4, "titulo": "Carpintero/a", "empresa": "Taller Madera", "lat": lat + 0.03, "lng": lng + 0.02},
    ]

    resultados = []
    for oferta in ofertas:
        dist = calcular_distancia(lat, lng, oferta["lat"], oferta["lng"])
        if dist <= radius:
            if not keyword or keyword in oferta["titulo"].lower():
                resultados.append({
                    "id": oferta["id"],
                    "titulo": oferta["titulo"],
                    "empresa": oferta["empresa"],
                    "distancia_km": round(dist, 2)
                })

    return jsonify(resultados)

if __name__ == "__main__":
    app.run(debug=True)
