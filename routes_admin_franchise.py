# routes_admin_franchise.py — Admin: ingest padrón, listar plazas, ocupar/liberar, export Excel (parche robusto)
import io
import math
import os

import pandas as pd
from flask import Blueprint, jsonify, request, send_file
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from extensions import db
from models_franchise_slots import FranchiseSlot

bp_admin_franq = Blueprint("admin_franq", __name__)

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "ramon")


def _auth():
    return (request.headers.get("X-Admin-Key") or "") == ADMIN_API_KEY


def _slots_rule(municipio: str, provincia: str, poblacion: int) -> int:
    """Cálculo de plazas:
    - Madrid/Barcelona (municipio y provincia coinciden): 1 por cada 20.000 (mín. 1)
    - Resto: 1 por cada 10.000 (mín. 1)
    """
    if poblacion is None or poblacion <= 0:
        return 0
    m = (municipio or "").strip().lower()
    p = (provincia or "").strip().lower()
    if (m == "madrid" and p == "madrid") or (m == "barcelona" and p == "barcelona"):
        return max(1, math.ceil(poblacion / 20000))
    return max(1, math.ceil(poblacion / 10000))


@bp_admin_franq.post("/api/admin/franquicia/ingest")
def ingest_csv():
    """Sube CSV/Excel con columnas: provincia, municipio, poblacion.
    Parche robusto:
      - upsert case-insensitive por (provincia, municipio)
      - captura de duplicados con flush() y fallback a update
      - no tumba en filas conflictivas (cuenta errors/skip)
    """
    if not _auth():
        return jsonify(ok=False, error="forbidden"), 403

    fs = request.files.get("file")
    if not fs:
        return jsonify(ok=False, error="missing_file"), 400

    # ---- Cargar dataframe (CSV primero; si falla, Excel) --------------------
    try:
        df = pd.read_csv(fs)
    except Exception:
        fs.seek(0)
        try:
            df = pd.read_excel(fs)
        except Exception:
            return jsonify(ok=False, error="bad_file"), 400

    if df is None or df.empty:
        return jsonify(ok=False, error="empty_file"), 400

    # ---- Normalizar cabeceras y detectar columnas ---------------------------
    cols_lower = {str(c).strip().lower(): c for c in df.columns}
    # búsqueda flexible
    def _find_col(*keys):
        for k in keys:
            if k in cols_lower:
                return cols_lower[k]
        # heurística por inclusión
        lc = [str(c).strip().lower() for c in df.columns]
        for key in keys:
            for i, name in enumerate(lc):
                if key in name:
                    return df.columns[i]
        return None

    col_p = _find_col("provincia", "prov.")
    col_m = _find_col("municipio", "muni")
    col_h = _find_col("poblacion", "población", "habit", "pob.", "total")

    if not (col_p and col_m and col_h):
        return jsonify(ok=False, error="missing_columns",
                       got=[str(c) for c in df.columns],
                       need=["provincia", "municipio", "poblacion"]), 400

    # ---- Quedarnos con lo necesario y limpiar tipos -------------------------
    df = df[[col_p, col_m, col_h]].copy()
    df.columns = ["provincia", "municipio", "poblacion"]

    df["provincia"] = df["provincia"].astype(str).str.strip()
    df["municipio"] = df["municipio"].astype(str).str.strip()
    df["poblacion"] = pd.to_numeric(df["poblacion"], errors="coerce").fillna(0).astype(int)

    # Filtramos filas vacías o población <= 0
    df = df[(df["provincia"] != "") & (df["municipio"] != "") & (df["poblacion"] > 0)]
    if df.empty:
        return jsonify(ok=False, error="no_valid_rows"), 400

    # ---- Ingest robusto (por fila) -----------------------------------------
    inserted = 0
    updated = 0
    skipped = 0
    errors = 0

    for _, r in df.iterrows():
        try:
            prov = str(r["provincia"]).strip()
            mun = str(r["municipio"]).strip()
            pop = int(r["poblacion"] or 0)
            plazas = _slots_rule(mun, prov, pop)

            # Case-insensitive lookup
            row = (db.session.query(FranchiseSlot)
                   .filter(func.lower(FranchiseSlot.provincia) == prov.lower(),
                           func.lower(FranchiseSlot.municipio) == mun.lower())
                   .first())

            if not row:
                # INSERT
                row = FranchiseSlot(
                    provincia=prov,
                    municipio=mun,
                    poblacion=pop,
                    plazas=plazas,
                    ocupadas=0,
                    libres=plazas,
                    assigned_to=None,
                    status="free" if plazas > 0 else "full",
                )
                db.session.add(row)
                try:
                    # flush aquí detecta un posible duplicado por la UniqueConstraint
                    db.session.flush()
                    inserted += 1
                except IntegrityError:
                    # Fallback a UPDATE si otro hilo/registro similar ya existe
                    db.session.rollback()
                    row = (db.session.query(FranchiseSlot)
                           .filter(func.lower(FranchiseSlot.provincia) == prov.lower(),
                                   func.lower(FranchiseSlot.municipio) == mun.lower())
                           .first())
                    if not row:
                        skipped += 1
                        continue
                    row.poblacion = pop
                    row.plazas = plazas
                    row.libres = max(0, int(row.plazas or 0) - int(row.ocupadas or 0))
                    row.status = "full" if row.libres == 0 else ("free" if (row.ocupadas or 0) == 0 else "partial")
                    updated += 1
            else:
                # UPDATE
                row.poblacion = pop
                row.plazas = plazas
                row.libres = max(0, int(row.plazas or 0) - int(row.ocupadas or 0))
                row.status = "full" if row.libres == 0 else ("free" if (row.ocupadas or 0) == 0 else "partial")
                updated += 1

        except Exception:
            # no paramos toda la carga por una fila mala
            db.session.rollback()
            errors += 1
            continue

    # Commit final (si hay algo que guardar)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        # si algo residual falló, no tumbar
        errors += 1

    total = int(db.session.query(FranchiseSlot).count())
    return jsonify(ok=True, inserted=inserted, updated=updated, skipped=skipped, errors=errors, total=total)


@bp_admin_franq.get("/api/admin/franquicia/summary")
def summary():
    if not _auth():
        return jsonify(ok=False, error="forbidden"), 403
    q = db.session.query(
        func.count(FranchiseSlot.id),
        func.sum(FranchiseSlot.poblacion),
        func.sum(FranchiseSlot.plazas),
        func.sum(FranchiseSlot.ocupadas),
        func.sum(FranchiseSlot.libres),
    ).one()
    total_munis = int(q[0] or 0)
    return jsonify(
        ok=True,
        total_municipios=total_munis,
        habitantes=int(q[1] or 0),
        plazas=int(q[2] or 0),
        ocupadas=int(q[3] or 0),
        libres=int(q[4] or 0),
    )


@bp_admin_franq.get("/api/admin/franquicia/slots")
def list_slots():
    if not _auth():
        return jsonify(ok=False, error="forbidden"), 403
    prov = request.args.get("provincia")
    muni = request.args.get("municipio")
    status = request.args.get("status")  # free|partial|full
    assigned = request.args.get("assigned_to")

    q = db.session.query(FranchiseSlot)
    if prov:
        q = q.filter(FranchiseSlot.provincia.ilike(f"%{prov}%"))
    if muni:
        q = q.filter(FranchiseSlot.municipio.ilike(f"%{muni}%"))
    if status in ("free", "partial", "full"):
        q = q.filter(FranchiseSlot.status == status)
    if assigned:
        q = q.filter(FranchiseSlot.assigned_to == assigned)

    rows = [
        r.to_dict()
        for r in q.order_by(FranchiseSlot.provincia, FranchiseSlot.municipio).limit(5000).all()
    ]
    return jsonify(ok=True, count=len(rows), results=rows)


@bp_admin_franq.post("/api/admin/franquicia/slots/ocupar")
def ocupar():
    if not _auth():
        return jsonify(ok=False, error="forbidden"), 403
    data = request.get_json(force=True) or {}
    slot_id = data.get("id")
    assigned_to = (data.get("assigned_to") or "").strip() or None
    inc = int(data.get("inc", 1))
    row = db.session.get(FranchiseSlot, slot_id)
    if not row:
        return jsonify(ok=False, error="not_found"), 404
    row.ocupadas = min(int(row.plazas or 0), int(row.ocupadas or 0) + max(1, inc))
    row.libres = max(0, int(row.plazas or 0) - int(row.ocupadas or 0))
    if assigned_to:
        row.assigned_to = assigned_to
    row.status = "full" if row.libres == 0 else ("free" if (row.ocupadas or 0) == 0 else "partial")
    db.session.commit()
    return jsonify(ok=True, slot=row.to_dict())


@bp_admin_franq.post("/api/admin/franquicia/slots/liberar")
def liberar():
    if not _auth():
        return jsonify(ok=False, error="forbidden"), 403
    data = request.get_json(force=True) or {}
    slot_id = data.get("id")
    dec = int(data.get("dec", 1))
    row = db.session.get(FranchiseSlot, slot_id)
    if not row:
        return jsonify(ok=False, error="not_found"), 404
    row.ocupadas = max(0, int(row.ocupadas or 0) - max(1, dec))
    row.libres = max(0, int(row.plazas or 0) - int(row.ocupadas or 0))
    row.status = "full" if row.libres == 0 else ("free" if (row.ocupadas or 0) == 0 else "partial")
    db.session.commit()
    return jsonify(ok=True, slot=row.to_dict())


@bp_admin_franq.get("/api/admin/franquicia/export.xlsx")
def export_xlsx():
    if not _auth():
        return jsonify(ok=False, error="forbidden"), 403

    rows = db.session.query(FranchiseSlot).order_by(
        FranchiseSlot.provincia, FranchiseSlot.municipio
    ).all()
    if not rows:
        return jsonify(ok=False, error="no_data"), 400

    df = pd.DataFrame([r.to_dict() for r in rows])

    # Provincias agregadas
    g = (
        df.groupby("provincia", as_index=False)
        .agg({"poblacion": "sum", "plazas": "sum", "ocupadas": "sum", "libres": "sum"})
        .sort_values("provincia")
    )

    # Totales nacionales
    tot = {
        "habitantes": int(df["poblacion"].sum()),
        "plazas": int(df["plazas"].sum()),
        "ocupadas": int(df["ocupadas"].sum()),
        "libres": int(df["libres"].sum()),
        "municipios": int(len(df)),
    }

    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="xlsxwriter") as xw:
        df.to_excel(xw, index=False, sheet_name="Municipios")
        g.to_excel(xw, index=False, sheet_name="Provincias")
        pd.DataFrame([tot]).to_excel(xw, index=False, sheet_name="Totales")
    bio.seek(0)
    return send_file(
        bio,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="plazas_franquicia.xlsx",
    )
