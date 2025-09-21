# services_notify.py
import os, smtplib
from email.mime.text import MIMEText
from app import db
from models_owner import OwnerNotifyLog

# Twilio (opcional)
try:
    from twilio.rest import Client as TwilioClient
except Exception:
    TwilioClient = None

def _log(check_id: int, channel: str, target: str, ok: bool, detail: str):
    try:
        log = OwnerNotifyLog(check_id=check_id, channel=channel, target=target, ok=ok, detail=detail[:2000])
        db.session.add(log); db.session.commit()
    except Exception as e:
        db.session.rollback()
        print("[NOTIFY] No se pudo guardar log:", e)

def send_email(to_email: str, subject: str, body: str, check_id: int, admin=False) -> bool:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    pwd  = os.getenv("SMTP_PASS")
    from_email = os.getenv("MAIL_FROM", user or "no-reply@spainroom.es")
    if not (host and user and pwd and to_email):
        _log(check_id, "admin_email" if admin else "email", to_email or "-", False, "SMTP no configurado")
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
        _log(check_id, "admin_email" if admin else "email", to_email, True, "OK")
        return True
    except Exception as e:
        _log(check_id, "admin_email" if admin else "email", to_email, False, f"ERROR: {e}")
        return False

def send_sms(to_phone: str, text: str, check_id: int, admin=False) -> bool:
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    from_phone = os.getenv("TWILIO_FROM")
    if not (TwilioClient and sid and token and from_phone and to_phone):
        _log(check_id, "admin_sms" if admin else "sms", to_phone or "-", False, "Twilio no configurado")
        return False

    try:
        client = TwilioClient(sid, token)
        m = client.messages.create(body=text, from_=from_phone, to=to_phone)
        _log(check_id, "admin_sms" if admin else "sms", to_phone, True, f"OK sid={m.sid}")
        return True
    except Exception as e:
        _log(check_id, "admin_sms" if admin else "sms", to_phone, False, f"ERROR:
