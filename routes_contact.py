# routes_contact.py
import os, re, smtplib, time
from email.mime.text import MIMEText
from flask import Blueprint, request, jsonify
from extensions import db
from models_contact import ContactMessage

bp_contact = Blueprint("contact", __name__)

# -------- helpers --------
def norm_phone(p: str) -> str:
    p = re.sub(r"[^\d+]", "", p or "")
    if p.startswith("+"): return p
    if p.startswith("34"): return "+" + p
    if re.fullmatch(r"\d{9,15}", p): return "+34" + p
    return p

# rate limit simple en memoria (por IP+endpoint)
_rl = {}
def rate_limit(key: str, per_sec: float) -> bool:
    now = time.time()
    last = _rl.get(key, 0)
    if now - last < per_sec:
        return False
    _rl[key] = now
    return True

def send_email(to_email: str, subject: str, body: str) -> bool:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    pwd  = os.getenv("SMTP_PASS")
    from_email = os.getenv("MAIL_FROM", user or "no-reply@spainroom.es")
    if not (host and user and pwd and to_email):
        print("[CONTACT] SMTP no configurado — no se envía email")
        return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"]    = from_email
        msg["To"]      = to_email
        with smtplib.SMTP(host, port, timeout=10) as s:
            s.starttls()
            s.login(user, pwd)
            s.sendmail(from_email, [to_email], msg.as_string())
        return True
    except Exception as e:
        print("[CONTACT] Error SMTP:", e)
        return False

# -------- ENDPOINTS --------

@bp_contact.post("/api/contacto/oportunidades")
def contacto_oportunidades():
    ip = request.headers.get("x-forwarded-for") or request.remote_addr or "0.0.0.0"
    if not rate_limit(f"contact:opp:{ip}", per_sec=2.0):
        return jsonify(ok=False, error="rate_limited"), 429

    data = request.get_json(silent=True) or {}
    tipo      = (data.get("tipo") or "oportunidades").strip()     # inversion | publicidad | colaboracion | ...
    nombre    = (data.get("nombre") or "").strip()
    email     = (data.get("email")  or "").strip()
    telefono  = (data.get("telefono") or "").strip()
    zona      = (data.get("zona") or data.get("sector") or "").strip()
    mensaje   = (data.get("mensaje") or "").strip()
    via       = (data.get("via") or "web_oportunidades").strip()
    meta      = data.get("meta_json") if isinstance(data.get("meta_json"), dict) else None

    # validaciones básicas
    if len(nombre.split()) < 2:
        return jsonify(ok=False, error="bad_nombre"), 400
    if email and ("@" not in email or "." not in email.split("@")[-1]):
        return jsonify(ok=False, error="bad_email"), 400
    teln = norm_phone(telefono) if telefono else None
    if telefono and not re.fullmatch(r"\+\d{9,15}", teln):
        return jsonify(ok=False, error="bad_telefono"), 400
    if not mensaje:
        return jsonify(ok=False, error="bad_mensaje"), 400

    # guardar
    row = ContactMessage(
        tipo=tipo, nombre=nombre, email=email or "",
        telefono=teln, zona=zona, mensaje=mensaje, via=via, meta_json=meta
    )
    db.session.add(row); db.session.commit()

    # notificación (opcional)
    mail_admin = os.getenv("MAIL_TO_ADMIN")
    if mail_admin:
        subj = f"[CONTACT/{row.tipo.upper()}] {row.nombre} <{row.email or '-'}>"
        body = (
            f"Nombre: {row.nombre}\n"
            f"Email: {row.email or '-'}\n"
            f"Teléfono: {row.telefono or '-'}\n"
            f"Zona/Sector: {row.zona or '-'}\n"
            f"Mensaje:\n{row.mensaje}\n\n"
            f"Vía: {row.via}\n"
            f"ID: {row.id}\n"
        )
        send_email(mail_admin, subj, body)

    return jsonify(ok=True, id=row.id)

@bp_contact.post("/api/contacto/tenants")
def contacto_tenants():
    ip = request.headers.get("x-forwarded-for") or request.remote_addr or "0.0.0.0"
    if not rate_limit(f"contact:ten:{ip}", per_sec=2.0):
        return jsonify(ok=False, error="rate_limited"), 429

    data = request.get_json(silent=True) or {}
    nombre   = (data.get("nombre") or "").strip()
    email    = (data.get("email")  or "").strip()
    mensaje  = (data.get("mensaje") or "").strip()
    fecha    = (data.get("fecha")   or "").strip()   # opcional
    via      = (data.get("via") or "web_tenant_prerequest").strip()

    if len(nombre.split()) < 2:
        return jsonify(ok=False, error="bad_nombre"), 400
    if email and ("@" not in email or "." not in email.split("@")[-1]):
        return jsonify(ok=False, error="bad_email"), 400
    if not mensaje:
        return jsonify(ok=False, error="bad_mensaje"), 400

    row = ContactMessage(
        tipo="tenants", nombre=nombre, email=email or "", telefono=None,
        zona=None, mensaje=mensaje, via=via, meta_json={"fecha": fecha} if fecha else None
    )
    db.session.add(row); db.session.commit()

    return jsonify(ok=True, id=row.id)
