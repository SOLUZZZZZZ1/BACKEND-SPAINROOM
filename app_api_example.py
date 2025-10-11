# app_api_example.py — Cómo registrar los módulos en backend-API
# Nora · 2025-10-11
from flask import Flask, jsonify

from routes_cedula import bp_legal
from routes_catastro import bp_cat
from routes_owner import bp_owner

def create_app():
    app = Flask(__name__)

    # health
    @app.get("/health")
    @app.get("/healthz")
    def health():
        return jsonify(ok=True)

    # Blueprints (sin prefijo adicional porque ya llevan /api/... en las rutas)
    app.register_blueprint(bp_legal)
    app.register_blueprint(bp_cat)
    app.register_blueprint(bp_owner)

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=10000, debug=True)
