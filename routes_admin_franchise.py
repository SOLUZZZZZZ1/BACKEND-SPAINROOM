# routes_admin_franchise.py — Admin: ingest padrón, listar plazas, ocupar/liberar, export Excel (robusto + autocreate)
import io, math, os
import pandas as pd
from flask import Blueprint, request, jsonify, send_file
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from extensions import db
from models_franchise_slots import FranchiseSlot

bp_admin_franq = Blueprint("admin_franq", __name__)
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "ramon")

# -------------------- helpers --------------------

def _auth():
    return (request.headers.get("X-Admin-Key") or "") == ADMIN_API_KEY

def _ensure_table():
    """Crea la tabla si no existe (no hace nada si ya está)."""
    try:
        FranchiseSlot.__table__.create(bind=db.engine, checkfirst=True)
    except SQLAlchemyError:
        db.session.rollback()

def _slots_rule(municipio: str, provincia: str, poblacion: int) -> int:
    if not poblacion or poblacion <= 0:
        return 0
    m = (municipio or "").strip().lower()
    p = (provincia or "").strip().lower()
    if (m == "madrid" and p == "madrid") or (m == "barcelona" and p == "barcelona"):
        return max(1, math.ceil(poblacion / 20000))
    return max(1, math.ceil(poblacion / 10000))

def _read_dataframe(fs) -> pd.DataFrame:
    """Lee FileStorage como CSV/Excel con heurística (UTF-8 con/sin BOM; ; o ,)."""
    name = (getattr(fs, "filename", "") or "").lower()
    mimetype = (getattr(fs, "mimetype", "") or "").lower()
    raw = fs.read()
    if not raw:
        raise ValueError("empty_file")

    # Excel por extensión o mimetype
    if name.endswith((".xlsx",".xls",".xlsm",".xlsb")) or "excel" in mimetype:
        bio = io.BytesIO(raw)
        engine = "openpyxl" if name.endswith(".xlsx") else None
        try:
            return pd.read_excel(bio, sheet_name=0, engine=engine)
        except Exception:
            bio.seek(0)
            return pd.read_excel(bio, sheet_name=0)

    # CSV
    text = raw.decode("utf-8-sig", errors="ignore")
    try:
        return pd.read_csv(io.StringIO(text), sep=None, engine="python")
    except Exception:
        return pd.read_csv(io.StringIO(text), sep=",", engine="python")

def _find_col(df: pd.DataFrame, *keys):
    cols = list(df.columns)
    low = [str(c).strip().lower() for c in cols]
    # exactos
    for k in keys:
        if k in low:
            return cols[low.index(k)]
    # por inclusión
    for k in keys:
        for i, n in enumerate(low):
            if k in n:
                return cols[i]
    return None

# -------------------- endpoints --------------------

@bp_admin_franq.post("/api/admin/franquicia/ingest")
def ingest_csv():
    if not _auth():
        return jsonify(ok=False, error="forbidden"), 403
    _ensure_table()

    fs = request.files.get("file")
    if not fs:
        return jsonify(ok=False, error="missing_file"), 400

    try:
        fs.stream.seek(0)
        df = _read_dataframe(fs)
    except Exception as e:
        return jsonify(ok=False, error="read_failed", error_detail=str(e)), 400
    if df is None or df.empty:
        return jsonify(ok=False, error="empty_file"), 400

    col_p = _find_col(df, "provincia", "prov.")
    col_m = _find_col(df, "municipio", "muni")
    col_h = _find_col(df, "poblacion", "población", "habit", "pob.", "total")
    if not (col_p and col_m and col_h):
        return jsonify(ok=False, error="missing_columns",
                       got=[str(c) for c in df.columns],
                       need=["provincia","municipio","poblacion"]), 400

    df = df[[col_p, col_m, col_h]].copy()
    df.columns = ["provincia","municipio","poblacion"]
    df["provincia"] = df["provincia"].astype(str).str.strip()
    df["municipio"] = df["municipio"].astype(str).str.strip()
    df["poblacion"] = pd.to_numeric(df["poblacion"], errors="coerce").fillna(0).astype(int)
    df = df[(df["provincia"]!="") & (df["municipio"]!="") & (df["poblacion"]>0)]
    if df.empty:
        return jsonify(ok=False, error="no_valid_rows"), 400

    inserted = updated = skipped = errors = 0

    for _, r in df.iterrows():
        try:
            prov = str(r["provincia"]).strip()
            mun  = str(r["municipio"]).strip()
            pop  = int(r["poblacion"] or 0)
            plazas = _slots_rule(mun, prov, pop)

            row = (db.session.query(FranchiseSlot)
                   .filter(func.lower(FranchiseSlot.provincia)==prov.lower(),
                           func.lower(FranchiseSlot.municipio)==mun.lower())
                   .first())
            if not row:
                row = FranchiseSlot(
                    provincia=prov, municipio=mun, poblacion=pop,
                    plazas=plazas, ocupadas=0, libres=plazas,
                    assigned_to=None, status="free" if plazas>0 else "full"
                )
                db.session.add(row)
                try:
                    db.session.flush()
                    inserted += 1
                except IntegrityError:
                    db.session.rollback()
                    row = (db.session.query(FranchiseSlot)
                           .filter(func.lower(FranchiseSlot.provincia)==prov.lower(),
                                   func.lower(FranchiseSlot.municipio)==mun.lower())
                           .first())
                    if not row:
                        skipped += 1
                        continue
                    row.poblacion = pop
                    row.plazas = plazas
                    row.libres = max(0, int(row.plazas or 0) - int(row.ocupadas or 0))
                    row.status = "full" if row.libres==0 else ("free" if (row.ocupadas or 0)==0 else "partial")
                    updated += 1
            else:
                row.poblacion = pop
                row.plazas = plazas
                row.libres = max(0, int(row.plazas or 0) - int(row.ocupadas or 0))
                row.status = "full" if row.libres==0 else ("free" if (row.ocupadas or 0)==0 else "partial")
                updated += 1

        except Exception:
            db.session.rollback()
            errors += 1
            continue

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        errors += 1

    total = int(db.session.query(FranchiseSlot).count())
    return jsonify(ok=True, inserted=inserted, updated=updated, skipped=skipped, errors=errors, total=total)

@bp_admin_franq.get("/api/admin/franquicia/summary")
def summary():
    if not _auth():
        return jsonify(ok=False, error="forbidden"), 403
    _ensure_table()
    q = db.session.query(
        func.count(FranchiseSlot.id),
        func.sum(FranchiseSlot.poblacion),
        func.sum(FranchiseSlot.plazas),
        func.sum(FranchiseSlot.ocupadas),
        func.sum(FranchiseSlot.libres),
    ).one()
    return jsonify(
        ok=True,
        total_municipios=int(q[0] or 0),
        habitantes=int(q[1] or 0),
        plazas=int(q[2] or 0),
        ocupadas=int(q[3] or 0),
        libres=int(q[4] or 0),
    )

@bp_admin_franq.get("/api/admin/franquicia/slots")
def list_slots():
    if not _auth():
        return jsonify(ok=False, error="forbidden"), 403
    _ensure_table()
    prov = request.args.get("provincia")
    muni = request.args.get("municipio")
    status = request.args.get("status")
    assigned = request.args.get("assigned_to")

    q = db.session.query(FranchiseSlot)
    if prov: q = q.filter(FranchiseSlot.provincia.ilike(f"%{prov}%"))
    if muni: q = q.filter(FranchiseSlot.municipio.ilike(f"%{muni}%"))
    if status in ("free","partial","full"): q = q.filter(FranchiseSlot.status==status)
    if assigned: q = q.filter(FranchiseSlot.assigned_to==assigned)
    rows = [r.to_dict() for r in q.order_by(FranchiseSlot.provincia, FranchiseSlot.municipio).limit(5000).all()]
    return jsonify(ok=True, count=len(rows), results=rows)

@bp_admin_franq.post("/api/admin/franquicia/slots/ocupar")
def ocupar():
    if not _auth():
        return jsonify(ok=False, error="forbidden"), 403
    _ensure_table()
    data = request.get_json(force=True) or {}
    slot_id = data.get("id")
    assigned_to = (data.get("assigned_to") or "").strip() or None
    inc = int(data.get("inc", 1))
    row = db.session.get(FranchiseSlot, slot_id)
    if not row: return jsonify(ok=False, error="not_found"), 404
    row.ocupadas = min(int(row.plazas or 0), int(row.ocupadas or 0) + max(1, inc))
    row.libres = max(0, int(row.plazas or 0) - int(row.ocupadas or 0))
    if assigned_to: row.assigned_to = assigned_to
    row.status = "full" if row.libres==0 else ("free" if (row.ocupadas or 0)==0 else "partial")
    db.session.commit()
    return jsonify(ok=True, slot=row.to_dict())

@bp_admin_franq.post("/api/admin/franquicia/slots/liberar")
def liberar():
    if not _auth():
        return jsonify(ok=False, error="forbidden"), 403
    _ensure_table()
    data = request.get_json(force=True) or {}
    slot_id = data.get("id")
    dec = int(data.get("dec", 1))
    row = db.session.get(FranchiseSlot, slot_id)
    if not row: return jsonify(ok=False, error="not_found"), 404
    row.ocupadas = max(0, int(row.ocupadas or 0) - max(1, dec))
    row.libres = max(0, int(row.plazas or 0) - int(row.ocupadas or 0))
    row.status = "full" if row.libres==0 else ("free" if (row.ocupadas or 0)==0 else "partial")
    db.session.commit()
    return jsonify(ok=True, slot=row.to_dict())

@bp_admin_franq.get("/api/admin/franquicia/export.xlsx")
def export_xlsx():
    if not _auth():
        return jsonify(ok=False, error="forbidden"), 403
    _ensure_table()
    rows = db.session.query(FranchiseSlot).order_by(FranchiseSlot.provincia, FranchiseSlot.municipio).all()
    if not rows: return jsonify(ok=False, error="no_data"), 400

    df = pd.DataFrame([r.to_dict() for r in rows])
    g = df.groupby("provincia", as_index=False).agg({
        "poblacion":"sum", "plazas":"sum", "ocupadas":"sum", "libres":"sum"
    }).sort_values("provincia")
    tot = {
        "habitantes": int(df["poblacion"].sum()),
        "plazas": int(df["plazas"].sum()),
        "ocupadas": int(df["ocupadas"].sum()),
        "libres": int(df["libres"].sum()),
        "municipios": int(len(df))
    }

    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name="Municipios")
        g.to_excel(xw, index=False, sheet_name="Provincias")
        pd.DataFrame([tot]).to_excel(xw, index=False, sheet_name="Totales")
    bio.seek(0)
    return send_file(bio, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name="plazas_franquicia.xlsx")
