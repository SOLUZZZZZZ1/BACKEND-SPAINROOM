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
    if p.startswith("34"): return "+"+p
    if re.fullmatch(r"\d{9,15}", p): return "+34"+p
    return p

# rate limit simple (memoria)
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

# -------- endpoints --------
@bp_contact.post("/api/contacto/oportunidades")
def contacto_oportunidades():
    ip = request.headers.get("x-forwarded-for") or request.remote_addr or
