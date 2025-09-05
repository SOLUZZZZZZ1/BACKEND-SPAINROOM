from flask import Blueprint, request, Response

bp_voice = Blueprint("voice", __name__, url_prefix="/voice")

@bp_voice.post("/answer")
def answer():
    twiml = """
    <Response>
      <Gather input="speech dtmf" language="es-ES" numDigits="1" timeout="5"
              action="/voice/lang-or-intent" method="POST">
        <Say language="es-ES" voice="Polly-Conchita">
          Bienvenido a SpainRoom. Pulsa 1 para español, 2 para inglés.
          También puedes decir reservas, propietarios, franquiciados u oportunidades.
        </Say>
        <Pause length="1"/>
        <Say language="en-US" voice="Polly-Joanna">
          Welcome to SpainRoom. Press 1 for Spanish, 2 for English.
          You may also say reservations, landlords, franchisees or opportunities.
        </Say>
      </Gather>
      <Say>No recibí respuesta.</Say>
      <Redirect method="POST">/voice/fallback</Redirect>
    </Response>
    """
    return Response(twiml, mimetype="text/xml")

@bp_voice.post("/lang-or-intent")
def lang_or_intent():
    digits = request.form.get("Digits")
    speech = (request.form.get("SpeechResult") or "").lower()
    if digits == "1" or "reserva" in speech:
        msg = "Has seleccionado reservas."
    elif digits == "2" or "english" in speech or "reservation" in speech:
        msg = "You selected English / reservations."
    elif "propiet" in speech:
        msg = "Propietarios."
    elif "franquic" in speech:
        msg = "Franquiciados."
    elif "oportun" in speech:
        msg = "Oportunidades."
    else:
        msg = "No entendí tu solicitud."
    twiml = f"<Response><Say>{msg}</Say></Response>"
    return Response(twiml, mimetype="text/xml")

@bp_voice.post("/fallback")
def fallback():
    return Response("<Response><Say>Lo siento, no pude procesar tu solicitud.</Say></Response>", mimetype="text/xml")

@bp_voice.post("/status")
def status():
    return Response("<Response></Response>", mimetype="text/xml")
