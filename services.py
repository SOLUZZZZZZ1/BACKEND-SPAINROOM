
import os, math, csv
from pathlib import Path
from typing import Dict, Any, List, Optional

from .models import db, FranquiciaSlots, FranquiciaOcupacion

THRESH_1 = int(os.getenv("PLAZAS_THRESH_1", "10000"))
THRESH_2 = int(os.getenv("PLAZAS_THRESH_2", "20000"))
DISTRICT_RATIO = int(os.getenv("PLAZAS_MIN_DISTRICT_RATIO", "20000"))
DATA_DIR = Path(os.getenv("PLAZAS_DATA_DIR", "./data/oficial"))

def _rule_slots_municipio(pop: int) -> int:
    if pop < THRESH_1:
        return 1
    elif pop < THRESH_2:
        return 2
    else:
        return math.ceil(pop / DISTRICT_RATIO)

def _read_csv(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def rebuild_from_csv(preserve_occupations: bool = True) -> Dict[str, Any]:
    mun_csv = DATA_DIR / "municipios_es.csv"
    if not mun_csv.exists():
        raise FileNotFoundError(f"No existe {mun_csv.as_posix()}")

    municipios = _read_csv(mun_csv)

    dist_csv = DATA_DIR / "distritos_es.csv"
    distritos = _read_csv(dist_csv) if dist_csv.exists() else []

    idx_d = {}
    for d in distritos:
        key = (d.get("provincia",""), d.get("ciudad",""))
        idx_d.setdefault(key, []).append(d)

    if not preserve_occupations:
        FranquiciaOcupacion.query.delete()
        FranquiciaSlots.query.delete()
        db.session.commit()

    total_groups = 0
    created_slots = 0

    for m in municipios:
        provincia = (m.get("provincia") or "").strip()
        municipio = (m.get("municipio") or "").strip()
        try:
            poblacion = int(float(m.get("poblacion", -1))) if m.get("poblacion") not in (None, "") else -1
        except Exception:
            poblacion = -1

        dlist = idx_d.get((provincia, municipio), [])

        if dlist:
            for d in dlist:
                distrito = (d.get("distrito") or "").strip()
                try:
                    pob_d = int(float(d.get("poblacion", "0") or 0))
                except Exception:
                    pob_d = 0
                slots = max(1, math.ceil(pob_d / DISTRICT_RATIO))
                group = FranquiciaSlots.query.filter_by(provincia=provincia, municipio=municipio, nivel="distrito", distrito=distrito).first()
                if not group:
                    group = FranquiciaSlots(provincia=provincia, municipio=municipio, nivel="distrito", distrito=distrito, poblacion=pob_d, slots=slots)
                    db.session.add(group)
                    total_groups += 1
                else:
                    group.poblacion = pob_d
                    group.slots = slots
                db.session.flush()

                for i in range(1, slots+1):
                    occ = FranquiciaOcupacion.query.filter_by(provincia=provincia, municipio=municipio, nivel="distrito", distrito=distrito, slot_index=i).first()
                    if not occ:
                        occ = FranquiciaOcupacion(provincia=provincia, municipio=municipio, nivel="distrito", distrito=distrito, slot_index=i, ocupado=0, ocupado_por=None)
                        db.session.add(occ)
                        created_slots += 1
        else:
            slots = _rule_slots_municipio(poblacion if poblacion >= 0 else 0)
            group = FranquiciaSlots.query.filter_by(provincia=provincia, municipio=municipio, nivel="municipio", distrito="").first()
            if not group:
                group = FranquiciaSlots(provincia=provincia, municipio=municipio, nivel="municipio", distrito="", poblacion=poblacion, slots=slots)
                db.session.add(group)
                total_groups += 1
            else:
                group.poblacion = poblacion
                group.slots = slots
            db.session.flush()

            for i in range(1, slots+1):
                occ = FranquiciaOcupacion.query.filter_by(provincia=provincia, municipio=municipio, nivel="municipio", distrito="", slot_index=i).first()
                if not occ:
                    occ = FranquiciaOcupacion(provincia=provincia, municipio=municipio, nivel="municipio", distrito="", slot_index=i, ocupado=0, ocupado_por=None)
                    db.session.add(occ)
                    created_slots += 1

    db.session.commit()
    return {"ok": True, "groups": total_groups, "created_slots": created_slots}

def summary_totals() -> Dict[str, int]:
    total_plazas = db.session.query(db.func.coalesce(db.func.sum(FranquiciaSlots.slots), 0)).scalar()
    total_ocupadas = db.session.query(db.func.coalesce(db.func.sum(FranquiciaOcupacion.ocupado), 0)).scalar()
    libres = int(total_plazas or 0) - int(total_ocupadas or 0)
    return {"total_plazas": int(total_plazas or 0), "ocupadas": int(total_ocupadas or 0), "libres": libres}

def query_slots(provincia: Optional[str]=None, estado: str="todas", q: Optional[str]=None):
    occ_counts = db.session.query(
        FranquiciaOcupacion.provincia.label("prov"),
        FranquiciaOcupacion.municipio.label("mun"),
        FranquiciaOcupacion.nivel.label("niv"),
        FranquiciaOcupacion.distrito.label("dis"),
        db.func.sum(FranquiciaOcupacion.ocupado).label("ocupadas")
    ).group_by("prov","mun","niv","dis").subquery()

    qry = db.session.query(
        FranquiciaSlots.id,
        FranquiciaSlots.provincia,
        FranquiciaSlots.municipio,
        FranquiciaSlots.nivel,
        FranquiciaSlots.distrito,
        FranquiciaSlots.poblacion,
        FranquiciaSlots.slots,
        db.func.coalesce(occ_counts.c.ocupadas, 0).label("ocupadas")
    ).outerjoin(
        occ_counts,
        db.and_(
            FranquiciaSlots.provincia == occ_counts.c.prov,
            FranquiciaSlots.municipio == occ_counts.c.mun,
            FranquiciaSlots.nivel == occ_counts.c.niv,
            FranquiciaSlots.distrito == occ_counts.c.dis,
        )
    )

    if provincia:
        qry = qry.filter(FranquiciaSlots.provincia.ilike(f"%{provincia}%"))
    if q:
        like = f"%{q}%"
        qry = qry.filter(db.or_(
            FranquiciaSlots.municipio.ilike(like),
            FranquiciaSlots.distrito.ilike(like)
        ))

    rows = []
    for r in qry.all():
        ocupadas = int(r.ocupadas or 0)
        libres = int(r.slots or 0) - ocupadas
        if estado == "ocupadas" and ocupadas <= 0:
            continue
        if estado == "libres" and libres <= 0:
            continue
        rows.append({
            "id": r.id,
            "provincia": r.provincia,
            "municipio": r.municipio,
            "nivel": r.nivel,
            "distrito": r.distrito,
            "poblacion": int(r.poblacion or 0),
            "slots": int(r.slots or 0),
            "ocupadas": ocupadas,
            "libres": libres,
        })
    return rows

def get_group_occupancy(slot_group_id: int):
    g = FranquiciaSlots.query.get(slot_group_id)
    if not g:
        return []
    occs = FranquiciaOcupacion.query.filter_by(
        provincia=g.provincia, municipio=g.municipio, nivel=g.nivel, distrito=g.distrito
    ).order_by(FranquiciaOcupacion.slot_index.asc()).all()
    return [
        {"slot_index": o.slot_index, "ocupado": int(o.ocupado or 0), "ocupado_por": o.ocupado_por}
        for o in occs
    ]

def ocupar_slot(provincia: str, municipio: str, nivel: str, distrito: str, slot_index: int, ocupado_por: str):
    o = FranquiciaOcupacion.query.filter_by(
        provincia=provincia, municipio=municipio, nivel=nivel, distrito=distrito, slot_index=slot_index
    ).first()
    if not o:
        return {"ok": False, "error": "slot_no_existe"}
    if int(o.ocupado or 0) == 1:
        return {"ok": False, "error": "ya_ocupado"}
    o.ocupado = 1
    o.ocupado_por = ocupado_por
    db.session.commit()
    return {"ok": True}

def liberar_slot(provincia: str, municipio: str, nivel: str, distrito: str, slot_index: int):
    o = FranquiciaOcupacion.query.filter_by(
        provincia=provincia, municipio=municipio, nivel=nivel, distrito=distrito, slot_index=slot_index
    ).first()
    if not o:
        return {"ok": False, "error": "slot_no_existe"}
    if int(o.ocupado or 0) == 0:
        return {"ok": False, "error": "ya_libre"}
    o.ocupado = 0
    o.ocupado_por = None
    db.session.commit()
    return {"ok": True}
