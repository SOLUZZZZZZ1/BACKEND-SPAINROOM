# routes_sms.py — Webhook de SMS entrantes (Twilio) + creación automática de Lead
import os
import re
from flask import Blueprint, request, jsonify, current_app

from extensions import db
from models_leads import Lead

# Si quieres validar la firma del webhook de Twilio, instala el SDK en el API:
# requirements.txt (API): twilio==9.0.1
VALIDATE_SIGNATURE = (os.getenv("VALIDATE_TWILIO_SIGNATURE", "off").lower() == "on")
if VALIDATE_SIGNATURE:
    try:
        from twilio.request_validator import RequestValidator  # type: ignore
    except Exception:
        VALIDATE_SIGNATURE = False

# (Opcional) si tienes un enrutador de franquicia por zona, descomenta e implementa
try:
    from services_owner import route_franchisee as guess_franquiciado  # type: ignore
except Exception:  # pragma: no cover
    def guess_franquiciado(provincia: str | None, municipio: str | None) -> str | None:
        return None

bp_sms = Blueprint("sms", __name__)

PHONE_RE = re.compile(r"^\+?\d{9,15}$")


# ----------------------- Helpers -----------------------
def _normalize_phone(v: str | None) -> str:
    s = re.sub(r"[^\d+]", "", v or "")
    if not s:
        return ""
    if s.startswith("+"):
        return s
    if s.startswith("34"):
        return "+" + s
    if re.fullmatch(r"\d{9,15}", s):
        return "+34" + s
    return s


def _infer_kind(text: str) -> str:
    """
    Clasificación muy simple por palabras clave.
    - 'franquicia', 'franquiciado' => franchise
    - 'propietario', 'dueño', 'propiedad' => owner
    - 'inquilino', 'alquiler', 'alquilar', 'habitación', 'habitacion' => tenant
    """
    t = (text or "").lower()
    if any(w in t for w in ("franquicia", "franquiciado", "franchise")):
        return "franchise"
    if any(w in t for w in ("propietario", "dueño", "dueno", "propiedad", "mi piso")):
        return "owner"
    if any(w in t for w in ("inquilino", "alquiler", "alquilar", "habitación", "habitacion", "room", "hab")):
        return "tenant"
    # por defecto, tratamos como tenant (consulta sobre habitaciones)
    return "tenant"


def _validate_twilio_signature() -> bool:
    """Valida X-Twilio-Signature si está activado VALIDATE_TWILIO_SIGNATURE=on."""
    if not VALIDATE_SIGNATURE:
        return True
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    if not token:
        current_app.logger.warning("[SMS IN] Falta TWILIO_AUTH_TOKEN para validar firma.")
        return False
    tw_sig = request.headers.get("X-Twilio-Signature", "")
    if not tw_sig:
        current_app.logger.warning("[SMS IN] Falta cabecera X-Twilio-Signature.")
        return False
    try:
        validator = RequestValidator(token)  # type: ignore
        # Render sirve URL pública HTTPS fiable
        url = request.url
        params = request.form.to_dict(flat=True)
        ok = validator.validate(url, params, tw_sig)
        if not ok:
            current_app.logger.warning("[SMS IN] Firma inválida: %s", tw_sig)
        return ok
    except Exception as e:  # pragma: no cover
        current_app.logger.warning("[SMS IN] Error validando firma: %s", e)
        return False


# ----------------------- Webhook -----------------------
@bp_sms.post("/inbound")
def sms_inbound():
    """
    Twilio envía application/x-www-form-urlencoded:
      From=+34..., To=+1225..., Body=...
      MessageSid=SM..., NumMedia=0/1...
    """
    if not _validate_twilio_signature():
        return jsonify(ok=False, error="invalid_signature"), 403

    frm = _normalize_phone(request.form.get("From", ""))
    to = _normalize_phone(request.form.get("To", ""))
    body = (request.form.get("Body", "") or "").strip()
    sid = request.form.get("MessageSid", "")
    num_media = int(request.form.get("NumMedia", "0") or 0)

    current_app.logger.info(
        "[SMS IN] from=%s to=%s sid=%s media=%s body=%s",
        frm, to, sid, num_media, body[:200]
    )

    # Validaciones mínimas
    if not frm or not PHONE_RE.fullmatch(frm):
        return jsonify(ok=False, error="bad_from"), 400

    # Inferir tipo por texto; zona n/d en SMS (se podría parsear provincia/municipio si lo incluyen)
    kind = _infer_kind(body)
    provincia = None
    municipio = None

    # Asignación a franquiciado (si hay lógica de enrutado por municipio)
    assigned_to = guess_franquiciado(provincia, municipio)

    # Crear lead
    lead = Lead(
        kind=kind,
        source="sms",
        provincia=provincia,
        municipio=municipio,
        nombre=None,
        telefono=frm,
        email=None,
        assigned_to=assigned_to,
        status="assigned" if assigned_to else "new",
        notes=body or None,
        meta_json={
            "twilio": {"to": to, "sid": sid, "num_media": num_media}
        },
    )
    db.session.add(lead)
    db.session.commit()

    return jsonify(ok=True, lead={
        "id": lead.id,
        "kind": lead.kind,
        "telefono": lead.telefono,
        "assigned_to": lead.assigned_to,
        "status": lead.status
    })
