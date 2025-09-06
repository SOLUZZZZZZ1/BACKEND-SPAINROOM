# app.py
from flask import Flask, jsonify
from flask_cors import CORS

def create_app():
    app = Flask(__name__)
    CORS(app, resources={r"/*": {"origins": "*"}})

    # ------------------- Health -------------------
    @app.get("/health")
    def health():
        return jsonify({"ok": True, "service": "BACKEND-SPAINROOM"})

    # ------------------- Defense (opcional) -------------------
    try:
        import defense
        if hasattr(defense, "bp_defense"):
            app.register_blueprint(defense.bp_defense, url_prefix="/defense")
        if hasattr(defense, "init_defense"):
            defense.init_defense(app)  # ¡ojo: 'app', no 'aplicación'!
        print("[DEFENSE] Activa.")
    except Exception as e:
        print(f"[DEFENSE] No se pudo activar: {e}")

    # ------------------- Auth -------------------
    try:
        import auth
        if hasattr(auth, "bp_auth"):
            app.register_blueprint(auth.bp_auth, url_prefix="/api/auth")
        print("[AUTH] Blueprint auth registrado.")
    except Exception as e:
        print(f"[AUTH] No se pudo cargar: {e}")

    # ------------------- Oportunidades -------------------
    try:
        import opportunities
        if hasattr(opportunities, "bp_opps"):
            app.register_blueprint(opportunities.bp_opps, url_prefix="/api/opps")
        print("[OPPORTUNITIES] Blueprint registrado.")
    except Exception as e:
        print(f"[OPPORTUNITIES] No se pudo cargar: {e}")

    # ------------------- Pagos -------------------
    try:
        import payments
        if hasattr(payments, "bp_payments"):
            app.register_blueprint(payments.bp_payments, url_prefix="/api/payments")
        print("[PAYMENTS] Blueprint registrado.")
    except Exception as e:
        print(f"[PAYMENTS] No se pudo cargar: {e}")

    # ------------------- Bot de voz -------------------
    try:
        import voice_bot
        if hasattr(voice_bot, "bp_voice"):
            app.register_blueprint(voice_bot.bp_voice, url_prefix="/voice")
        print("[VOICE] Blueprint voice registrado.")
    except Exception as e:
        print(f"[VOICE] No se pudo cargar: {e}")

    # ------------------- Rutas raíz -------------------
    @app.get("/")
    def root():
        # Página sencilla para confirmar que está levantado
        return (
            "<h1>SpainRoom Backend</h1>"
            "<p>Usa <code>/health</code> para estado y los prefijos "
            "<code>/api/*</code>, <code>/voice/*</code> para servicios.</p>"
        ), 200

    return app

# Para gunicorn: app:app
app = create_app()
