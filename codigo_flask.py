# codigo_flask.py
# SpainRoom · Backend principal (Render: gunicorn codigo_flask:app)
# - Defensa (WAF) embebida y ACTIVA (no inspecciona /voice, /health, /__routes)
# - IVR /voice/*: formulario por turnos (rol→población→nombre→teléfono→nota→confirmación)
# - Voz natural: un <Say> por turno (SSML <prosody> + micro-pausas), barge-in, re-prompts amables
# - Tareas con transcript + SMS a franquiciados (multi-destino)
# - Territorios (España): microzonas (bbox) y rejilla nacional; endpoints /territories/*
# - Admin: UI de tareas /admin/tasks y actualización /tasks/update
# - Geocoder (Nominatim) y utils de ejemplo

from flask import Flask, request, jsonify, Response, Response as _FlaskResponse
import requests, json, os, re, random, tempfile, shutil
from math import radians, sin, cos, sqrt, atan2, floor
from urllib.parse import unquote_plus
from datetime import datetime, timezone

app = Flask(__name__)

# ==============================
# Config / Entorno
# ==============================
CENTRAL_PHONE = os.getenv("CENTRAL_PHONE", "+12252553716")       # fallback si no hay franquiciado
SMS_FROM      = os.getenv("TWILIO_MESSAGING_FROM", "+12252553716")
VOICE_FROM    = os.getenv("TWILIO_VOICE_FROM", "+12252553716")
TTS_VOICE     = os.getenv("TTS_VOICE", "Polly.Conchita")         # Twilio-Polly compatible
TERR_FILE     = os.getenv("TERR_FILE", "/tmp/spainroom_territories.json")
TERR_TOKEN    = os.getenv("TERR_TOKEN", "")                      # token admin para /territories/*
GRID_SIZE_DEG = float(os.getenv("GRID_SIZE_DEG", "0.05"))        # ~0.05º ≈ 5–6 km

# ==============================
# DEFENSA (WAF) embebida
# ==============================
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
_SQLI = [r"(?i)\bunion\b.+\bselect\b", r"(?i)\b(select|insert|update|delete)\b.+\bfrom\b",
         r"(?i)\bor\s+1=1\b", r"(?i)\bsleep\(", r"(?i)information_schema", r"(?i)load_file\("]
_XSS  = [r"(?i)<script\b", r"(?i)javascript:", r"(?i)onerror\s*="]
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
    payload.update(kw); print(json.dumps(payload, ensure_ascii=False), flush=True)

def _skip_waf():
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
    if _skip_waf(): return
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

# ==============================
# Salud / Demos utilidad
# ==============================
@app.get("/health")
def health(): return jsonify(ok=True, service="BACKEND-SPAINROOM"), 200

def calcular_distancia(lat1, lon1, lat2, lon2):
    R=6371; dlat=radians(lat2-lat1); dlon=radians(lon2-lon1)
    a=sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return 2*atan2(sqrt(a), sqrt(1-a))*R

@app.get("/api/geocode")
def geocode():
    address = request.args.get("address")
    if not address: return jsonify({"error":"Falta parámetro address"}), 400
    url=f"https://nominatim.openstreetmap.org/search?q={address}&format=json&limit=1"
    r=requests.get(url,headers={"User-Agent":"SpainRoom/1.0"},timeout=10)
    if r.status_code!=200 or not r.json(): return jsonify({"error":"No se pudo geocodificar"}), 500
    d=r.json()[0]; return jsonify({"lat":float(d["lat"]), "lng":float(d["lon"]) })

# ==============================
# Utilidades de VOZ (SSML natural)
# ==============================
def _ssml(text: str) -> str:
    """[[b300]] -> <break time="300ms">, [[digits:600123123]] -> say-as digits"""
    import html as _html
    s=text
    s=re.sub(r"\[\[b(\d{2,4})\]\]", lambda m:f'<break time="{m.group(1)}ms"/>', s)
    s=re.sub(r"\[\[digits:([\d\s\+]+)\]\]", lambda m:f'<say-as interpret-as="digits">{m.group(1)}</say-as>', s)
    return f'<speak><prosody rate="medium" pitch="+2%">{_html.escape(s, quote=False)}</prosody></speak>'

def _say_es_ssml(text: str) -> str:
    return f'<Say language="es-ES" voice="{TTS_VOICE}">{_ssml(text)}</Say>'

def _line(*opts): return random.choice([o for o in opts if o])

def _gather_es(action: str, timeout="10", end_silence="auto",
               hints=("sí, si, no, propietario, inquilino, jaen, madrid, valencia, sevilla, "
                      "barcelona, malaga, granada, soy, me llamo, mi nombre es, "
                      "uno,dos,tres,cuatro,cinco,seis,siete,ocho,nueve,cero"),
               allow_dtmf: bool=False) -> str:
    mode="speech dtmf" if allow_dtmf else "speech"
    return (f'<Gather input="{mode}" language="es-ES" timeout="{timeout}" '
            f'speechTimeout="{end_silence}" speechModel="phone_call" bargeIn="true" '
            f'action="{action}" method="POST" actionOnEmptyResult="true" hints="{hints}">')

def _twiml(body: str) -> Response:
    """Devuelve TwiML válido text/xml (EVITA 500 y el 'goodbye')."""
    b = body.strip()
    if not b.startswith("<Response"): b = f"<Response>{b}</Response>"
    return Response(b, mimetype="text/xml")

# ==============================
# TAREAS (DB opcional + JSONL)
# ==============================
TASKS_FILE="/tmp/spainroom_tasks.jsonl"
db=None
try:
    from flask_sqlalchemy import SQLAlchemy
    db_url=os.getenv("SQLALCHEMY_DATABASE_URI") or os.getenv("DATABASE_URL")
    if db_url:
        app.config["SQLALCHEMY_DATABASE_URI"]=db_url
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"]=False
        db=SQLAlchemy(app)
        class Task(db.Model):
            __tablename__="sr_tasks"
            id=db.Column(db.Integer,primary_key=True)
            call_sid=db.Column(db.String(64),index=True)
            role=db.Column(db.String(32)); zone=db.Column(db.String(64))
            name=db.Column(db.String(128)); phone=db.Column(db.String(32))
            assignees=db.Column(db.String(512))  # CSV phones
            recording=db.Column(db.String(512)); transcript=db.Column(db.Text)
            status=db.Column(db.String(32),default="pending")
            created_at=db.Column(db.DateTime,default=datetime.utcnow)
        with app.app_context(): db.create_all()
except Exception as e:
    _jlog("db_init_skip", error=str(e))

def _save_task(call_sid, role, zone, name, phone, assignees, recording_url, transcript_text):
    task={"created_at":datetime.utcnow().isoformat()+"Z","call_sid":call_sid,"role":role,"zone":zone,
          "name":name,"phone":phone,"assignees":assignees,"recording":recording_url,
          "transcript":transcript_text,"status":"pending"}
    if db:
        try:
            t=Task(call_sid=call_sid, role=role, zone=zone, name=name, phone=phone,
                   assignees=",".join(assignees), recording=recording_url, transcript=transcript_text)
            db.session.add(t); db.session.commit(); return
        except Exception as e: _jlog("task_db_fail", error=str(e))
    try:
        with open(TASKS_FILE,"a",encoding="utf-8") as f: f.write(json.dumps(task,ensure_ascii=False)+"\n")
    except Exception as e: _jlog("task_file_fail", error=str(e))

@app.get("/tasks/list")
def tasks_list():
    out=[]
    if db:
        try:
            rows=db.session.execute(db.select(Task).order_by(Task.created_at.desc())).scalars().all()
            for t in rows:
                out.append({"id":t.id,"call_sid":t.call_sid,"role":t.role,"zone":t.zone,
                            "name":t.name,"phone":t.phone,"assignees":t.assignees.split(",") if t.assignees else [],
                            "recording":t.recording,"transcript":t.transcript,"status":t.status,
                            "created_at":t.created_at.isoformat()+"Z"})
        except Exception as e: _jlog("task_list_db_fail", error=str(e))
    else:
        try:
            if os.path.exists(TASKS_FILE):
                with open(TASKS_FILE,"r",encoding="utf-8") as f:
                    for line in f: out.append(json.loads(line))
        except Exception as e: _jlog("task_list_file_fail", error=str(e))
    return jsonify(out),200

@app.post("/tasks/update")
def tasks_update():
    try:
        payload=request.get_json(force=True) or {}
        tid=payload.get("id"); call_sid=payload.get("call_sid")
        status=(payload.get("status") or "pending").strip(); notes=(payload.get("notes") or "").strip()
        # DB
        if db:
            row=None
            if tid: row=db.session.get(Task, tid)
            elif call_sid:
                row=db.session.execute(db.select(Task).where(Task.call_sid==call_sid).order_by(Task.created_at.desc())).scalars().first()
            if not row: return jsonify(ok=False,error="task_not_found"),404
            row.status=status
            if notes:
                row.transcript=(row.transcript or "")
                row.transcript=(row.transcript + (" | " if row.transcript else "") + f"NOTA: {notes}")[:4000]
            db.session.commit(); return jsonify(ok=True),200
        # JSONL
        if not call_sid: return jsonify(ok=False,error="call_sid_required"),400
        if not os.path.exists(TASKS_FILE): return jsonify(ok=False,error="tasks_file_not_found"),404
        tasks=[]; last_idx=-1
        with open(TASKS_FILE,"r",encoding="utf-8") as f:
            for i,line in enumerate(f):
                try:
                    obj=json.loads(line); tasks.append(obj)
                    if obj.get("call_sid")==call_sid: last_idx=i
                except: pass
        if last_idx==-1: return jsonify(ok=False,error="task_not_found"),404
        tasks[last_idx]["status"]=status
        if notes:
            oldt=tasks[last_idx].get("transcript","")
            tasks[last_idx]["transcript"]=(oldt + (" | " if oldt else "") + f"NOTA: {notes}")[:4000]
        with tempfile.NamedTemporaryFile("w",delete=False,encoding="utf-8") as tmp:
            for obj in tasks: tmp.write(json.dumps(obj,ensure_ascii=False)+"\n")
            tmp_path=tmp.name
        shutil.move(tmp_path, TASKS_FILE); return jsonify(ok=True),200
    except Exception as e:
        return jsonify(ok=False,error=str(e)),500

# ==============================
# SMS helper
# ==============================
def _send_sms(to_e164: str, body: str):
    sid=os.getenv("TWILIO_ACCOUNT_SID",""); tok=os.getenv("TWILIO_AUTH_TOKEN","")
    if not (sid and tok and SMS_FROM and to_e164): _jlog("sms_skip",reason="missing_sid_or_token_or_from_or_to"); return
    try:
        from twilio.rest import Client
        Client(sid,tok).messages.create(from_=SMS_FROM, to=to_e164, body=body); _jlog("sms_ok",to=to_e164)
    except Exception as e: _jlog("sms_fail", error=str(e), to=to_e164)

# ==============================
# TERRITORIOS (España): microzonas y rejilla
# ==============================
PROVS = {"jaen":"Jaén","madrid":"Madrid","valencia":"Valencia","sevilla":"Sevilla",
         "barcelona":"Barcelona","malaga":"Málaga","granada":"Granada"}

def _slug_city(s:str)->str:
    s=(s or "").lower().strip()
    repl={"á":"a","é":"e","í":"i","ó":"o","ú":"u","ü":"u","ñ":"n"}
    for k,v in repl.items(): s=s.replace(k,v)
    return re.sub(r"[^a-z0-9]+","-",s).strip("-")
