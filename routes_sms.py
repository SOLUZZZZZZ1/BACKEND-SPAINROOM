# routes_sms.py — Webhook de SMS entrantes (Twilio)
from flask import Blueprint, request, jsonify, current_app

bp_sms = Blueprint("sms", __name__)

@bp_sms.post("/inbound")
def sms_inbound():
    """
    Twilio envía application/x-www-form-urlencoded:
      From=+346..., To=+1225..., Body=...
      MessageSid=SM..., NumMedia=0/1...
    """
    frm = request.form.get("From", "")
    to  = request.form.get("To", "")
    body= request.form.get("Body", "")
    sid = request.form.get("MessageSid", "")
    num = int(request.form.get("NumMedia", "0") or 0)

    current_app.logger.info("[SMS IN] from=%s to=%s sid=%s body=%s media=%s",
                            frm, to, sid, body[:160], num)

    # TODO: aquí puedes crear un lead automático si quieres:
    # from extensions import db
    # from models_leads import Lead
    # db.session.add(Lead(kind="tenant/owner/franchise", source="sms",
    #     provincia=None, municipio=None, nombre=None, telefono=frm, notes=body))
    # db.session.commit()

    return jsonify(ok=True)
