from flask import Flask, jsonify
from flask_cors import CORS

def create_app():
    app = Flask(__name__)
    CORS(app, resources={r"/*": {"origins": "*"}})

    # ---------- Rutas básicas ----------
    @app.route("/")
    def root():
        return jsonify({"ok": True, "service": "BACKEND-SPAINROOM"})

    @app.route("/health")
    def health():
        return jsonify({"ok": True, "service": "BACKEND-SPAINROOM"})

    @app.get("/__routes")
    def list_routes():
        output = []
        for rule in app.url_map.iter_rules():
            methods = ",".join(sorted(rule.methods - {"HEAD", "OPTIONS"}))
            output.append({
                "rule": str(rule),
                "endpoint": rule.endpoint,
                "methods": methods
            })
        return jsonify({"count": len(output), "routes": output})

    # ---------- Defense ----------
    try:
        import defense
defense.init_defense(app)

    else:
        app.register_blueprint(bp_defense)
        print("[DEFENSE] Activada.")

    # ---------- Auth ----------
    try:
        from auth import bp_auth
    except Exception as e:
        print("[AUTH] No se pudo cargar:", e)
    else:
        app.register_blueprint(bp_auth, url_prefix="/api/auth")
        print("[AUTH] Blueprint auth registrado.")

    # ---------- Opportunities ----------
    try:
        from opportunities import bp_opps
    except Exception as e:
        print("[OPPORTUNITIES] No se pudo cargar:", e)
    else:
        app.register_blueprint(bp_opps, url_prefix="/api/opps")
        print("[OPPORTUNITIES] Blueprint registrado.")

    # ---------- Payments ----------
    try:
        from payments import bp_payments
    except Exception as e:
        print("[PAGOS] No se pudo cargar:", e)
    else:
        app.register_blueprint(bp_payments)
        print("[PAGOS] Blueprint registrado.")

    # ---------- Voice ----------
    try:
        from voice_bot import bp_voice
    except Exception as e:
        print("[VOICE] No se pudo cargar:", e)
    else:
        app.register_blueprint(bp_voice, url_prefix="/voice")
        print("[VOICE] Blueprint voice registrado.")

    return app


# Necesario para Render con: gunicorn app:app
app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
