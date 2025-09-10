# codigo_flask.py
# SpainRoom · Backend principal (Render: gunicorn codigo_flask:app)
# - Defensa (WAF ligero) embebida y ACTIVA (no inspecciona /voice, /health, /__routes)
# - IVR /voice/*: formulario conversacional PASO A PASO (rol→población→nombre→teléfono→nota)
# - Un solo <Say> por turno (sin micro-cortes), barge-in y timeout generoso
# - Tarea pendiente con resumen + transcripción; SMS a franquiciados (multi-destino)
# - Endpoints: /tasks/list (ver pendientes), /voice/* flujo completo
# - Geocoder/Jobs de ejemplo

from flask import Flask, request, jsonify, Response
import requests, json, os, re, random
from math import radians, sin, cos, sqrt, atan2
from urllib.parse import unquote_plus
from datetime import datetime, timezone

app = Flask(__name__)

# =========================================================
#  DEFENSA (WAF ligero embebido) — ACTIVA
#  (No inspecciona /voice/*, /health ni /__routes)
# =========================================================
DEF_CFG = {
    "MAX_BODY": int(os.getenv("DEFENSE_MAX_BODY", "524288")),
    "ALLOW_METHODS": set((os.getenv("DEFENSE_ALLOW_METHODS", "GET,POST,OPTIONS")).split(",")),
    "ALLOW_CT": set(ct.strip().lower() for ct in os.getenv(
        "DEFENSE_ALLOW_CT",
        "application/json,application/x-www-form-urlencoded,multipart/form-data,text/xml,application/xml"
    ).split(",")),
    "BLOCKED_UA": set(ua.strip().lower() for ua in os.getenv(
        "DEFENSE_BLOCKED_UA", "sqlmap,nmap,nikto,dirbuster,acunetix").split(",")),
    "STRICT_HOSTS": [h.strip().lower() for h in os.getenv("DEFENSE_HOSTS", "").split(",") if h.strip()],
    "TRUST_PROXY": os.getenv("DEFENSE_TRUST_PROXY", "true").lower() == "true",
    "ANOMALY_THRESHOLD": int(os.getenv("DEFENSE_ANOMALY_THRESHOLD", "8")),
    "SKIP_PREFIXES": [p.strip() for p in os.getenv("DEFENSE_SKIP_PREFIXES", "/voice,/__routes,/health").split(",") if p.strip()],
}

_SQLI = [
    r"(?i)\bunion\b.+\bselect\b", r"(?i)\b(select|insert|update|delete)\b.+\bfrom\b",
    r"(?i)\bor\s+1=1\b", r"(?i)\bsleep\(", r"(?i)information_schema", r"(?i)load_file\(",
]
_XSS = [r"(?i)<script\b", r"(?i)javascript:", r"(?i)onerror\s*="]
_TRAV = [r"\.\./", r"%2e%2e%2f", r"\x00"]
_BADH = ["X-Original-URL", "X-Override-URL"]

def _ip():
    if DEF_CFG["TRUST_PROXY"]:
        xf = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        return xf or (request.remote_addr or "")
    return request.remote_addr or ""

def _jlog(event, **kw):
    payload = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "event": event, "ip": _ip(), "path": request.path,
        "method": request.method, "rid": request.headers.get("X-Request-ID", "")
    }
    payload.update(kw)
    print(json.dumps(payload, ensure_ascii=False), flush=True)

def _skip():
    p = request.path or ""
    for pref in DEF_CFG["SKIP_PREFIXES"]:
        if p.startswith(pref): return True
    return False

def _host_ok():
    if not DEF_CFG["STRICT_HOSTS"]: return None
    host = (request.headers.get("Host") or "").split(":")[0].lower().strip()
    return None if host in DEF_CFG["STRICT_HOSTS"] else f"host_not_allowed:{host}"

def _ua_ok():
    ua = (request.headers.get("User-Agent") or "").lower()
    for bad in DEF_CFG["BLOCKED_UA"]:
        if bad and bad in ua: return f"ua_blocked:{bad}"
    return None

def _size_ok():
    cl = request.content_length or 0
    return None if not cl or cl <= DEF_CFG["MAX_BODY"] else f"body_too_large:{cl}"

def _ct_ok():
    if request.method in ("GET","HEAD","OPTIONS"): return None
    ct = (request.headers.get("Content-Type","") or "").split(";")[0].strip().lower()
    return None if (not ct or ct in DEF_CFG["ALLOW_CT"]) else f"bad_content_type:{ct}"

def _badh_ok():
    for h in _BADH:
        if h in request.headers: return f"bad_header:{h}"
    return None

def _hit(pats, text):
    if not text: return None
    for p in pats:
        if re.search(p, text): return p
    return None

def _qs_ok():
    qs = request.query_string.decode("utf-8","ignore")
    if _hit(_SQLI, qs): return "sqli_qs"
    if _hit(_XSS, qs):  return "xss_qs"
    return None

def _trav_ok():
    return "traversal_path" if _hit(_TRAV, (request.path or "").lower()) else None

def _body_ok():
    try:
        raw = request.get_data(cache=False, as_text=True)[:4096]
    except Exception:
        return None
    if _hit(_SQLI, raw): return "sqli_body"
    if _hit(_XSS, raw):  return "xss_body"
    return None

@app.before_request
def _waf_gate():
    if _skip():  # /voice, /health, /__routes
        return
    score, reasons = 0, []
    for chk in (_host_ok, _ua_ok, _size_ok, _ct_ok, _badh_ok, _trav_ok, _qs_ok, _body_ok):
        r = chk()
        if r:
            reasons.append(r)
            score += 2 if chk in (_host_ok,_ua_ok,_size_ok,_ct_ok,_badh_ok) else 3
    if request.method not in DEF_CFG["ALLOW_METHODS"]:
        reasons.append(f"method_not_allowed:{request.method}"); score += 2
    if score >= DEF_CFG["ANOMALY_THRESHOLD"]:
        _jlog("waf_block", reason=",".join(reasons), score=score)
        return Response(status=403)

@app.after_request
def _secure_headers(resp):
    resp.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    resp.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    return resp

@app.get("/defense/health")
def defense_health():
    return jsonify(ok=True, defense="registered", skips=list(DEF_CFG["SKIP_PREFIXES"])), 200

# =========================================================
#  SALUD / DEMOS
# =========================================================
@app.get("/health")
def health():
    return jsonify(ok=True, service="BACKEND-SPAINROOM"), 200

def calcular_distancia(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1); dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

@app.get("/api/geocode")
def geocode():
    address = request.args.get("address")
    if not address: return jsonify({"error": "Falta parámetro address"}), 400
    url = f"https://nominatim.openstreetmap.org/search?q={address}&format=json&limit=1"
    headers = {"User-Agent": "SpainRoom/1.0"}
    r = requests.get(url, headers=headers, timeout=10)
    if r.status_code != 200 or not r.json(): return jsonify({"error": "No se pudo geocodificar"}), 500
    d = r.json()[0]; return jsonify({"lat": float(d["lat"]), "lng": float(d["lon"])})

@app.get("/api/jobs/search")
def search_jobs():
    try:
        lat = float(request.args.get("lat")); lng = float(request.args.get("lng"))
        radius = float(request.args.get("radius_km", 2)); keyword = (request.args.get("q","")).lower()
    except Exception: return jsonify({"error":"Parámetros inválidos"}), 400
    ofertas = [
        {"id":1,"titulo":"Camarero/a","empresa":"Bar Central","lat":lat+0.01,"lng":lng+0.01},
        {"id":2,"titulo":"Dependiente/a","empresa":"Tienda Local","lat":lat+0.015,"lng":lng},
        {"id":3,"titulo":"Administrativo/a","empresa":"Gestoría","lat":lat-0.02,"lng":lng-0.01},
        {"id":4,"titulo":"Carpintero/a","empresa":"Taller Madera","lat":lat+0.03,"lng":lng+0.02},
    ]
    out=[]
    for o in ofertas:
        dist = calcular_distancia(lat,lng,o["lat"],o["lng"])
        if dist <= radius and (not keyword or keyword in o["titulo"].lower()):
            out.append({"id":o["id"],"titulo":o["titulo"],"empresa":o["empresa"],"distancia_km":round(dist,2)})
    return jsonify(out)

# =========================================================
#  TAREAS (DB opcional + JSONL fallback)
# =========================================================
TASKS_FILE = "/tmp/spainroom_tasks.jsonl"
db = None
try:
    from flask_sqlalchemy import SQLAlchemy
    db_url = os.getenv("SQLALCHEMY_DATABASE_URI") or os.getenv("DATABASE_URL")
    if db_url:
        app.config["SQLALCHEMY_DATABASE_URI"] = db_url
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        db = SQLAlchemy(app)
        class Task(db.Model):
            __tablename__ = "sr_tasks"
            id = db.Column(db.Integer, primary_key=True)
            call_sid = db.Column(db.String(64), index=True)
            role     = db.Column(db.String(32))
            zone     = db.Column(db.String(32))
            name     = db.Column(db.String(128))
            phone    = db.Column(db.String(32))
            assignees= db.Column(db.String(256))  # CSV de phones
            recording= db.Column(db.String(512))
            transcript = db.Column(db.Text)       # NUEVO: transcript unido
            status   = db.Column(db.String(32), default="pending")
            created_at = db.Column(db.DateTime, default=datetime.utcnow)
        with app.app_context():
            db.create_all()
except Exception as e:
    _jlog("db_init_skip", error=str(e))

def _save_task(call_sid, role, zone, name, phone, assignees, recording_url, transcript_text):
    task = {
        "created_at": datetime.utcnow().isoformat()+"Z",
        "call_sid": call_sid, "role": role, "zone": zone, "name": name,
        "phone": phone, "assignees": assignees, "recording": recording_url,
        "transcript": transcript_text, "status": "pending"
    }
    if db:
        try:
            t = Task(call_sid=call_sid, role=role, zone=zone, name=name,
                     phone=phone, assignees=",".join(assignees),
                     recording=recording_url, transcript=transcript_text)
            db.session.add(t); db.session.commit(); return
        except Exception as e:
            _jlog("task_db_fail", error=str(e))
    # fallback JSONL
    try:
        with open(TASKS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(task, ensure_ascii=False)+"\n")
    except Exception as e:
        _jlog("task_file_fail", error=str(e))

@app.get("/tasks/list")
def tasks_list():
    out=[]
    if db:
        try:
            rows = db.session.execute(db.select(Task).order_by(Task.created_at.desc())).scalars().all()
            for t in rows:
                out.append({
                    "id": t.id, "call_sid": t.call_sid, "role": t.role, "zone": t.zone,
                    "name": t.name, "phone": t.phone,
                    "assignees": t.assignees.split(",") if t.assignees else [],
                    "recording": t.recording, "transcript": t.transcript,
                    "status": t.status, "created_at": t.created_at.isoformat()+"Z"
                })
        except Exception as e:
            _jlog("task_list_db_fail", error=str(e))
    else:
        try:
            if os.path.exists(TASKS_FILE):
                with open(TASKS_FILE, "r", encoding="utf-8") as f:
                    for line in f:
                        out.append(json.loads(line))
        except Exception as e:
            _jlog("task_list_file_fail", error=str(e))
    return jsonify(out), 200

# ====== SMS helper (aviso a franquiciados) ======
def _send_sms(to_e164: str, body: str):
    sid = os.getenv("TWILIO_ACCOUNT_SID", ""); tok = os.getenv("TWILIO_AUTH_TOKEN", ""); frm = os.getenv("TWILIO_MESSAGING_FROM", "")
    if not (sid and tok and frm and to_e164):
        _jlog("sms_skip", reason="missing_sid_or_token_or_from_or_to"); return
    try:
        from twilio.rest import Client
        Client(sid, tok).messages.create(from_=frm, to=to_e164, body=body)
        _jlog("sms_ok", to=to_e164)
    except Exception as e:
        _jlog("sms_fail", error=str(e), to=to_e164)

# =========================================================
#  VOZ — FORMULARIO CONVERSACIONAL (rol→población→nombre→teléfono→nota)
# =========================================================
VOICE_PREFIX = "/voice"
TTS_VOICE = os.getenv("TTS_VOICE", "Polly.Conchita")
TWILIO_CALLER = os.getenv("TWILIO_VOICE_FROM", "+12252553716")

def _twiml(body: str) -> Response:
    body = body.strip()
    if not body.startswith("<Response"): body = f"<Response>{body}</Response>"
    return Response(body, mimetype="text/xml")

def _say_es_ssml(text: str) -> str:
    # Un único <Say> con <prosody> (sin cortes)
    return f'<Say language="es-ES" voice="{TTS_VOICE}"><prosody rate="medium" pitch="+2%">{text}</prosody></Say>'

def _line(*opts): return random.choice(opts)

def _gather_es(action: str, timeout="10", end_silence="auto",
               hints=("sí, si, no, propietario, inquilino, jaen, madrid, valencia, sevilla, "
                      "barcelona, malaga, granada, soy, me llamo, mi nombre es, "
                      "uno,dos,tres,cuatro,cinco,seis,siete,ocho,nueve,cero"),
               allow_dtmf: bool=False) -> str:
    mode = "speech dtmf" if allow_dtmf else "speech"
    return (f'<Gather input="{mode}" language="es-ES" timeout="{timeout}" '
            f'speechTimeout="{end_silence}" speechModel="phone_call" bargeIn="true" '
            f'action="{action}" method="POST" actionOnEmptyResult="true" hints="{hints}">')

# Estado por llamada
# step: ask_role → ask_city → ask_name → ask_phone → ask_note → confirm → done
_IVR_MEM = {}  # CallSid -> { step, role, zone, name, phone, note, transcript:[] }

PROVS = {"jaen":"Jaén","madrid":"Madrid","valencia":"Valencia","sevilla":"Sevilla",
         "barcelona":"Barcelona","malaga":"Málaga","granada":"Granada"}

FRAN_MAP = {
    "jaen":{"name":"Jaén","phones":["+34683634299"]},
    "madrid":{"name":"Madrid Centro","phones":["+34600000001"]},
    "valencia":{"name":"Valencia","phones":["+34600000003"]},
    "sevilla":{"name":"Sevilla","phones":["+34600000004"]},
    "barcelona":{"name":"Barcelona","phones":["+34600000005"]},
    "malaga":{"name":"Málaga","phones":["+34600000006"]},
    "granada":{"name":"Granada","phones":["+34600000007"]},
}

def _normalize(s: str) -> str:
    s = (s or "").lower()
    for a,b in (("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u")): s = s.replace(a,b)
    return s.strip()

def _parse_role(s: str) -> str:
    s = _normalize(s)
    if "propiet" in s or "duen" in s: return "propietario"
    if "inquil" in s or "alquil" in s or "habitacion" in s: return "inquilino"
    return ""

def _parse_city(s: str) -> str:
    s = _normalize(s)
    alias = {"barna":"barcelona","md":"madrid","vlc":"valencia","sevill":"sevilla"}
    for k,v in alias.items():
        if k in s: s = v
    for key in PROVS.keys():
        if key in s or s == key: return key
    return ""

def _parse_name(s: str) -> str:
    s = (s or "").strip().title()
    return s[:80]

def _parse_phone(s: str, digits: str) -> str:
    # Preferir DTMF si lo hay
    d = re.sub(r"\D","", digits or "")
    if len(d) >= 9: return "+34"+d if not d.startswith("34") and not d.startswith("+") else ("+"+d if not d.startswith("+") else d)
    # Intentar extraer de voz
    s = _normalize(s)
    # Mapear palabras a dígitos básicos
    repl = {"uno":"1","dos":"2","tres":"3","cuatro":"4","cinco":"5","seis":"6","siete":"7","ocho":"8","nueve":"9","cero":"0"}
    for k,v in repl.items(): s = s.replace(k, v)
    d = re.sub(r"\D","", s)
    if len(d) >= 9: return "+34"+d if not d.startswith("34") and not d.startswith("+") else ("+"+d if not d.startswith("+") else d)
    return ""

def _assign_targets(zone_key: str):
    data = FRAN_MAP.get(zone_key or "", {})
    return data.get("phones", [])

@app.get("/voice/health")
def voice_health():
    return jsonify(ok=True, service="voice"), 200

@app.route("/voice/answer", methods=["GET","POST"])
def voice_answer():
    call_id = unquote_plus(request.form.get("CallSid","") or request.args.get("CallSid","") or "")
    _IVR_MEM[call_id] = {"step":"ask_role","role":"","zone":"","name":"","phone":"","note":"",
                         "transcript":[]}
    texto = ("Hola, soy de SpainRoom. Empezamos: ¿eres propietario o inquilino y de qué población hablamos?")
    tw = "<Response>"+ _gather_es("/voice/next") + _say_es_ssml(texto) + "</Gather>" \
         + _say_es_ssml("No te escuché bien, vamos otra vez.") \
         + '<Redirect method="POST">/voice/answer</Redirect></Response>'
    return _twiml(tw)

@app.route("/voice/next", methods=["POST"])
def voice_next():
    call_id = unquote_plus(request.form.get("CallSid",""))
    speech  = unquote_plus(request.form.get("SpeechResult",""))
    digits  = request.form.get("Digits","")
    mem = _IVR_MEM.setdefault(call_id, {"step":"ask_role","role":"","zone":"","name":"","phone":"","note":"","transcript":[]})
    s = (speech or "").strip()
    if s: mem["transcript"].append(s)

    step = mem["step"]

    # ---- ASK ROLE ----
    if step == "ask_role":
        r = _parse_role(s)
        if not r:
            return _twiml("<Response>"+ _gather_es("/voice/next") +
                          _say_es_ssml("¿Eres propietario o inquilino?") + "</Gather></Response>")
        mem["role"] = r
        mem["step"] = "ask_city"

    # ---- ASK CITY ----
    if mem["step"] == "ask_city":
        c = _parse_city(s)
        if not c:
            pregunta = ("¿En qué población está el inmueble que quieres alquilar?"
                        if mem["role"]=="propietario"
                        else "¿En qué población quieres alquilar?")
            return _twiml("<Response>"+ _gather_es("/voice/next") +
                          _say_es_ssml(pregunta) + "</Gather></Response>")
        mem["zone"] = c
        mem["step"] = "ask_name"

    # ---- ASK NAME ----
    if mem["step"] == "ask_name":
        if not s:
            return _twiml("<Response>"+ _gather_es("/voice/next") +
                          _say_es_ssml("¿Cuál es tu nombre completo?") + "</Gather></Response>")
        mem["name"] = _parse_name(s)
        mem["step"] = "ask_phone"

    # ---- ASK PHONE ----
    if mem["step"] == "ask_phone":
        phone = _parse_phone(s, digits)
        if not phone:
            return _twiml("<Response>"+ _gather_es("/voice/next", allow_dtmf=True) +
                          _say_es_ssml("¿Cuál es un teléfono de contacto? Puedes decirlo o marcarlo en el teclado.") +
                          "</Gather></Response>")
        mem["phone"] = phone
        mem["step"] = "ask_note"

    # ---- ASK NOTE ----
    if mem["step"] == "ask_note":
        if not s:
            return _twiml("<Response>"+ _gather_es("/voice/next") +
                          _say_es_ssml("Cuéntame brevemente el motivo de la llamada.") +
                          "</Gather></Response>")
        mem["note"] = s
        mem["step"] = "confirm"

    # ---- CONFIRM ----
    if mem["step"] == "confirm":
        zona = PROVS.get(mem["zone"], mem["zone"].title() or "tu zona")
        rol  = "propietario" if mem["role"]=="propietario" else "inquilino"
        resumen = f"{mem['name']}, {rol} en {zona}, teléfono {mem['phone']}. ¿Está correcto?"
        return _twiml("<Response>"+ _gather_es("/voice/confirm-summary", allow_dtmf=True) +
                      _say_es_ssml(resumen) + "</Gather></Response>")

    # fallback
    return _twiml("<Response>"+ _gather_es("/voice/next") +
                  _say_es_ssml("Seguimos. ¿Me repites, por favor?") + "</Gather></Response>")

@app.post("/voice/confirm-summary")
def voice_confirm_summary():
    call_id = unquote_plus(request.form.get("CallSid",""))
    speech  = unquote_plus(request.form.get("SpeechResult",""))
    digits  = request.form.get("Digits","")
    yn = "yes" if (digits=="1" or re.search(r"\b(si|sí|vale|correcto|claro|ok)\b", (speech or "").lower())) else \
         ("no" if (digits=="2" or re.search(r"\bno\b", (speech or "").lower())) else "")

    mem = _IVR_MEM.get(call_id, None)
    if not mem:  # volver al inicio
        return _twiml('<Response><Redirect method="POST">/voice/answer</Redirect></Response>')

    if yn == "no":
        # Preguntar qué corregimos (simple: volver a población → nombre → teléfono)
        mem["step"] = "ask_city"
        pregunta = ("Vamos a corregirlo. ¿En qué población está el inmueble?"
                    if mem["role"]=="propietario"
                    else "Vamos a corregirlo. ¿En qué población quieres alquilar?")
        return _twiml("<Response>"+ _gather_es("/voice/next") +
                      _say_es_ssml(pregunta) + "</Gather></Response>")

    if yn != "yes":
        return _twiml("<Response>"+ _gather_es("/voice/confirm-summary", allow_dtmf=True) +
                      _say_es_ssml("¿Me confirmas, por favor? Di sí o no, o pulsa 1 o 2.") +
                      "</Gather></Response>")

    # Confirmado → crear tarea y despedir
    zona_lbl = PROVS.get(mem["zone"], mem["zone"].title() or "tu zona")
    assignees = _assign_targets(mem["zone"])
    transcript_text = " | ".join([t for t in mem.get("transcript",[]) if t])

    _save_task(call_id=call_id, role=mem["role"], zone=mem["zone"], name=mem["name"],
               phone=mem["phone"], assignees=assignees, recording_url="", transcript_text=transcript_text)

    # SMS resumen
    resumen = (f"SpainRoom: {mem['role']} en {zona_lbl}. "
               f"Nombre: {mem['name'] or 'N/D'}. Tel: {mem['phone'] or 'N/D'}. Nota: {mem['note'] or 'N/D'}")
    for to in assignees:
        _send_sms(to, resumen)

    gracias = "Perfecto, ya tengo todo. La persona de tu zona te llamará en breve. ¡Gracias!"
    del _IVR_MEM[call_id]  # cerrar memoria de la llamada
    return _twiml("<Response>"+ _say_es_ssml(gracias) + "<Hangup/></Response>")

# Root y fallback
@app.route("/", methods=["GET","POST"])
def root_safe():
    if request.method == "POST":
        return _twiml(_say_es_ssml("Hola, te atiendo ahora mismo.")
                      + '<Redirect method="POST">/voice/answer</Redirect>')
    return ("", 404)

@app.route("/voice/fallback", methods=["GET","POST"])
def voice_fallback():
    return _twiml(_say_es_ssml("Un segundo, por favor.")
                  + '<Redirect method="POST">/voice/answer</Redirect>')

# Diagnóstico
@app.get("/__routes")
def __routes():
    return {"routes":[f"{r.endpoint} -> {r.rule}" for r in app.url_map.iter_rules()]}, 200

# MAIN local
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
