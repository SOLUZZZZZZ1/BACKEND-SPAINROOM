# app_verify_cedula.py
from flask import Flask, jsonify
from flask_cors import CORS

from defense import init_defense
from voice_bot import bp_voice

def create_app():
    app = Flask(__name__)
    CORS(app)

    # Defensa (rate limiting, filtros UA, etc.)
    init_defense(app)

    # ✅ REGISTRA el bot de voz (/voice/answer, /voice/lang-or-intent, ...)
    app.register_blueprint(bp_voice)

    @app.get("/health")
    def health():
        return jsonify(ok=True)

    @app.get("/")
    def home():
        return "SpainRoom backend – cédula + voz OK"

    # Rutas de depuración para comprobar que está cargado el blueprint
    @app.get("/__routes")
    def routes():
        return jsonify(sorted([str(r) for r in app.url_map.iter_rules()]))

    return app

# Export para gunicorn: app_verify_cedula:app
app = create_app()
