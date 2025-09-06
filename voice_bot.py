# voice_bot.py  — SpainRoom IVR básico (DTMF + voz)
from flask import Blueprint, request, Response
from urllib.parse import unquote_plus

bp_voice = Blueprint("voice", __name__)

def xml(twiml: str) -> Response:
    """Devuelve TwiML como text/xml (Twilio)."""
    body = twiml.strip()
    if not body.startswith("<Response"):
        body = f"<Response>{body}</Response>"
    return Response(body, mimetype="text/xml")

def say_es(texto: str) -> str:
    # 'alice' funciona sin add-ons. (Polly-* requiere features extra)
    return f'<Say language="es-ES" voice="alice">{texto}</Say>'

def say_en(text: str) -> str:
    return f'<Say language="en-US" voice="alice">{text}</Say>'

def gather_es(action: str) -> str:
    return (
        f'<Gather input="speech dtmf" language="es-ES" numDigits="1" '
        f'timeout="5" action="{action}" method="POST">'
    )

def get_field(name: str, default: str = "") -> str:
    # Twilio manda x-www-form-urlencoded; Flask lo pone en request.form
    val = request.form.get(name, default)
    return unquote_plus(val) if val else default
# ---------------- Health ----------------
@bp_voice.get("/health")
def voice_health():
    return {"ok": True, "service": "voice"}, 200

# --------------- /voice/answer (entrada) ---------------
# Webhook principal (Voice > A Call Comes In)
@bp_voice.post("/answer")
def answer():
    # Menú bilingüe, con Gather a /voice/lang-or-intent
    twiml = f"""
    <Response>
      {say_es("Bienvenido a SpainRoom. Pulsa 1 para español, 2 para inglés. "
              "También puedes decir reservas, propietarios, franquiciados u oportunidades.")}
      <Pause length="1"/>
      {say_en("Welcome to SpainRoom. Press 1 for Spanish, 2 for English. "
              "You may also say reservations, landlords, franchisees or opportunities.")}
      {gather_es("/voice/lang-or-intent")}
        <!-- Repetimos las instrucciones dentro del Gather para compatibilidad -->
        {say_es("Pulsa 1 para español, 2 para inglés. "
                "O bien di: reservas, propietarios, franquiciados u oportunidades.")}
      </Gather>
      {say_es("No recibí respuesta.")}
      <Redirect method="POST">/voice/fallback</Redirect>
    </Response>
    """
    return xml(twiml)
# --------------- /voice/lang-or-intent ---------------
@bp_voice.post("/lang-or-intent")
def lang_or_intent():
    digits = get_field("Digits")
    speech = get_field("SpeechResult").lower().strip()

    # Normalizamos intents en ambos idiomas
    def intent_from_speech(s: str) -> str:
        if not s:
            return ""
        # Español
        if "reserva" in s or "reser" in s:
            return "reservas"
        if "propiet" in s or "dueñ" in s or "landlord" in s:
            return "propietarios"
        if "franquici" in s or "franchise" in s:
            return "franquiciados"
        if "oportunidad" in s or "opport" in s or "colabora" in s:
            return "oportunidades"
        # Inglés rápido
        if s in {"1","spanish","español"}:
            return "lang_es"
        if s in {"2","english","inglés","ingles"}:
            return "lang_en"
        return ""

    # Prioridad: DTMF para idioma, luego Speech
    if digits == "1":
        return xml(route_lang("es"))
    if digits == "2":
        return xml(route_lang("en"))

    # Intent por voz
    intent = intent_from_speech(speech)

    if intent == "lang_es":
        return xml(route_lang("es"))
    if intent == "lang_en":
        return xml(route_lang("en"))

    if intent in {"reservas","propietarios","franquiciados","oportunidades"}:
        return xml(route_intent(intent))

    # Nada claro: reintento soft
    return xml(
        f"""
        <Response>
          {say_es("No te entendí bien. ¿Puedes repetir?")}
          {gather_es("/voice/lang-or-intent")}
            {say_es("Pulsa 1 para español, 2 para inglés, o di: reservas, propietarios, "
                    "franquiciados u oportunidades.")}
          </Gather>
          <Redirect method="POST">/voice/fallback</Redirect>
        </Response>
        """
    )

def route_lang(lang: str) -> str:
    if lang == "es":
        return (
            f"{say_es('Has elegido español.')}"
            '<Redirect method="POST">/voice/loop?lang=es</Redirect>'
        )
    else:
        return (
            f"{say_en('You selected English.')}"
            '<Redirect method="POST">/voice/loop?lang=en</Redirect>'
        )

def route_intent(intent: str) -> str:
    # Respuestas demo y redirect a loop para seguir en menú
    mapping = {
        "reservas": say_es("Reservas. Te enviaré un SMS con el enlace a la web."),
        "propietarios": say_es("Propietarios. Podemos verificar la cédula y subir tus viviendas."),
        "franquiciados": say_es("Franquiciados. Gestiona tu zona, propietarios e inquilinos."),
        "oportunidades": say_es("Oportunidades. Te contamos cómo colaborar con SpainRoom.")
    }
    msg = mapping.get(intent, say_es("De acuerdo."))
    return f"""{msg}<Redirect method="POST">/voice/loop</Redirect>"""

# --------------- /voice/loop (menú persistente) ---------------
@bp_voice.post("/loop")
def loop_menu():
    lang = request.args.get("lang", "es")
    if lang == "en":
        body = (
            say_en("Main menu. Say: reservations, landlords, franchisees or opportunities. "
                   "Or press 1 for Spanish, 2 for English.")
            + gather_es("/voice/lang-or-intent")  # Gather en es-ES capta bien dtmf + voz
            + say_en("Please say your option or press 1 Spanish, 2 English.")
            + "</Gather>"
        )
    else:
        body = (
            say_es("Menú principal. Di: reservas, propietarios, franquiciados u oportunidades. "
                   "O pulsa 1 para español, 2 para inglés.")
            + gather_es("/voice/lang-or-intent")
            + say_es("Por favor, di tu opción o pulsa 1 o 2.")
            + "</Gather>"
        )
    return xml(body)

# --------------- /voice/fallback ---------------
@bp_voice.post("/fallback")
def fallback():
    return xml(
        say_es("Vamos a intentarlo de nuevo.")
        + '<Redirect method="POST">/voice/answer</Redirect>'
    )
# --------------- /voice/debug (opcional) ---------------
@bp_voice.post("/debug")
def debug():
    # Inspección rápida de lo que envía Twilio
    kv = {k: v for k, v in request.form.items()}
    return {"ok": True, "form": kv}, 200
