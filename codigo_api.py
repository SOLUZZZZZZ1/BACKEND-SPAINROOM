# codigo_api.py — SpainRoom API ONLY (Flask + SQLAlchemy)
import os
from flask import Flask, jsonify, request
from flask_cors import CORS
from extensions import db
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import OperationalError
import psycopg2

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

def _import_models(app: Flask):
    """Importa modelos ANTES de create_all()."""
    for modname in [
        "models_rooms",
        "models_auth",
        "models_contracts",
        "models_uploads",
        "models_franchise",
        "models_reservas",
        "models_remesas",
        "models_leads",
        # añadidos para cobertura completa:
        "models_contact",
        "models_roomleads",
        "models_kyc",
        "models_franchise_slots",   # <- IMPRESCINDIBLE para franchise_slots
    ]:
        try:
            __import__(modname)
        except Exception as e:
            app.logger.warning("Model skip: %s (%s)", modname, e)

def _create_database_if_missing(app: Flask, db_uri: str) -> str:
    """
    Si la BD no existe: se conecta a 'postgres' y la crea con el mismo owner.
    Devuelve la URI final (la original).
    """
    url = make_url(db_uri)
    if url.get_backend_name() != "postgresql":
        return db_uri

    target_db = url.database
    host = url.host
    port = url.port or 5432
    user = url.username
    pwd  = url.password
    sslmode = url.query.get("sslmode", "require")

    app.logger.warning('Intentando crear BD "%s" en %s:%s ...', target_db, host, port)
    conn = None
    try:
        conn = psycopg2.connect(
            dbname="postgres", user=user, password=pwd, host=host, port=port, sslmode=sslmode
        )
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (target_db,))
            exists = cur.fetchone() is not None
            if not exists:
                owner = user or "spainroom_user"
                cur.execute(f"CREATE DATABASE {target_db} WITH OWNER = {owner} ENCODING 'UTF8' TEMPLATE template1;")
                app.logger.info('BD "%s" creada con OWNER "%s".', target_db, owner)
            else:
                app.logger.info('BD "%s" ya existía.', target_db)
    except Exception as e:
        app.logger.exception("No se pudo crear la BD automáticamente: %s", e)
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass

    return db_uri

def create_app():
    app = Flask(__name__)

    # -------------------- Config --------------------
    app.config["SECRET_KEY"] = env("SECRET_KEY", "sr-dev-secret")
    db_uri = env("DATABASE_URL", "sqlite:///spainroom.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = db_uri

    # ✅ AÑADIDO: opciones del engine para conexiones estables (evita SSL/conn stale)
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,   # valida la conexión antes de usarla
        "pool_recycle": 300,     # recicla conexiones cada 5 min
        "pool_size": 5,
        "max_overflow": 10,
    }

    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # CORS
    CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)

    # -------------------- DB init --------------------
    db.init_app(app)

    # 1) Importa modelos
    _import_models(app)

    # 2) Intenta create_all() si se solicita; si falla por "database does not exist", crea BD y reintenta
    with app.app_context():
        try:
            # Solo si activas explícitamente la variable (no en prod por defecto)
            CREATE_ALL = os.getenv("SPAINROOM_CREATE_ALL", "false").lower() in {"1", "true", "yes"}
            if CREATE_ALL:
                try:
                    db.create_all()
                    app.logger.info("DB create_all() OK (uri=%s)", app.config.get("SQLALCHEMY_DATABASE_URI"))
                except OperationalError as oe:
                    msg = str(oe).lower()
                    if "database" in msg and "does not exist" in msg:
                        app.logger.warning('La BD objetivo no existe. Intentando crearla automáticamente...')
                        final_uri = _create_database_if_missing(app, db_uri)
                        app.config["SQLALCHEMY_DATABASE_URI"] = final_uri
                        db.engine.dispose()
                        try:
                            db.create_all()
                            app.logger.info("DB create_all() OK tras crear BD (uri=%s)", final_uri)
                        except Exception as e2:
                            app.logger.exception("DB create_all() failed tras crear BD: %s", e2)
                    else:
                        app.logger.exception("DB create_all() failed: %s", oe)
        except Exception as e:
            app.logger.exception("DB init failed: %s", e)

    # -------------------- Rutas de salud/diag y raíz --------------------
    @app.get("/health")
    def health():
        return jsonify(ok=True, service="spainroom-api")

    @app.get("/diag")
    def diag():
        return jsonify(
            ok=True,
            db_uri=app.config.get("SQLALCHEMY_DATABASE_URI", "sqlite"),
            blueprints=list(app.blueprints.keys()),
        )

    @app.get("/")
    def root():
        return jsonify(ok=True, service="spainroom-api",
                       hint="use /health, /diag, /api/* or POST /sms/inbound")

    # CORS fino
    @app.after_request
    def add_cors_headers(resp):
        origin = request.headers.get("Origin")
        if _allowed_origin(origin):
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Admin-Key, X-Franquiciado, X-User-Id"
            resp.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
        return resp

    # -------------------- Blueprints --------------------
    _try_register(app, "routes_rooms",             "bp_rooms",        None)
    _try_register(app, "routes_contracts",         "bp_contracts",    None)
    _try_register(app, "routes_contact",           "bp_contact",      None)
    _try_register(app, "routes_auth",              "bp_auth",         None)
    _try_register(app, "routes_franchise",         "bp_franchise",    None)  # /api/franchise/*
    _try_register(app, "routes_kyc",               "bp_kyc",          None)
    _try_register(app, "routes_reservas",          "bp_reservas",     None)
    _try_register(app, "routes_remesas",           "bp_remesas",      None)
    _try_register(app, "routes_leads",             "bp_leads",        None)
    _try_register(app, "routes_uploads_rooms",     "bp_upload_rooms", None)
    _try_register(app, "routes_upload_generic",    "bp_upload_generic", None)
    _try_register(app, "routes_sms",               "bp_sms",          "/sms")
    _try_register(app, "routes_admin_franchise",   "bp_admin_franq",  None)  # /api/admin/franquicia/*

    return app

if __name__ == "__main__":
    app = create_app()
    # En local: por defecto 127.0.0.1:5000
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=True)
