# routes_leads.py
import os, re
from flask import Blueprint, request, jsonify
from extensions import db
from models_leads import Lead

try:
    from services_owner import route_franchisee as guess_franquiciado
except Exception:
    def guess_franquiciado(provincia, municipio):
        return None

bp_leads = Blueprint("leads", __name__)
PHONE_RE = re.compile(r"^\+?\d{9,15}$")

def _norm_phone(s: str) -> str:
    s = re.sub(r"[^\d+]", "", s or "")
    if s.startswith("+"): return s
    if s.startswith("34"): return "+"+s
    if re.fullmatch(r"\d{9,15}", s): return "+34"+s
    return s

def _notify_admin(lead: Lead):
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USER")
    pwd  = os.getenv("SMTP_PASS")
    to   = os.getenv("MAIL_TO_ADMIN")
    if not (host and user and pwd and to): 
        return False
    try:
        import smtplib
        from email.mime.text import MIMEText
        body = (f"[LEAD {lead.kind.upper()}]\n"
                f"Nombre: {lead.nombre}\n"
                f"Teléfono: {lead.telefono}\n"
                f"Zona: {lead.municipio or '-'}, {lead.provincia or '-'}\n"
                f"Asignado a: {lead.assigned_to or '(pendiente)'}\n"
                f"Estado: {lead.status}\n"
                f"ID: {lead.id}\n")
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = f"[LEAD/{lead.kind}] {lead.nombre} ({lead.municipio or '-'} - {lead.provincia or '-'})"
        msg["From"] = os.getenv("MAIL_FROM", user or "no-reply@spainroom.es")
        msg["To"] = to
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT","587")), timeout=10) as s:
            s.starttls(); s.login(user, pwd); s.sendmail(msg["From"], [to], msg.as_string())
        return True
    except Exception:
        return False

@bp_leads.post("/api/leads")
def create_lead():
    data = request.get_json(force=True) or {}
    kind = (data.get("kind") or "").lower()
    if kind not in {"owner","tenant","franchise"}:
        return jsonify(ok=False, error="bad_kind"), 400

    nombre = (data.get("nombre") or "").strip()
    telefono = _norm_phone(data.get("telefono") or "")
    if len(nombre.split()) < 2:
        return jsonify(ok=False, error="bad_nombre"), 400
    if not PHONE_RE.fullmatch(telefono):
        return jsonify(ok=False, error="bad_telefono"), 400

    provincia = (data.get("provincia") or "").strip()
    municipio = (data.get("municipio") or "").strip()
    assigned_to = guess_franquiciado(provincia, municipio) if kind != "franchise" else None
    status = "assigned" if assigned_to else "new"

    lead = Lead(
        kind=kind, source="voice",
        provincia=provincia or None, municipio=municipio or None,
        nombre=nombre, telefono=telefono, email=(data.get("email") or None),
        assigned_to=assigned_to, status=status,
        notes=(data.get("notes") or None), meta_json=(data.get("meta_json") if isinstance(data.get("meta_json"), dict) else None)
    )
    db.session.add(lead); db.session.commit()
    _notify_admin(lead)
    return jsonify(ok=True, lead=lead.to_dict())

@bp_leads.get("/api/leads")
def list_leads():
    q = Lead.query
    st = (request.args.get("status") or "").lower()
    kd = (request.args.get("kind") or "").lower()
    prov = request.args.get("provincia") or None
    mun  = request.args.get("municipio") or None
    ass  = request.args.get("assigned_to") or None

    if st in {"new","assigned","done","invalid"}: q = q.filter(Lead.status==st)
    if kd in {"owner","tenant","franchise"}: q = q.filter(Lead.kind==kd)
    if prov: q = q.filter(Lead.provincia.ilike(f"%{prov}%"))
    if mun:  q = q.filter(Lead.municipio.ilike(f"%{mun}%"))

    h_franq = (request.headers.get("X-Franquiciado") or "").strip()
    if h_franq:
        q = q.filter((Lead.assigned_to==h_franq) | (Lead.assigned_to.is_(None)))

    if ass: q = q.filter(Lead.assigned_to==ass)
    rows = [l.to_dict() for l in q.order_by(Lead.id.desc()).limit(500).all()]
    return jsonify(ok=True, results=rows, count=len(rows))

@bp_leads.patch("/api/leads/<int:lead_id>")
def update_lead(lead_id:int):
    data = request.get_json(force=True) or {}
    l = db.session.get(Lead, lead_id)
    if not l: return jsonify(ok=False, error="not_found"), 404

    st = (data.get("status") or "").lower()
    if st in {"new","assigned","done","invalid"}: l.status = st
    if "assigned_to" in data:
        l.assigned_to = (data.get("assigned_to") or None)
        if l.assigned_to and l.status == "new":
            l.status = "assigned"
    if "notes" in data:
        l.notes = (data.get("notes") or None)
    db.session.commit()
    return jsonify(ok=True, lead=l.to_dict())
