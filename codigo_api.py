# codigo_api.py — SpainRoom API ONLY (Flask + SQLAlchemy)
import os
from flask import Flask, jsonify, request
from flask_cors import CORS
from extensions import db  # instancia SQLAlchemy compartida

def env(k, default=""):
    return os.getenv(k, default)

def _allowed_origin(origin: str | None) -> bool:
    if not origin:
        return False
    if origin.endswith(".vercel.app"):
        return True
    return origin in {
        "http://localhost:5176",
        "http://127.0.0.1:5176",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    }

def _try_register(app: Flask, module_name: str, attr: str, url_prefix: str | None = None):
    try:
        mod = __import__(module_name, fromlist=[attr])
        bp = getattr(mod, attr)
        if url_prefix:
            app.register_blueprint(bp, url_prefix=url_prefix)
        else:
            app.register_blueprint(bp)
        app.logger.info("BP OK: %s.%s -> %s", module_name, attr, url_prefix or "/")
    except Exception as e:
        app.logger.warning("BP SKIP: %s.%s (%s)", module_name, attr, e)

def create_app():
    app = Flask(__name__)

    app.config["SECRET_KEY"] = env("SECRET_KEY", "sr-dev-secret")
    app.config["SQLALCHEMY_DATABASE_URI"] = env("DATABASE_URL", "sqlite:///spainroom.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)

    db.init_app(app)
    with app.app_context():
        try:
            db.create_all()
            app.logger.info("DB create_all() OK")
        except Exception as e:
            app.logger.exception("DB create_all() failed: %s", e)

    @app.get("/health")
    def health():
        return jsonify(ok=True, service="spainroom-api")

    @app.get("/diag")
    def diag():
        return jsonify(
            ok=True,
            db_uri=app.config.get("SQLALCHEMY_DATABASE_URI","sqlite"),
            blueprints=list(app.blueprints.keys())
        )

    @app.after_request
    def add_cors_headers(resp):
        origin = request.headers.get("Origin")
        if _allowed_origin(origin):
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Admin-Key, X-Franquiciado"
            resp.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
        return resp

    # Blueprints (tolerantes)
    _try_register(app, "routes_rooms",      "bp_rooms",      None)
    _try_register(app, "routes_contracts",  "bp_contracts",  None)
    _try_register(app, "routes_contact",    "bp_contact",    None)
    _try_register(app, "routes_auth",       "bp_auth",       None)
    _try_register(app, "routes_franchise",  "bp_franchise",  None)
    _try_register(app, "routes_kyc",        "bp_kyc",        None)
    _try_register(app, "routes_reservas",   "bp_reservas",   None)
    _try_register(app, "routes_remesas",    "bp_remesas",    None)
    _try_register(app, "routes_leads",      "bp_leads",      None)
    _try_register(app, "routes_sms",        "bp_sms",        "/sms")

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=True)
