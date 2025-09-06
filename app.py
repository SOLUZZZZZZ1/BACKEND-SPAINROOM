from flask import Flask, jsonify
from flask_cors import CORS

def create_app():
    app = Flask(__name__)
    CORS(app, resources={r"/*": {"origins": "*"}})

    # -------------------
    # Ruta básica de salud
    # -------------------
    @app.route("/health")
    def health():
        return jsonify({"ok": True, "service": "BACKEND-SPAINROOM"})

    # -------------------
    # Defensa
    # -------------------
    try:
        from defense import setup_defense
        setup_defense(app)
        print("[DEFENSE] Defensa activada.")
    except Exception as e:
        print(f"[DEFENSE] No se pudo activar: {e}")

    # -------------------
    # Autenticación
    # -------------------
    try:
        from auth import bp_auth
        app.register_blueprint(bp_auth, url_prefix="/api/auth")
        print("[AUTH] Blueprint auth registrado.")
    except Exception as e:
        print(f"[AUTH] No se pudo cargar: {e}")

    # -------------------
    # Oportunidades
    # -------------------
    try:
        from opportunities import bp_opps
        app.register_blueprint(bp_opps, url_prefix="/api/opportunities")
        print("[OPPORTUNITIES] Blueprint registrado.")
    except Exception as e:
        print(f"[OPPORTUNITIES] No se pudo cargar: {e}")

    # -------------------
    # Pagos
    # -------------------
    try:
        from payments import bp_payments
        app.register_blueprint(bp_payments, url_prefix="/api/payments")
        print("[PAYMENTS] Blueprint registrado.")
    except Exception as e:
        print(f"[PAYMENTS] No se pudo cargar: {e}")

    # -------------------
    # Bot de voz (Twilio)
    # -------------------
    try:
        from voice_bot import bp_voice
        app.register_blueprint(bp_voice, url_prefix="/voice")
        print("[VOICE] Blueprint voice registrado.")
    except Exception as e:
        print(f"[VOICE] No se pudo cargar: {e}")

    return app


# 👉 Objeto que usará Render con gunicorn app:app
app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)

   
