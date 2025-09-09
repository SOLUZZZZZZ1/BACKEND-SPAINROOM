# codigo_flask.py
# Backend SpainRoom — app Flask principal (Render: gunicorn codigo_flask:app)

from flask import Flask, request, jsonify, Response
import requests
from math import radians, sin, cos, sqrt, atan2
from urllib.parse import unquote_plus
import os, time, random

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
#  DEFENSE: cortafuegos ligero y rate-limit (si está disponible)
#  * No rompe si falta defense.py o si ya tienes Limiter en otro módulo
# =========================================================
try:
    import defense as _defense  # tu archivo defense.py

    # 1) Rate-limit opcional (solo si defense expone Limiter y get_remote_address)
    Limiter = getattr(_defense, "Limiter", None)
    get_remote_address = getattr(_defense, "get_remote_address", None)
    if Limiter and get_remote_address:
        # Si ya tienes Limiter global, comenta estas líneas
        limiter = Limiter(
            key_func=get_remote_address,
            app=app,
            default_limits=["200 per minute"],  # ajusta a tu gusto
        )
        print("[DEFENSE] Rate limit activo.", flush=True)

    # 2) Gate de defensa (antes de cualquier endpoint)
    _looks_malicious = getattr(_defense, "_looks_malicious", None)

    @app.before_request
    def _defense_gate():
        if not _looks_malicious:
            return
        try:
            reason = _looks_malicious()  # devuelve str|None según defense.py
            if reason:
                print(f"[DEFENSE] Bloqueado: {reason} {request.method} {request.path}", flush=True)
                return ("", 403)
        except Exception as e:
            print(f"[DEFENSE] Warning: {e}", flush=True)

    print("[DEFENSE] Cortafuegos activo.", flush=True)

except Exception as e:
    print(f"[DEFENSE] No activo (sin romper): {e}", flush=True)


# =========================================================
#  UTILS EXISTENTES — HAVERSINE, GEOCODER, JOBS
# =========================================================
def calcular_distancia(lat1, lon1, lat2, lon2):
    """Distancia en km (Haversine)."""
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c


@app.get("/api/geocode")
def geocode():
    address = request.args.get("address")
    if not address:
        return jsonify({"error": "Falta parámetro address"}), 400

    url = f"https://nominatim.openstreetmap.org/search?q={address}&format=json&limit=1"
    headers = {"User-Agent": "SpainRoom/1.0"}  # Nominatim exige User-Agent
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
#  REGISTRO OPCIONAL DE TUS BLUEPRINTS (NO ROMPE SI NO ESTÁN)
#  Mantenemos mensajes similares a tus logs anteriores
#  (NO registramos el antiguo VOICE para evitar choque con /voice/* de abajo)
# =========================================================
def _try_register(label: str, import_path: str, attr: str = None, url_prefix: str = None, print_ok: str = None):
    """Registra un blueprint si existe sin romper el arranque."""
    try:
        module = __import__(import_path, fromlist=['*'])
        bp = getattr(module, attr) if attr else getattr(module, "bp", None)
        if bp is None:
            return
        if any(getattr(b, "name", "") == bp.name for b in app.blueprints.values()):
            return
        if url_prefix:
            app.register_blueprint(bp, url_prefix=url_prefix)
        else:
            app.register_blueprint(bp)
        if print_ok:
            print(print_ok, flush=True)
    except Exception:
        pass

_try_register("AUTH",          "auth",          "bp_auth",          "/auth",          "[AUTH] Blueprint auth registrado.")
_try_register("OPPORTUNITIES", "opportunities", "bp_opportunities", "/opportunities", "[OPPORTUNITIES] Blueprint registrado.")
_try_register("PAYMENTS",      "payments",      "bp_payments",      "/payments",      "[PAYMENTS] Blueprint registrado.")
# (no registramos VOICE externo aquí para no chocar con /voice/* de abajo)


# =========================================================
#  IVR PERSONA NATURAL — /voice/*  (barge-in + cortes naturales)
# =========================================================

def _twiml(body: str) -> Response:
    body = body.strip()
    if not body.startswith("<Response"):
        body = f"<Response>{body}</Response>"
    return Response(body, mimetype="text/xml")

def _say_es(text: str) -> str:
    return f'<Say language="es-ES" voice="alice">{text}</Say>'

def _pause(sec=0.4) -> str:
    return f'<Pause length="{max(0.2, min(2.0, sec))}"/>'

def _gather_es(action: str, timeout="5", end_silence="auto",
               hints: str = "propietario, inquilino, jaen, madrid, valencia, sevilla, barcelona, malaga, granada"):
    # bargeIn permite interrumpir mientras habla
    return (f'<Gather input="speech" language="es-ES" timeout="{timeout}" '
            f'speechTimeout="{end_silence}" action="{action}" method="POST" '
            f'bargeIn="true" actionOnEmptyResult="true" hints="{hints}">')

def _ack():
    return random.choice(["vale", "ok", "perfecto", "genial", "ajá", "te sigo", "sí", "de una", "dale"])

def _short():
    return _pause(0.3)

# Memoria por llamada (producción: Redis/DB con TTL)
_IVR_MEM = {}  # { CallSid: {"role":"", "zone":"", "name":""} }

PROVS = {
    "jaen": "Jaén", "madrid": "Madrid", "valencia": "Valencia", "sevilla": "Sevilla",
    "barcelona": "Barcelona", "malaga": "Málaga", "granada": "Granada"
}
# Jaén con el número real que me diste (+34683634299)
FRAN_MAP = {
    "jaen":      {"name": "Jaén",          "phone": "+34683634299"},
    "madrid":    {"name": "Madrid Centro", "phone": "+34600000001"},
    "valencia":  {"name": "Valencia",      "phone": "+34600000003"},
    "sevilla":   {"name": "Sevilla",       "phone": "+34600000004"},
    "barcelona": {"name": "Barcelona",     "phone": "+34600000005"},
    "malaga":    {"name": "Málaga",        "phone": "+34600000006"},
    "granada":   {"name": "Granada",       "phone": "+34600000007"},
}

_YES = {"si","sí","vale","correcto","claro","ok","de acuerdo"}
_NO  = {"no","negativo"}

def _yesno(s: str) -> str:
    s = (s or "").lower().strip()
    if any(w in s for w in _YES): return "yes"
    if any(w in s for w in _NO):  return "no"
    return ""

def _role(s: str) -> str:
    s = (s or "").lower()
    if "propiet" in s or "dueñ" in s: return "propietario"
    if "inquil"  in s or "alquil" in s: return "inquilino"
    return ""

def _zone(s: str) -> str:
    s = (s or "").lower().strip()
    s = s.replace("á","a").replace("é","e").replace("í","i").","
    s = s.replace("ó","o").replace("ú","u")
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

@app.get("/voice/health")
def _voice_health():
    return jsonify(ok=True, service="voice"), 200

@app.post("/voice/answer")
def _voice_answer():
    twiml = (
        _say_es("Ey, ¿qué tal? Soy de SpainRoom.")
        + _short()
        + _say_es("Cuéntame rápido: ¿eres propietario o inquilino, y de qué provincia?")
        + _gather_es("/voice/handle")
        + _say_es("Por ejemplo: Soy inquilino en Jaen y me llamo Ana.")
        + "</Gather>"
        + _say_es("Uff, no pillé nada, vamos de nuevo.")
        + '<Redirect method="POST">/voice/answer</Redirect>'
    )
    return _twiml(twiml)

@app.post("/voice/handle")
def _voice_handle():
    call_id = unquote_plus(request.form.get("CallSid",""))
    mem = _IVR_MEM.setdefault(call_id, {"role":"", "zone":"", "name":""})

    speech = unquote_plus(request.form.get("SpeechResult",""))
    speech_l = speech.lower().strip()

    if not mem["role"]:
        r = _role(speech_l)
        if r: mem["role"] = r
    if not mem["zone"]:
        z = _zone(speech_l)
        if z: mem["zone"] = z
    if not mem["name"]:
        n = _name(speech)
        if n: mem["name"] = n

    missing = []
    if not mem["role"]: missing.append("rol")
    if not mem["zone"]: missing.append("provincia")
    if not mem["name"]: missing.append("nombre")

    if missing:
        ask = missing[0]
        if ask == "rol":
            tw = (_say_es(f"{_ack()}. ¿Eres propietario o inquilino?")
                  + _gather_es("/voice/handle") + _say_es("Por ejemplo: soy propietario, o soy inquilino.") + "</Gather>")
        elif ask == "provincia":
            tw = (_say_es(f"{_ack()}. ¿De qué provincia me llamas?")
                  + _gather_es("/voice/handle") + _say_es("Ejemplo: Jaen, Madrid, Valencia o Sevilla.") + "</Gather>")
        else:
            tw = (_say_es(f"{_ack()}. ¿Cómo te llamas?")
                  + _gather_es("/voice/handle") + _say_es("Ejemplo: me llamo Ana.") + "</Gather>")
        return _twiml(tw)

    zone_h = PROVS.get(mem["zone"], mem["zone"].title() or "tu zona")
    tw = (_say_es(f"{_ack()}. Perfecto {mem['name']}. Eres {mem['role']} en {zone_h}. ¿Te paso ahora con la persona de {zone_h}?")
          + _gather_es("/voice/confirm") + _say_es("Solo dime: si o no.") + "</Gather>")
    return _twiml(tw)

@app.post("/voice/confirm")
def _voice_confirm():
    call_id = unquote_plus(request.form.get("CallSid",""))
    mem = _IVR_MEM.get(call_id, {"role":"", "zone":"", "name":""})
    yn = _yesno(unquote_plus(request.form.get("SpeechResult","")))
    if yn == "yes":
        fran = _assign(mem["zone"])
        if fran and fran.get("phone"):
            caller = os.getenv("TWILIO_VOICE_FROM", "+12252553716")
            return _twiml(
                _say_es(f"Genial, un segundo…")
                + _short()
                + f'<Dial callerId="{caller}"><Number>{fran["phone"]}</Number></Dial>'
            )
        return _twiml(
            _say_es("No ubico al responsable ahora mismo. Déjame un mensaje y te devuelven la llamada.")
            + '<Record maxLength="120" playBeep="true" action="/voice/answer" method="POST"/>'
        )
    elif yn == "no":
        return _twiml(
            _say_es("Ok, corrijamos eso. ¿De qué provincia me llamas?")
            + _gather_es("/voice/handle") + _say_es("Ejemplo: Jaen, Madrid, Valencia o Sevilla.") + "</Gather>"
        )
    else:
        return _twiml(
            _say_es("Perdona, ¿me confirmas con un si o un no?")
            + _gather_es("/voice/confirm") + _say_es("¿Vamos? Solo si o no.") + "</Gather>"
        )


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
