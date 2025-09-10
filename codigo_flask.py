# codigo_flask.py
# Backend SpainRoom — app Flask principal (Render: gunicorn codigo_flask:app)

from flask import Flask, request, jsonify, Response
import requests
from math import radians, sin, cos, sqrt, atan2
from urllib.parse import unquote_plus
import os, random

# =========================================================
#  APP FLASK (Render usa gunicorn codigo_flask:app)
# =========================================================
app = Flask(__name__)

# =========================================================
#  SMOKE TEST /health
# =========================================================
@app.get("/health")
def health():
    return jsonify(ok=True, service="BACKEND-SPAINROOM"), 200


# =========================================================
#  UTILS — HAVERSINE, GEOCODER, JOBS (lo de siempre)
# =========================================================
def calcular_distancia(lat1, lon1, lat2, lon2):
    """Distancia en km (Haversine)."""
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(1 - a), sqrt(a))
    return R * c


@app.get("/api/geocode")
def geocode():
    address = request.args.get("address")
    if not address:
        return jsonify({"error": "Falta parámetro address"}), 400

    url = f"https://nominatim.openstreetmap.org/search?q={address}&format=json&limit=1"
    headers = {"User-Agent": "SpainRoom/1.0"}
    r = requests.get(url, headers=headers, timeout=10)

    if r.status_code != 200 or not r.json():
        return jsonify({"error": "No se pudo geocodificar"}), 500

    data = r.json()[0]
    return jsonify({"lat": float(data["lat"]), "lng": float(data["lon"])})


@app.get("/api/jobs/search")
def search_jobs():
    """Mock con cálculo real de distancias, radio en km y filtro por keyword."""
    try:
        lat = float(request.args.get("lat"))
        lng = float(request.args.get("lng"))
        radius = float(request.args.get("radius_km", 2))
        keyword = request.args.get("q", "").lower()
    except Exception:
        return jsonify({"error": "Parámetros inválidos"}), 400

    ofertas = [
        {"id": 1, "titulo": "Camarero/a",      "empresa": "Bar Central",   "lat": lat + 0.01,  "lng": lng + 0.01},
        {"id": 2, "titulo": "Dependiente/a",   "empresa": "Tienda Local",  "lat": lat + 0.015, "lng": lng},
        {"id": 3, "titulo": "Administrativo/a","empresa": "Gestoría",      "lat": lat - 0.02,  "lng": lng - 0.01},
        {"id": 4, "titulo": "Carpintero/a",    "empresa": "Taller Madera", "lat": lat + 0.03,  "lng": lng + 0.02},
    ]

    resultados = []
    for o in ofertas:
        dist = calcular_distancia(lat, lng, o["lat"], o["lng"])
        if dist <= radius:
            if not keyword or keyword in o["titulo"].lower():
                resultados.append({
                    "id": o["id"], "titulo": o["titulo"], "empresa": o["empresa"],
                    "distancia_km": round(dist, 2)
                })
    return jsonify(resultados)


# =========================================================
#  IVR PERSONA NATURAL — /voice/*  (voz neural + SSML + barge-in)
#  (El bot NO hace defensa: si tienes WAF/limiter, excluye estas rutas allí)
# =========================================================

VOICE_PREFIX = "/voice"
TTS_VOICE = os.getenv("TTS_VOICE", "Polly.Lucia")  # alternativas: Polly.Conchita, Polly.Enrique
TWILIO_CALLER = os.getenv("TWILIO_VOICE_FROM", "+12252553716")  # callerId en el Dial

def _twiml(body: str) -> Response:
    body = body.strip()
    if not body.startswith("<Response"):
        body = f"<Response>{body}</Response>"
    return Response(body, mimetype="text/xml")

def _say_es_ssml(text: str) -> str:
    """
    Voz neural con SSML (estilo conversacional) — suena más humana en Twilio.
    Requiere voice="Polly.*".
    """
    ssml = (
        '<speak>'
        ' <amazon:domain name="conversational">'
        '  <prosody rate="medium" pitch="+2%">'
        f'   {text}'
        '  </prosody>'
        ' </amazon:domain>'
        '</speak>'
    )
    return f'<Say language="es-ES" voice="{TTS_VOICE}">{ssml}</Say>'

def _line(*opciones: str) -> str:
    return random.choice(opciones)

def _pause(sec=0.3) -> str:
    return f'<Pause length="{max(0.2, min(2.0, sec))}"/>'

def _gather_es(action: str,
               timeout="8",
               end_silence="auto",
               hints=("sí, si, no, propietario, inquilino, jaen, madrid, valencia, sevilla, "
                      "barcelona, malaga, granada, soy, me llamo, mi nombre es"),
               allow_dtmf: bool=False) -> str:
    gather_input = "speech dtmf" if allow_dtmf else "speech"
    return (
        f'<Gather input="{gather_input}" language="es-ES" timeout="{timeout}" '
        f'speechTimeout="{end_silence}" speechModel="phone_call" '
        f'action="{action}" method="POST" actionOnEmptyResult="true" hints="{hints}">'
    )

def _ack():
    return random.choice(["vale", "ok", "perfecto", "genial", "ajá", "te sigo", "sí", "de una", "dale"])

# Memoria por llamada (producción: Redis/DB con TTL si quieres)
_IVR_MEM = {}  # { CallSid: {"role":"", "zone":"", "name":"", "miss": 0} }

PROVS = {
    "jaen": "Jaén", "madrid": "Madrid", "valencia": "Valencia", "sevilla": "Sevilla",
    "barcelona": "Barcelona", "malaga": "Málaga", "granada": "Granada"
}
# Jaén con el número real
FRAN_MAP = {
    "jaen":      {"name": "Jaén",          "phone": "+34683634299"},
    "madrid":    {"name": "Madrid Centro", "phone": "+34600000001"},
    "valencia":  {"name": "Valencia",      "phone": "+34600000003"},
    "sevilla":   {"name": "Sevilla",       "phone": "+34600000004"},
    "barcelona": {"name": "Barcelona",     "phone": "+34600000005"},
    "malaga":    {"name": "Málaga",        "phone": "+34600000006"},
    "granada":   {"name": "Granada",       "phone": "+34600000007"},
}

_YES = {"si", "sí", "vale", "correcto", "claro", "ok", "de acuerdo"}
_NO  = {"no", "negativo"}

def _yesno(s: str) -> str:
    s = (s or "").lower().strip()
    if any(w in s for w in _YES): return "yes"
    if any(w in s for w in _NO):  return "no"
    return ""

def _role(s: str) -> str:
    s = (s or "").lower()
    if "propiet" in s or "dueñ" in s: return "propietario"
    if "inquil"  in s or "alquil" in s: return "inquilino"
    if "busco" in s or "habitacion" in s or "habitación" in s: return "inquilino"
    if "alquilar" in s and "habitacion" in s: return "inquilino"
    return ""

def _zone(s: str) -> str:
    s = (s or "").lower().strip()
    s = (
        s.replace("á","a")
         .replace("é","e")
         .replace("í","i")
         .replace("ó","o")
         .replace("ú","u")
    )
    aliases = {"barna": "barcelona", "md": "madrid", "vlc": "valencia", "sevill": "sevilla"}
    for k, v in aliases.items():
        if k in s:
            s = v
    for key in PROVS.keys():
        if key in s or s == key:
            return key
    return ""

def _name(s: str) -> str:
    s = (s or "").strip()
    lower = s.lower()
    for cue in ["me llamo", "soy", "mi nombre es"]:
        if cue in lower:
            after = s.lower().split(cue,1)[1].strip()
            return after.title()[:60]
    parts = [w for w in s.split() if len(w) > 1]
    return parts[0].title()[:40] if parts else ""

def _assign(zone_key: str):
    return FRAN_MAP.get(zone_key or "", {"name": "Central SpainRoom", "phone": None})


@app.get(f"{VOICE_PREFIX}/health")
def _voice_health():
    return jsonify(ok=True, service="voice"), 200


# === BLINDADO: acepta GET y POST (por si Twilio llega en GET)
@app.route(f"{VOICE_PREFIX}/answer", methods=["GET", "POST"])
def _voice_answer():
    tw = (
        "<Response>"
        + _gather_es(f"{VOICE_PREFIX}/handle")
        + _say_es_ssml(_line("Hola, ¿cómo vas? Soy de SpainRoom.",
                             "¡Ey! Soy de SpainRoom, cuéntame."))
        + _say_es_ssml("Dime en una frase: ¿eres propietario o inquilino, y de qué provincia?")
        + "</Gather>"
        + _say_es_ssml("No te pillé, vamos otra vez.")
        + f'<Redirect method="POST">{VOICE_PREFIX}/answer</Redirect>'
        + "</Response>"
    )
    return _twiml(tw)


@app.route(f"{VOICE_PREFIX}/handle", methods=["GET", "POST"])
def _voice_handle():
    if request.method == "GET":
        # Si Twilio pegó en GET por error, devuelve TwiML y re-entra por POST
        return _twiml(_say_es_ssml("Te escucho…") +
                      f'<Redirect method="POST">{VOICE_PREFIX}/answer</Redirect>')

    call_id = unquote_plus(request.form.get("CallSid", ""))
    mem = _IVR_MEM.setdefault(call_id, {"role": "", "zone": "", "name": "", "miss": 0})

    speech = unquote_plus(request.form.get("SpeechResult", ""))
    s = (speech or "").lower().strip()

    if not mem["role"]:
        r = _role(s)
        if r: mem["role"] = r
    if not mem["zone"]:
        z = _zone(s)
        if z: mem["zone"] = z
    if not mem["name"]:
        n = _name(speech)
        if n: mem["name"] = n

    missing = []
    if not mem["role"]: missing.append("rol")
    if not mem["zone"]: missing.append("provincia")

    if missing:
        mem["miss"] += 1
        ask = missing[0]
        if ask == "rol":
            tw = (
                "<Response>"
                + _gather_es(f"{VOICE_PREFIX}/handle")
                + _say_es_ssml(_line("¿Eres propietario o inquilino?",
                                     "Vale, ¿propietario o inquilino?"))
                + "</Gather>"
                + "</Response>"
            )
        else:
            tw = (
                "<Response>"
                + _gather_es(f"{VOICE_PREFIX}/handle")
                + _say_es_ssml(_line("¿De qué provincia me llamas?",
                                     "Dime solo la provincia, porfa."))
                + "</Gather>"
                + "</Response>"
            )
        return _twiml(tw)

    zone_h = PROVS.get(mem["zone"], mem["zone"].title() or "tu zona")
    mem["miss"] = 0
    tw = (
        "<Response>"
        + _gather_es(f"{VOICE_PREFIX}/confirm", allow_dtmf=True)
        + _say_es_ssml(_line(
            f"{_line('Genial','Perfecto','Vale')}. {mem['name'] + ', ' if mem['name'] else ''}"
            f"{'propietario' if mem['role']=='propietario' else 'inquilino'} en {zone_h}. "
            "¿Te paso con la persona de tu zona?",
            f"{mem['name'] + ', ' if mem['name'] else ''}¿te va bien que te pase ya con {zone_h}?"))
        + "</Gather>"
        + "</Response>"
    )
    return _twiml(tw)


@app.route(f"{VOICE_PREFIX}/confirm", methods=["GET", "POST"])
def _voice_confirm():
    if request.method == "GET":
        # Repregunta muy breve para GETs accidentales
        return _twiml(
            "<Response>"
            + _gather_es(f"{VOICE_PREFIX}/confirm", allow_dtmf=True)
            + _say_es_ssml("¿sí o no?")
            + "</Gather>"
            + "</Response>"
        )

    call_id = unquote_plus(request.form.get("CallSid", ""))
    mem = _IVR_MEM.get(call_id, {"role": "", "zone": "", "name": "", "miss": 0})

    yn  = _yesno(unquote_plus(request.form.get("SpeechResult", "")))
    d   = (request.form.get("Digits") or "").strip()
    if d == "1": yn = "yes"
    if d == "2": yn = "no"

    if yn == "yes":
        fran = _assign(mem["zone"])
        if fran and fran.get("phone"):
            return _twiml(
                "<Response>"
                + _say_es_ssml("Genial, un segundo…")
                + _pause(0.25)
                + f'<Dial callerId="{TWILIO_CALLER}"><Number>{fran["phone"]}</Number></Dial>'
                + "</Response>"
            )
        return _twiml(
            "<Response>"
            + _say_es_ssml("No ubico al responsable ahora mismo. Te dejo buzón.")
            + f'<Record maxLength="120" playBeep="true" action="{VOICE_PREFIX}/answer" method="POST"/>'
            + "</Response>"
        )

    if yn == "no":
        return _twiml(
            "<Response>"
            + _gather_es(f"{VOICE_PREFIX}/handle")
            + _say_es_ssml("Vale, dime de qué provincia y lo ajusto.")
            + "</Gather>"
            + "</Response>"
        )

    # No entendido → repregunta brevísima
    return _twiml(
        "<Response>"
        + _gather_es(f"{VOICE_PREFIX}/confirm", allow_dtmf=True)
        + _say_es_ssml("¿sí o no?")
        + "</Gather>"
        + "</Response>"
    )


# =========================================================
#  ROOT y FALLBACK seguros (Twilio jamás verá 404)
# =========================================================
@app.route("/", methods=["GET", "POST"])
def _root_safe():
    # Si Twilio pega al root por error, devuelve TwiML y redirige al flujo
    if request.method == "POST":
        return _twiml(_say_es_ssml("Hola, te atiendo ahora mismo.") +
                      f'<Redirect method="POST">{VOICE_PREFIX}/answer</Redirect>')
    return ("", 404)

@app.route(f"{VOICE_PREFIX}/fallback", methods=["GET", "POST"])
def _voice_fallback():
    return _twiml(_say_es_ssml("Uff, un segundo…") +
                  f'<Redirect method="POST">{VOICE_PREFIX}/answer</Redirect>')


# =========================================================
#  DIAGNÓSTICO: LISTAR RUTAS CARGADAS (puedes borrarla después)
# =========================================================
@app.get("/__routes")
def __routes():
    return {"routes": [f"{r.endpoint} -> {r.rule}" for r in app.url_map.iter_rules()]}, 200


# =========================================================
#  MAIN LOCAL
# =========================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
