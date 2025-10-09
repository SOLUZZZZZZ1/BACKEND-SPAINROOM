# app.py — SpainRoom BACKEND (cédula-first, estricto y veraz)
# - Conexión Postgres robusta (Render) con SSL y pool sano
# - Endpoints con OPTIONS (preflight) → 204
# - /api/legal/requirement lee de legal_requirements (municipio→provincia→default)
# - /api/legal/cedula/check devuelve has_doc/status desde owner_cedulas (sin inventar)
# - Catastro estricto (503 si no disponible; no se inventan datos)
# - Owner: check + upload
# Nota: /api/rooms/* lo gestiona vuestro blueprint (si lo registras). Aquí nos centramos en cédula.

import os, sys, types, logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from flask import Flask, jsonify, request, current_app
from flask_cors import CORS
from werkzeug.utils import secure_filename
from sqlalchemy import text

# ---------- DB bootstrap ----------
try:
    from extensions import db  # si existe módulo; si no, creamos uno dinámico
except Exception:
    from flask_sqlalchemy import SQLAlchemy
    db = SQLAlchemy()
    mod = types.ModuleType("extensions"); mod.db = db; sys.modules["extensions"] = mod

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = f"sqlite:///{(BASE_DIR / 'spainroom.db').as_posix()}"

SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", DEFAULT_DB)
_raw = os.environ.get("DATABASE_URL")
if _raw:
    if _raw.startswith("postgres://"):
        _raw = _raw.replace("postgres://", "postgresql+psycopg2://", 1)
    elif _raw.startswith("postgresql://"):
        _raw = _raw.replace("postgresql://", "postgresql+psycopg2://", 1)
    # fuerza SSL en Render
    if "sslmode=" not in _raw and "+psycopg2://" in _raw:
        _raw += ("&" if "?" in _raw else "?") + "sslmode=require"
    SQLALCHEMY_DATABASE_URI = _raw

ENGINE_OPTIONS = {
    "pool_pre_ping": True,   # revalida conexiones del pool
    "pool_recycle": 300,     # recicla cada 5 min
}

# ---------- App factory ----------
def create_app(test_config=None):
    app = Flask(__name__, static_folder="public", static_url_path="/")
    try:
        Path(app.instance_path).mkdir(parents=True, exist_ok=True)
        Path(app.instance_path, "uploads").mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    app.config.update(
        SQLALCHEMY_DATABASE_URI=SQLALCHEMY_DATABASE_URI,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS=ENGINE_OPTIONS,
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,  # 20MB uploads
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
    _init_logging(app)

    # --------- (Opcional) registra tus blueprints si existen ---------
    try:
        from routes_rooms import bp_rooms
        app.register_blueprint(bp_rooms)
    except Exception:
        pass

    # --------- CORS global extra (respeta preflight) ---------
    ALLOWED_ORIGINS = {
        "http://localhost:5176", "http://127.0.0.1:5176"
        # añade tu vercel si aplica: "https://tu-frontend.vercel.app",
    }
    @app.after_request
    def add_cors_headers(resp):
        origin = request.headers.get("Origin")
        if origin and (origin in ALLOWED_ORIGINS or origin.endswith(".vercel.app")):
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Admin-Key, X-Franquiciado"
            resp.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
        return resp

    # --------- Health ----------
    @app.get("/health")
    def health():
        return jsonify(ok=True, service="spainroom-backend")

    # --------- OWNER mínimos ----------
    @app.route("/api/owner/check", methods=["POST", "OPTIONS"])
    def owner_check():
        if request.method == "OPTIONS":
            return ("", 204)
        body = request.get_json(silent=True) or {}
        import uuid
        return jsonify(ok=True, id="SRV-CHK-" + uuid.uuid4().hex[:8], echo=body)

    @app.route("/api/owner/cedula/upload", methods=["POST", "OPTIONS"])
    def cedula_upload():
        if request.method == "OPTIONS":
            return ("", 204)
        f = request.files.get("file")
        if not f:
            return jsonify(ok=False, error="no_file"), 400
        filename = secure_filename(f.filename or "file")
        up = Path(current_app.instance_path) / "uploads"
        up.mkdir(parents=True, exist_ok=True)
        tgt = up / filename
        f.save(tgt)
        return jsonify(ok=True, filename=filename, size=tgt.stat().st_size)

    # --------- CATASTRO estricto (no mock) ----------
    @app.route("/api/catastro/resolve_direccion", methods=["POST", "OPTIONS"])
    def resolve_direccion():
        if request.method == "OPTIONS":
            return ("", 204)
        body = request.get_json(silent=True) or {}
        if not all((body.get("direccion"), body.get("municipio"), body.get("provincia"))):
            return jsonify(ok=False, error="bad_request", message="Faltan direccion/municipio/provincia"), 400
        # Sin integración SOAP: no inventamos
        return jsonify(ok=False, error="catastro_unavailable", message="Servicio Catastro no disponible"), 503

    @app.route("/api/catastro/consulta_refcat", methods=["POST", "OPTIONS"])
    def consulta_refcat():
        if request.method == "OPTIONS":
            return ("", 204)
        body = request.get_json(silent=True) or {}
        refcat = (body.get("refcat") or "").strip()
        if len(refcat) != 20 or not refcat.isalnum():
            return jsonify(ok=False, error="bad_refcat", message="La referencia catastral debe tener 20 caracteres alfanuméricos."), 400
        # Sin integración SOAP: no inventamos
        return jsonify(ok=False, error="catastro_unavailable", message="Servicio Catastro no disponible"), 503

    # --------- LEGAL: requisito por zona (DB real) ----------
    @app.route("/api/legal/requirement", methods=["POST", "OPTIONS"])
    def legal_requirement():
        if request.method == "OPTIONS":
            return ("", 204)
        b = request.get_json(silent=True) or {}
        municipio = (b.get("municipio") or "").strip()
        provincia = (b.get("provincia") or "").strip()
        if not provincia:
            return jsonify(ok=False, error="bad_request", message="Falta 'provincia'"), 400

        sql = text("""
            WITH q AS (
              SELECT cat, doc, org, vig, notas, link
              FROM legal_requirements
              WHERE
                (municipality_key = unaccent(lower(coalesce(:mun,''))) AND province_key = unaccent(lower(:prov)))
                OR (municipality IS NULL AND province_key = unaccent(lower(:prov)))
              ORDER BY (municipality IS NOT NULL) DESC
              LIMIT 1
            )
            SELECT * FROM q
        """)
        row = None
        try:
            with db.engine.connect() as c:
                r = c.execute(sql, {"mun": municipio, "prov": provincia}).mappings().first()
                if r: row = dict(r)
        except Exception as e:
            current_app.logger.warning("legal_requirement DB error: %s", e)

        if not row:
            row = {"cat":"no","doc":"—","org":"—","vig":"—","notas":"Sin datos para provincia/municipio.","link":None}

        return jsonify(ok=True, requirement=row)

    # --------- LEGAL: ¿TIENE cédula en vigor? (DB owner_cedulas) ----------
    @app.route("/api/legal/cedula/check", methods=["POST", "OPTIONS"])
    def cedula_check():
        if request.method == "OPTIONS":
            return ("", 204)
        b = request.get_json(silent=True) or {}
        refcat = (b.get("refcat") or "").strip()
        cedula_num = (b.get("cedula_numero") or "").strip()
        if not (refcat or cedula_num):
            return jsonify(ok=False, error="bad_request", message="Pasa refcat o cedula_numero"), 400

        sql = text("""
            SELECT refcat, cedula_numero, estado, expires_at, verified_at, source, notes, created_at
            FROM v_owner_cedulas_last
            WHERE (:num IS NOT NULL AND cedula_numero = :num)
               OR (:ref IS NOT NULL AND refcat = :ref)
            ORDER BY created_at DESC
            LIMIT 1
        """)
        row = None
        try:
            with db.engine.connect() as c:
                r = c.execute(sql, {"num": cedula_num or None, "ref": refcat or None}).mappings().first()
                if r: row = dict(r)
        except Exception as e:
            current_app.logger.warning("cedula_check DB error: %s", e)

        if not row:
            return jsonify(ok=True, has_doc=False, status="no_consta",
                           detail="Sin registro interno. Aporta cédula o espera verificación oficial."), 200

        estado = (row.get("estado") or "no_consta").lower()
        has_doc = (estado == "vigente")
        return jsonify(ok=True, has_doc=has_doc, status=estado, data=row), 200

    # --------- Admin: registrar/actualizar estado documental ----------
    @app.route("/api/admin/cedula/upsert", methods=["POST", "OPTIONS"])
    def admin_cedula_upsert():
        if request.method == "OPTIONS":
            return ("", 204)
        b = request.get_json(silent=True) or {}
        refcat = (b.get("refcat") or "").strip() or None
        cedula_num = (b.get("cedula_numero") or "").strip() or None
        estado = (b.get("estado") or "pendiente").strip().lower()
        expires_at = (b.get("expires_at") or None)
        source = (b.get("source") or "manual").strip().lower()
        notes = b.get("notes")

        if estado not in ("vigente", "caducada", "no_consta", "pendiente"):
            return jsonify(ok=False, error="bad_request", message="estado inválido"), 400
        if not (refcat or cedula_num):
            return jsonify(ok=False, error="bad_request", message="refcat o cedula_numero requerido"), 400

        sql = text("""
            INSERT INTO owner_cedulas (refcat, cedula_numero, estado, expires_at, verified_at, source, notes)
            VALUES (:refcat, :cedula_numero, :estado, :expires_at, NOW(), :source, :notes)
        """)
        try:
            with db.engine.begin() as c:
                c.execute(sql, {
                    "refcat": refcat, "cedula_numero": cedula_num,
                    "estado": estado, "expires_at": expires_at,
                    "source": source, "notes": notes
                })
        except Exception as e:
            current_app.logger.warning("admin_cedula_upsert error: %s", e)
            return jsonify(ok=False, error="server_error"), 500

        return jsonify(ok=True)

    # --------- Raíz y errores ----------
    @app.get("/")
    def index():
        return jsonify(ok=True, msg="SpainRoom API (cédula)")

    @app.errorhandler(404)
    def nf(e):
        return jsonify(ok=False, error="not_found", message="No encontrado"), 404

    @app.errorhandler(500)
    def se(e):
        app.logger.exception("500")
        return jsonify(ok=False, error="server_error"), 500

    return app

# ---------- Logging ----------
def _init_logging(app):
    app.logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    sh = logging.StreamHandler(); sh.setFormatter(fmt); sh.setLevel(logging.INFO)
    app.logger.addHandler(sh)
    logs_dir = BASE_DIR / "logs"; logs_dir.mkdir(exist_ok=True)
    fh = RotatingFileHandler(logs_dir / "backend.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt); fh.setLevel(logging.INFO); app.logger.addHandler(fh)
    app.logger.info("Logging listo")

# ---------- Dev runner ----------
def run_dev():
    app = create_app()
    debug = os.environ.get("FLASK_DEBUG", "1") in ("1","true","True")
    port = int(os.environ.get("PORT", "5000"))
    app.logger.info("Dev http://127.0.0.1:%s (debug=%s)", port, debug)
    app.run(host="0.0.0.0", port=port, debug=debug)

if __name__ == "__main__":
    run_dev()
