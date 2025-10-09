
# app.py — backend centrado en cédula (estricto y veraz)
import os, sys, types, logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from flask import Flask, jsonify, request, current_app
from flask_cors import CORS
from werkzeug.utils import secure_filename
from sqlalchemy import text

# DB bootstrap
try:
    from extensions import db
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
    if "sslmode=" not in _raw and "+psycopg2://" in _raw:
        _raw += ("&" if "?" in _raw else "?") + "sslmode=require"
    SQLALCHEMY_DATABASE_URI = _raw

ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 300}

def create_app(test_config=None):
    app = Flask(__name__, static_folder="public", static_url_path="/")
    app.config.update(
        SQLALCHEMY_DATABASE_URI=SQLALCHEMY_DATABASE_URI,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS=ENGINE_OPTIONS,
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
    )
    if test_config: app.config.update(test_config)

    db.init_app(app)
    CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
    _init_logging(app)

    @app.get("/health")
    def health(): return jsonify(ok=True, service="spainroom-backend")

    # OWNER básicos
    @app.route("/api/owner/check", methods=["POST", "OPTIONS"])
    def owner_check():
        if request.method == "OPTIONS": return ("", 204)
        import uuid
        return jsonify(ok=True, id="SRV-TEST-" + uuid.uuid4().hex[:8])

    @app.route("/api/owner/cedula/upload", methods=["POST", "OPTIONS"])
    def cedula_upload():
        if request.method == "OPTIONS": return ("", 204)
        f = request.files.get("file")
        if not f: return jsonify(ok=False, error="no_file"), 400
        filename = secure_filename(f.filename or "file")
        up_dir = Path(current_app.instance_path) / "uploads"; up_dir.mkdir(parents=True, exist_ok=True)
        target = up_dir / filename; f.save(target)
        return jsonify(ok=True, filename=filename, size=target.stat().st_size)

    # CATASTRO estricto (sin mock)
    @app.post("/api/catastro/resolve_direccion")
    def resolve_dir():
        body = request.get_json(silent=True) or {}
        if not all((body.get("direccion"), body.get("municipio"), body.get("provincia"))):
            return jsonify(ok=False, error="bad_request", message="Faltan direccion/municipio/provincia"), 400
        return jsonify(ok=False, error="catastro_unavailable", message="Servicio Catastro no disponible"), 503

    @app.post("/api/catastro/consulta_refcat")
    def consulta_refcat():
        body = request.get_json(silent=True) or {}
        refcat = (body.get("refcat") or "").strip()
        if len(refcat) != 20 or not refcat.isalnum():
            return jsonify(ok=False, error="bad_refcat", message="La referencia catastral debe tener 20 caracteres alfanuméricos."), 400
        return jsonify(ok=False, error="catastro_unavailable", message="Servicio Catastro no disponible"), 503

    # CÉDULA: DB real (municipio -> provincia -> default), SIEMPRE 200 con resultado
    @app.post("/api/legal/requirement")
    def legal_requirement():
        body = request.get_json(silent=True) or {}
        municipio = (body.get("municipio") or "").strip()
        provincia = (body.get("provincia") or "").strip()
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
            with db.engine.connect() as conn:
                r = conn.execute(sql, {"mun": municipio, "prov": provincia}).mappings().first()
                if r: row = dict(r)
        except Exception as e:
            current_app.logger.warning("legal_requirement DB error: %s", e)

        if not row:
            row = {"cat":"no","doc":"—","org":"—","vig":"—","notas":"Sin datos para provincia/municipio.","link":None}

        return jsonify(ok=True, requirement={**row, "has_doc": False})

    @app.get("/")
    def index(): return jsonify(ok=True, msg="SpainRoom API (cédula)")

    @app.errorhandler(404)
    def nf(e): return jsonify(ok=False, error="not_found", message="No encontrado"), 404

    @app.errorhandler(500)
    def se(e):
        app.logger.exception("500"); return jsonify(ok=False, error="server_error"), 500

    return app

def _init_logging(app):
    app.logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    sh = logging.StreamHandler(); sh.setFormatter(fmt); sh.setLevel(logging.INFO)
    app.logger.addHandler(sh)
    logs_dir = BASE_DIR / "logs"; logs_dir.mkdir(exist_ok=True)
    fh = RotatingFileHandler(logs_dir / "backend.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt); fh.setLevel(logging.INFO); app.logger.addHandler(fh)
    app.logger.info("Logging listo")
