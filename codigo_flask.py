# codigo_flask.py
# Backend SpainRoom — app Flask principal (Render: gunicorn codigo_flask:app)

from flask import Flask, request, jsonify, Response
import requests
from math import radians, sin, cos, sqrt, atan2
from urllib.parse import unquote_plus
import os, random

app = Flask(__name__)

# === ACTIVAR DEFENSA (WAF / headers / CORS) — sin tocar /voice ===
try:
    from defense_guard import register_defense
    register_defense(app)
    print("[DEFENSE] Registrada por defense_guard", flush=True)
except Exception as e:
    print(f"[DEFENSE] No activa: {e}", flush=True)

# ------------------ Health ------------------
@app.get("/health")
def health():
    return jsonify(ok=True, service="BACKEND-SPAINROOM"), 200

# Diagnóstico defensa
@app.get("/defense/health")
def defense_health():
    return jsonify(ok=True, defense="registered"), 200

# ------------------ Utils: Haversine / Geocoder / Jobs ------------------
def calcular_distancia(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1); dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c

@app.get("/api/geocode")
def geocode():
    address = request.args.get("address")
    if not address: return jsonify({"error": "Falta parámetro address"}), 400
    url = f"https://nominatim.openstreetmap.org/search?q={address}&format=json&limit=1"
    headers = {"User-Agent": "SpainRoom/1.0"}
    r = requests.get(url, headers=headers, timeout=10)
    if r.status_code != 200 or not r.json(): return jsonify({"error": "No se pudo geocodificar"}), 500
    data = r.json()[0]; return jsonify({"lat": float(data["lat"]), "lng": float(data["lon"])})

@app.get("/api/jobs/search")
def search_jobs():
    try:
        lat = float(request.args.get("lat")); lng = float(request.args.get("lng"))
        radius = float(request.args.get("radius_km", 2)); keyword = (request.args.get("q", "")).lower()
    except Exception: return jsonify({"error": "Parámetros inválidos"}), 400
    ofertas = [
        {"id":1,"titulo":"Camarero/a","empresa":"Bar Central","lat":lat+0.01,"lng":lng+0.01},
        {"id":2,"titulo":"Dependiente/a","empresa":"Tienda Local","lat":lat+0.015,"lng":lng},
        {"id":3,"titulo":"Administrativo/a","empresa":"Gestoría","lat":lat-0.02,"lng":lng-0.01},
        {"id":4,"titulo":"Carpintero/a","empresa":"Taller Madera","lat":lat+0.03,"lng":lng+0.02},
    ]
    out = []
    for o in ofertas:
        dist = calcular_distancia(lat,lng,o["lat"],o["lng"])
        if dist <= radius and (not keyword or keyword in o["titulo"].lower()):
            out.append({"id":o["id"],"titulo":o["titulo"],"empresa":o["empresa"],"distancia_km":round(dist,2)})
    return jsonify(out)

# ------------------ IVR PERSONA NATURAL /voice/* ------------------
VOICE_PREFIX = "/voice"
TTS_VOICE = os.getenv("TTS_VOICE", "Polly.Lucia")
TWILIO_CALLER = os.getenv("TWILIO_VOICE_FROM", "+12252553716")

def _twiml(body: str) -> Response:
    body = body.strip()
    if not body.startswith("<Response"): body = f"<Response>{body}</Response>"
    return Response(body, mimetype="text/xml")

def _say_es_ssml(text: str) -> str:
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

def _line(*opts): return random.choice(opts)
def _pause(sec=0.3): return f'<Pause length="{max(0.2, min(2.0, sec))}"/>'

def _gather_es(action: str, timeout="8", end_silence="auto",
               hints=("sí, si, no, propietario, inquilino, jaen, madrid, valencia, sevilla, "
                      "barcelona, malaga, granada, soy, me llamo, mi nombre es"),
               allow_dtmf: bool=False) -> str:
    gather_input = "speech dtmf" if allow_dtmf else "speech"
    return (f'<Gather input="{gather_input}" language="es-ES" timeout="{timeout}" '
            f'speechTimeout="{end_silence}" speechModel="phone_call" '
            f'action="{action}" method="POST" actionOnEmptyResult="true" hints="{hints}">')

def _ack(): return _line("vale","ok","perfecto","genial","ajá","te sigo","sí","de una","dale")

_IVR_MEM = {}  # { CallSid: {"role":"", "zone":"", "name":"", "miss":0} }
PROVS = {"jaen":"Jaén","madrid":"Madrid","valencia":"Valencia","sevilla":"Sevilla",
         "barcelona":"Barcelona","malaga":"Málaga","granada":"Granada"}
FRAN_MAP = {
    "jaen":{"name":"Jaén","phone":"+34683634299"},
    "madrid":{"name":"Madrid Centro","phone":"+34600000001"},
    "valencia":{"name":"Valencia","phone":"+34600000003"},
    "sevilla":{"name":"Sevilla","phone":"+34600000004"},
    "barcelona":{"name":"Barcelona","phone":"+34600000005"},
    "malaga":{"name":"Málaga","phone":"+34600000006"},
    "granada":{"name":"Granada","phone":"+34600000007"},
}
_YES = {"si","sí","vale","correcto","claro","ok","de acuerdo"}; _NO = {"no","negativo"}

def _yesno(s): s=(s or "").lower().strip(); 
# fmt: off
def _yesno(s: str) -> str:
    s = (s or "").lower().strip()
    if any(w in s for w in _YES): return "yes"
    if any(w in s for w in _NO):  return "no"
    return ""
# fmt: on

def _role(s: str) -> str:
    s = (s or "").lower()
    if "propiet" in s or "dueñ" in s: return "propietario"
    if "inquil" in s or "alquil" in s or "habitacion" in s or "habitación" in s or "busco" in s: return "inquilino"
    return ""

def _zone(s: str) -> str:
    s = (s or "").lower().strip()
    s = (s.replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u"))
    aliases = {"barna":"barcelona","md":"madrid","vlc":"valencia","sevill":"sevilla"}
    for k,v in aliases.items():
        if k in s: s = v
    for key in PROVS.keys():
        if key in s or s == key: return key
    return ""

def _name(s: str) -> str:
    s = (s or "").strip(); low = s.lower()
    for cue in ["me llamo","soy","mi nombre es"]:
        if cue in low:
            after = s.lower().split(cue,1)[1].strip()
            return after.title()[:60]
    parts = [w for w in s.split() if len(w)>1]
    return parts[0].title()[:40] if parts else ""

def _assign(zone_key: str): return FRAN_MAP.get(zone_key or "", {"name":"Central SpainRoom","phone":None})

@app.get(f"{VOICE_PREFIX}/health")
def voice_health(): return jsonify(ok=True, service="voice"), 200

# BLINDADO: acepta GET y POST
@app.route(f"{VOICE_PREFIX}/answer", methods=["GET","POST"])
def voice_answer():
    tw = ("<Response>"
          + _gather_es(f"{VOICE_PREFIX}/handle")
          + _say_es_ssml(_line("Hola, ¿cómo vas? Soy de SpainRoom.","¡Ey! Soy de SpainRoom, cuéntame."))
          + _say_es_ssml("Dime en una frase: ¿eres propietario o inquilino, y de qué provincia?")
          + "</Gather>"
          + _say_es_ssml("No te pillé, vamos otra vez.")
          + f'<Redirect method="POST">{VOICE_PREFIX}/answer</Redirect>'
          + "</Response>")
    return _twiml(tw)

@app.route(f"{VOICE_PREFIX}/handle", methods=["GET","POST"])
def voice_handle():
    if request.method == "GET":
        return _twiml(_say_es_ssml("Te escucho…") + f'<Redirect method="POST">{VOICE_PREFIX}/answer</Redirect>')
    call_id = unquote_plus(request.form.get("CallSid",""))
    mem = _IVR_MEM.setdefault(call_id, {"role":"", "zone":"", "name":"", "miss":0})
    speech = unquote_plus(request.form.get("SpeechResult","")); s = (speech or "").lower().strip()

    if not mem["role"]:
        r = _role(s);  mem["role"] = r or mem["role"]
    if not mem["zone"]:
        z = _zone(s);  mem["zone"] = z or mem["zone"]
    if not mem["name"]:
        n = _name(speech); mem["name"] = n or mem["name"]

    missing = []; 
    if not mem["role"]: missing.append("rol")
    if not mem["zone"]: missing.append("provincia")
    if missing:
        mem["miss"] += 1; ask = missing[0]
        if ask == "rol":
            tw = ("<Response>" + _gather_es(f"{VOICE_PREFIX}/handle")
                  + _say_es_ssml(_line("¿Eres propietario o inquilino?","Vale, ¿propietario o inquilino?"))
                  + "</Gather></Response>")
        else:
            tw = ("<Response>" + _gather_es(f"{VOICE_PREFIX}/handle")
                  + _say_es_ssml(_line("¿De qué provincia me llamas?","Dime solo la provincia, porfa."))
                  + "</Gather></Response>")
        return _twiml(tw)

    zone_h = PROVS.get(mem["zone"], mem["zone"].title() or "tu zona")
    tw = ("<Response>" + _gather_es(f"{VOICE_PREFIX}/confirm", allow_dtmf=True)
          + _say_es_ssml(_line(
              f"{_line('Genial','Perfecto','Vale')}. {mem['name']+', ' if mem['name'] else ''}"
              f"{'propietario' if mem['role']=='prop
