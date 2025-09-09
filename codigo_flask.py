# codigo_flask.py
# Backend SpainRoom — app Flask principal (Render: gunicorn codigo_flask:app)

from flask import Flask, request, jsonify, Response
import requests
from math import radians, sin, cos, sqrt, atan2
from urllib.parse import unquote_plus
import os, time

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
                # Log y 403 sin cuerpo; no interrumpe tus logs
                print(f"[DEFENSE] Bloqueado: {reason} {request.method} {request.path}", flush=True)
                return ("", 403)
        except Exception as e:
            # Nunca romper por el gate
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
        {"id": 1, "titulo": "Camarero/a",     "empresa": "Bar Central",   "lat": lat + 0.01,  "lng": lng + 0.01},
        {"id": 2, "titulo": "Dependiente/a",  "empresa": "Tienda Local",  "lat": lat + 0.015, "lng": lng},
        {"id": 3, "titulo": "Administrativo/a","empresa": "Gestoría",     "lat": lat - 0.02,  "lng": lng - 0.01},
        {"id": 4, "titulo": "Carpintero/a",   "empresa": "Taller Madera", "lat": lat + 0.03,  "lng": lng + 0.02},
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
# =========================================================
def _try_register(label: str, import_path: str, attr: str = None, url_prefix: str = None, print_ok: str = None):
    """Registra un blueprint si existe sin romper el arranque."""
    try:
        module = __import__(import_path, fromlist=['*'])
        bp = getattr(module, attr) if attr else getattr(module, "bp", None)
        if bp is None:
            return
        # Evitar doble registro
        if any(getattr(b, "name", "") == bp.name for b in app.blueprints.values()):
            return
        if url_prefix:
            app.register_blueprint(bp, url_prefix=url_prefix)
        else:
            app.register_blueprint(bp)
        if print_ok:
            print(print_ok, flush=True)
    except Exception:
        # No cortamos el arranque si falta
        pass

# Si existen en tu proyecto, se registran; si no, se ignoran (sin romper):
_try_register("DEFENSE",       "defense_bot",          "bp_defense",       "/defense",      "[DEFENSE] Activa.")
_try_register("AUTH",          "auth",                 "bp_auth",          "/auth",         "[AUTH] Blueprint auth registrado.")
_try_register("OPPORTUNITIES", "opportunities",        "bp_opportunities", "/opportunities","[OPPORTUNITIES] Blueprint registrado.")
_try_register("PAYMENTS",      "payments",             "bp_payments",      "/payments",     "[PAYMENTS] Blueprint registrado.")
# Si ya tienes voice_bot.py con bp_voice, lo registramos con /voice
_try_register("VOICE",         "voice_bot",            "bp_voice",         "/voice",        "[VOICE] Blueprint voice registrado.")


# =========================================================
#  IVR / VOZ — FALLBACK EN ESTE MISMO ARCHIVO (si no hay voice_bot)
#  → Activo solo si NO existe ruta /voice/answer tras los try_register
# =========================================================
def _have_route(rule: str) -> bool:
    try:
        return any(getattr(r, "rule", "") == rule for r in app.url_map.iter_rules())
    except Exception:
        return False

if not _have_route("/voice/answer"):
    # --- Fallback mínimo (menú de intents + español/inglés) ---
    def _twiml(body: str) -> Response:
        body = body.strip()
        if not body.startswith("<Response"):
            body = f"<Response>{body}</Response>"
        return Response(body, mimetype="text/xml")

    def _say_es(texto: str) -> str:
        return f'<Say language="es-ES" voice="alice">{texto}</Say>'

    def _say_en(texto: str) -> str:
        return f'<Say language="en-US" voice="alice">{texto}</Say>'

    def _gather_es(action: str) -> str:
        return (f'<Gather input="speech dtmf" language="es-ES" numDigits="1" '
                f'timeout="5" action="{action}" method="POST">')

    @app.get("/voice/health")
    def _voice_health():
        return jsonify(ok=True, service="voice_menu_fallback"), 200

    @app.post("/voice/answer")
    def _voice_answer():
        twiml = f"""
        <Response>
          {_say_es("Bienvenido a SpainRoom. Pulsa 1 para español, 2 para inglés. "
                   "También puedes decir reservas, propietarios, franquiciados u oportunidades.")}
          <Pause length="1"/>
          {_say_en("Welcome to SpainRoom. Press 1 for Spanish, 2 for English. "
                   "You may also say reservations, landlords, franchisees or opportunities.")}
          {_gather_es("/voice/lang-or-intent")}
            {_say_es("Pulsa 1 para español, 2 para inglés, o di: reservas, propietarios, franquiciados u oportunidades.")}
          </Gather>
          {_say_es("No recibí respuesta.")}
          <Redirect method="POST">/voice/fallback</Redirect>
        </Response>
        """
        return _twiml(twiml)

    def _intent_from_speech(s: str) -> str:
        s = (s or "").lower().strip()
        if "reserva" in s or "reser" in s: return "reservas"
        if "propiet" in s or "dueñ" in s or "landlord" in s: return "propietarios"
        if "franquici" in s or "franchise" in s: return "franquiciados"
        if "oportunidad" in s or "opport" in s or "colabora" in s: return "oportunidades"
        if s in {"1","spanish","español"}: return "lang_es"
        if s in {"2","english","inglés","ingles"}: return "lang_en"
        return ""

    def _route_lang(lang: str) -> str:
        if lang == "es":
            return (_say_es("Has elegido español.")
                    + '<Redirect method="POST">/voice/loop?lang=es</Redirect>')
        return (_say_en("You selected English.")
                + '<Redirect method="POST">/voice/loop?lang=en</Redirect>')

    def _route_intent(intent: str) -> str:
        mapping = {
            "reservas": _say_es("Reservas. Te enviaré un SMS con el enlace a la web."),
            "propietarios": _say_es("Propietarios. Podemos verificar la cédula y subir tus viviendas."),
            "franquiciados": _say_es("Franquiciados. Gestiona tu zona, propietarios e inquilinos."),
            "oportunidades": _say_es("Oportunidades. Te contamos cómo colaborar con SpainRoom."),
        }
        msg = mapping.get(intent, _say_es("De acuerdo."))
        return f"{msg}<Redirect method='POST'>/voice/loop</Redirect>"

    @app.post("/voice/lang-or-intent")
    def _voice_lang_or_intent():
        digits = unquote_plus(request.form.get("Digits", ""))
        speech = unquote_plus(request.form.get("SpeechResult", "")).lower().strip()

        if digits == "1":
            return _twiml(_route_lang("es"))
        if digits == "2":
            return _twiml(_route_lang("en"))

        intent = _intent_from_speech(speech)

        if intent == "lang_es":
            return _twiml(_route_lang("es"))
        if intent == "lang_en":
            return _twiml(_route_lang("en"))

        if intent in {"reservas","propietarios","franquiciados","oportunidades"}:
            return _twiml(_route_intent(intent))

        # Reintento suave
        twiml = ( _say_es("No te entendí bien. ¿Puedes repetir?")
                  + _gather_es("/voice/lang-or-intent")
                  + _say_es("Pulsa 1 para español, 2 para inglés, o di: reservas, propietarios, franquiciados u oportunidades.")
                  + "</Gather>"
                  + '<Redirect method="POST">/voice/fallback</Redirect>' )
        return _twiml(twiml)

    @app.post("/voice/loop")
    def _voice_loop():
        lang = request.args.get("lang", "es")
        if lang == "en":
            body = ( _say_en("Main menu. Say: reservations, landlords, franchisees or opportunities. "
                             "Or press 1 for Spanish, 2 for English.")
                     + _gather_es("/voice/lang-or-intent")
                     + _say_en("Please say your option or press 1 Spanish, 2 English.")
                     + "</Gather>" )
        else:
            body = ( _say_es("Menú principal. Di: reservas, propietarios, franquiciados u oportunidades. "
                             "O pulsa 1 para español, 2 para inglés.")
                     + _gather_es("/voice/lang-or-intent")
                     + _say_es("Por favor, di tu opción o pulsa 1 o 2.")
                     + "</Gather>" )
        return _twiml(body)

    @app.post("/voice/fallback")
    def _voice_fallback():
        return _twiml(_say_es("Vamos a intentarlo de nuevo.") + '<Redirect method="POST">/voice/answer</Redirect>')

    print("[VOICE] Fallback de voz activo en /voice/* (no se encontró voice_bot)", flush=True)


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
