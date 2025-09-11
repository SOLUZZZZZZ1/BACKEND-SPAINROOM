# codigo_flask.py
# SpainRoom · Backend principal (Render: gunicorn codigo_flask:app)
# - Defensa (WAF) embebida y ACTIVA (no inspecciona /voice, /health, /__routes)
# - IVR /voice/*: formulario por turnos (rol→población→nombre→teléfono→nota→confirmación)
# - Voz natural: un <Say> por turno (SSML <prosody> + micro-pausas), barge-in, re-prompts amables
# - Tareas con transcript + SMS a franquiciados (multi-destino) y UI /admin/tasks
# - Territorios (España): microzonas (bbox) + rejilla nacional; endpoints /territories/*
# - Geocoder (Nominatim) y utils

from flask import Flask, request, jsonify, Response, Response as _FlaskResponse
from flask import has_request_context
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
# Helpers base
# ==============================
def _twiml(body: str) -> Response:
    """Devuelve TwiML válido text/xml (EVITA 500 y el 'goodbye')."""
    b = (body or "").strip()
    if not b.startswith("<Response"): b = f"<Response>{b}</Response>"
    return Response(b, mimetype="text/xml")

def _now_iso():  # útil para logs externos
    return datetime.now(tz=timezone.utc).isoformat()

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
    if has_request_context():
        if DEF_CFG["TRUST_PROXY"]:
            xf = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            return xf or (request.remote_addr or "")
        return request.remote_addr or ""
    return "-"  # fuera de request

def _jlog(event, **kw):
    payload = {
        "ts": _now_iso(),
        "event": event,
        "ip": _ip(),
        "path": request.path if has_request_context() else "-",
        "method": request.method if has_request_context() else "-",
        "rid": (request.headers.get("X-Request-ID", "") if has_request_context() else "")
    }
    payload.update(kw)
    try:
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    except Exception:
        print(str(payload), flush=True)

def _skip_waf():
    if not has_request_context():  # jamás bloquear fuera de request
        return True
    p = request.path or ""
    for pref in DEF_CFG["SKIP_PREFIXES"]:
        if p.startswith(pref): return True
    return False

def _host_ok():
    if not DEF_CFG["STRICT_HOSTS"] or not has_request_context(): return None
    host = (request.headers.get("Host") or "").split(":")[0].lower().strip()
    return None if host in DEF_CFG["STRICT_HOSTS"] else f"host_not_allowed:{host}"

def _ua_ok():
    if not has_request_context(): return None
    ua = (request.headers.get("User-Agent") or "").lower()
    for bad in DEF_CFG["BLOCKED_UA"]:
        if bad and bad in ua: return f"ua_blocked:{bad}"
    return None

def _size_ok():
    if not has_request_context(): return None
    cl = request.content_length or 0
    return None if not cl or cl <= DEF_CFG["MAX_BODY"] else f"body_too_large:{cl}"

def _ct_ok():
    if not has_request_context(): return None
    if request.method in ("GET","HEAD","OPTIONS"): return None
    ct = (request.headers.get("Content-Type","") or "").split(";")[0].strip().lower()
    return None if (not ct or ct in DEF_CFG["ALLOW_CT"]) else f"bad_content_type:{ct}"

def _badh_ok():
    if not has_request_context(): return None
    for h in _BADH:
        if h in request.headers: return f"bad_header:{h}"
    return None

def _hit(pats, text):
    if not text: return None
    for p in pats:
        if re.search(p, text): return p
    return None

def _qs_ok():
    if not has_request_context(): return None
    qs = request.query_string.decode("utf-8","ignore")
    if _hit(_SQLI, qs): return "sqli_qs"
    if _hit(_XSS, qs):  return "xss_qs"
    return None

def _trav_ok():
    if not has_request_context(): return None
    return "traversal_path" if _hit(_TRAV, (request.path or "").lower()) else None

def _body_ok():
    if not has_request_context(): return None
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
# TERRITORIOS (España): microzonas y rejilla
# ==============================
PROVS = {"jaen":"Jaén","madrid":"Madrid","valencia":"Valencia","sevilla":"Sevilla",
         "barcelona":"Barcelona","malaga":"Málaga","granada":"Granada"}

def _slug_city(s:str)->str:
    s=(s or "").lower().strip()
    repl={"á":"a","é":"e","í":"i","ó":"o","ú":"u","ü":"u","ñ":"n"}
    for k,v in repl.items(): s=s.replace(k,v)
    return re.sub(r"[^a-z0-9]+","-",s).strip("-")

def _tile_key(lat:float,lng:float,size:float=GRID_SIZE_DEG)->str:
    lat_i=floor(lat/size); lng_i=floor(lng/size)
    return f"tile_{size:.3f}_{lat_i}_{lng_i}"

def _load_territories():
    if os.path.exists(TERR_FILE):
        try:
            with open(TERR_FILE,"r",encoding="utf-8") as f: return json.load(f)
        except: pass
    return {"tiles":{}, "microzones":[]}  # tiles: key->{label,phones[]}; microzones:[{city, name, bbox:[minlat,minlng,maxlat,maxlng], phones[]}]

def _save_territories(obj):
    try:
        with open(TERR_FILE,"w",encoding="utf-8") as f: json.dump(obj,f,ensure_ascii=False,indent=2)
    except Exception as e: _jlog("territories_save_err", error=str(e))

TERR=_load_territories()

def _point_in_bbox(lat,lng,b):
    return (b[0] <= lat <= b[2]) and (b[1] <= lng <= b[3])

def _geocode_city(city:str):
    url=f"https://nominatim.openstreetmap.org/search?q={city}, España&format=json&limit=1"
    r=requests.get(url,headers={"User-Agent":"SpainRoom/1.0"},timeout=10)
    if r.status_code==200 and r.json():
        d=r.json()[0]; return float(d["lat"]), float(d["lon"])
    return None,None

def _find_assignees_by_latlng(lat,lng,city_slug):
    # 1) microzonas por ciudad
    for mz in TERR.get("microzones",[]):
        if mz.get("city")==city_slug and _point_in_bbox(lat,lng,mz.get("bbox",[0,0,0,0])):
            return (mz.get("phones") or []), f"{mz.get('city')}::{mz.get('name')}"
    # 2) tile por rejilla
    key=_tile_key(lat,lng)
    if key in TERR.get("tiles",{}):
        t=TERR["tiles"][key]; return (t.get("phones") or []), key
    # 3) sin dueño -> central
    return [], "unassigned"

@app.get("/territories/list")
def terr_list(): return jsonify(TERR),200

@app.get("/territories/lookup")
def terr_lookup():
    # ?city=Jaen o ?lat=..&lng=..
    city=request.args.get("city","").strip()
    lat=request.args.get("lat"); lng=request.args.get("lng")
    if city and not (lat and lng):
        lt,lg=_geocode_city(city); 
        if lt is None: return jsonify(ok=False,error="geocode_failed"),400
        lat,lng=lt,lg
    try:
        lat=float(lat); lng=float(lng)
    except: return jsonify(ok=False,error="lat_lng_required"),400
    phones, owner=_find_assignees_by_latlng(lat,lng,_slug_city(city) if city else "")
    return jsonify(ok=True, lat=lat, lng=lng, owner=owner, phones=phones),200

def _auth_terr():
    if not TERR_TOKEN: return True
    return request.headers.get("X-Admin-Token","")==TERR_TOKEN

@app.post("/territories/claim")
def terr_claim():
    if not _auth_terr(): return jsonify(ok=False,error="unauthorized"),403
    payload=request.get_json(force=True) or {}
    mode=payload.get("mode")  # "tile" | "bbox"
    label=payload.get("label",""); phones=payload.get("phones") or []
    if mode=="tile":
        lat=float(payload.get("lat")); lng=float(payload.get("lng"))
        size=float(payload.get("size", GRID_SIZE_DEG))
        key=_tile_key(lat,lng,size); TERR["tiles"][key]={"label":label,"phones":phones}
        _save_territories(TERR); return jsonify(ok=True,key=key),200
    elif mode=="bbox":
        city=_slug_city(payload.get("city","")); bbox=payload.get("bbox")  # [minlat,minlng,maxlat,maxlng]
        if not (city and isinstance(bbox,list) and len(bbox)==4): return jsonify(ok=False,error="invalid_bbox"),400
        TERR["microzones"].append({"city":city,"name":label,"bbox":bbox,"phones":phones})
        _save_territories(TERR); return jsonify(ok=True),200
    return jsonify(ok=False,error="mode_required"),400

@app.post("/territories/unclaim")
def terr_unclaim():
    if not _auth_terr(): return jsonify(ok=False,error="unauthorized"),403
    payload=request.get_json(force=True) or {}
    mode=payload.get("mode")
    if mode=="tile":
        key=payload.get("key","")
        if key in TERR.get("tiles",{}): TERR["tiles"].pop(key,None); _save_territories(TERR); return jsonify(ok=True),200
        return jsonify(ok=False,error="not_found"),404
    elif mode=="bbox":
        city=_slug_city(payload.get("city","")); name=payload.get("label","")
        arr=TERR.get("microzones",[]); n=len(arr)
        TERR["microzones"]=[mz for mz in arr if not (mz.get("city")==city and mz.get("name")==name)]
        if len(TERR["microzones"])<n: _save_territories(TERR); return jsonify(ok=True),200
        return jsonify(ok=False,error="not_found"),404
    return jsonify(ok=False,error="mode_required"),400

# ==============================
# VOZ: formulario por turnos
# ==============================
def smalltalk_kind(s: str):
    s=(s or '').lower()
    if any(p in s for p in ['hola','buenas','qué tal','que tal']): return 'greeting'
    if any(p in s for p in ['hace buen dia','buen día','tiempo','clima']): return 'weather'
    if any(p in s for p in ['hora es','qué hora']): return 'time'
    if any(p in s for p in ['gracias','muchas gracias']): return 'thanks'
    return ''

def _normalize(s:str)->str:
    s=(s or "").lower().strip()
    for a,b in (("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ü","u"),("ñ","n")): s=s.replace(a,b)
    return s

def _parse_role(s:str)->str:
    s=_normalize(s)
    if "propiet" in s or "duen" in s: return "propietario"
    if "inquil" in s or "alquil" in s or "habitacion" in s: return "inquilino"
    return ""

def _parse_city(s:str)->str:
    s=_normalize(s)
    alias={"barna":"barcelona","md":"madrid","vlc":"valencia","sevill":"sevilla"}
    for k,v in alias.items():
        if k in s: s=v
    for key in PROVS.keys():
        if key in s or s==key: return key
    return _slug_city(s) if s else ""

def _parse_name(s:str)->str: return (s or "").strip().title()[:80]

def _parse_phone(s:str, digits:str)->str:
    d=re.sub(r"\D","", digits or "")
    if len(d)>=9: return "+34"+d if not d.startswith(("34","+")) else ("+"+d if not d.startswith("+") else d)
    s=_normalize(s); repl={"uno":"1","dos":"2","tres":"3","cuatro":"4","cinco":"5","seis":"6","siete":"7","ocho":"8","nueve":"9","cero":"0"}
    for k,v in repl.items(): s=s.replace(k,v)
    d=re.sub(r"\D","", s)
    if len(d)>=9: return "+34"+d if not d.startswith(("34","+")) else ("+"+d if not d.startswith("+") else d)
    return ""

# Estado por llamada
# step: ask_role → ask_city → ask_name → ask_phone → ask_note → confirm → done
_IVR_MEM = {}  # CallSid -> { step, role, zone, name, phone, note, transcript:[], miss }

@app.get("/voice/health")
def voice_health(): return jsonify(ok=True, service="voice"), 200

@app.route("/voice/answer", methods=["GET","POST"])
def voice_answer():
    call_id = unquote_plus(request.form.get("CallSid","") or request.args.get("CallSid","") or "")
    _IVR_MEM[call_id]={"step":"ask_role","role":"","zone":"","name":"","phone":"","note":"","miss":0,"transcript":[]}
    texto=_line(
        "Hola [[b200]] soy de SpainRoom. ¿Eres propietario o inquilino [[b200]] y de qué población hablamos?",
        "¡Hola! [[b150]] Cuéntame en una frase [[b150]] si eres propietario o inquilino [[b150]] y la población."
    )
    tw="<Response>"+ _gather_es("/voice/next") + _say_es_ssml(texto) + "</Gather>" \
       + _say_es_ssml("No te escuché bien [[b200]] vamos otra vez.") \
       + '<Redirect method="POST">/voice/answer</Redirect></Response>'
    return _twiml(tw)

@app.route("/voice/next", methods=["POST"])
def voice_next():
    call_id=unquote_plus(request.form.get("CallSid",""))
    speech =unquote_plus(request.form.get("SpeechResult","")); digits=request.form.get("Digits","")
    mem=_IVR_MEM.setdefault(call_id,{"step":"ask_role","role":"","zone":"","name":"","phone":"","note":"","miss":0,"transcript":[]})
    s=(speech or "").strip()
    if s: mem["transcript"].append(s)
    step=mem["step"]

    # small-talk
    kind=smalltalk_kind(s)
    if kind=='greeting':
        return _twiml("<Response>"+ _gather_es("/voice/next") +
                      _say_es_ssml("Hola [[b150]] encantada. ¿Eres propietario o inquilino [[b150]] y de qué población?") +
                      "</Gather></Response>")
    if kind=='weather':
        return _twiml("<Response>"+ _gather_es("/voice/next") +
                      _say_es_ssml("¡Qué bien! [[b150]] Dime [[b120]] ¿eres propietario o inquilino y en qué población?") +
                      "</Gather></Response>")
    if kind=='time':
        return _twiml("<Response>"+ _gather_es("/voice/next") +
                      _say_es_ssml("Estoy aquí ahora mismo para ayudarte [[b150]] ¿propietario o inquilino [[b120]] y de qué población?") +
                      "</Gather></Response>")
    if kind=='thanks':
        return _twiml("<Response>"+ _gather_es("/voice/next") +
                      _say_es_ssml("¡A ti! [[b120]] Seguimos [[b100]] ¿propietario o inquilino y de qué población?") +
                      "</Gather></Response>")

    # rol
    if step=="ask_role":
        r=_parse_role(s)
        if not r:
            mem["miss"]+=1
            prompt="¿Eres propietario o inquilino?" if mem["miss"]<2 else "Solo dime [[b120]] propietario [[b120]] o inquilino."
            return _twiml("<Response>"+ _gather_es("/voice/next")+ _say_es_ssml(prompt)+"</Gather></Response>")
        mem["role"]=r; mem["step"]="ask_city"; mem["miss"]=0

    # ciudad / población (según rol)
    if mem["step"]=="ask_city":
        c=_parse_city(s)
        if not c or c in ("", "de", "la", "el"):
            mem["miss"]+=1
            pregunta = ("¿En qué población está el inmueble?" if mem["role"]=="propietario"
                        else "¿En qué población quieres alquilar?")
            if mem["miss"]>=2:
                pregunta = "Solo la población [[b120]] por ejemplo Jaén o Madrid."
            return _twiml("<Response>"+ _gather_es("/voice/next")+ _say_es_ssml(pregunta)+"</Gather></Response>")
        mem["zone"]=c; mem["step"]="ask_name"; mem["miss"]=0

    # nombre
    if mem["step"]=="ask_name":
        if not s:
            mem["miss"]+=1
            return _twiml("<Response>"+ _gather_es("/voice/next")+ _say_es_ssml("¿Cuál es tu nombre completo?")+" </Gather></Response>")
        mem["name"]=_parse_name(s); mem["step"]="ask_phone"; mem["miss"]=0

    # teléfono
    if mem["step"]=="ask_phone":
        phone=_parse_phone(s, digits)
        if not phone:
            mem["miss"]+=1
            txt="¿Cuál es un teléfono de contacto? [[b180]] Puedes decirlo o marcarlo en el teclado."
            if mem["miss"]>=2: txt="Marca ahora tu número [[b150]] o díctalo despacio."
            return _twiml("<Response>"+ _gather_es("/voice/next", allow_dtmf=True)+ _say_es_ssml(txt)+"</Gather></Response>")
        mem["phone"]=phone; mem["step"]="ask_note"; mem["miss"]=0

    # nota breve
    if mem["step"]=="ask_note":
        if not s:
            mem["miss"]+=1
            return _twiml("<Response>"+ _gather_es("/voice/next")+ _say_es_ssml("Cuéntame brevemente el motivo de la llamada.")+"</Gather></Response>")
        mem["note"]=s; mem["step"]="confirm"; mem["miss"]=0

    # confirmación
    if mem["step"]=="confirm":
        zona = PROVS.get(mem["zone"], mem["zone"].title() or "tu zona")
        phone_digits = re.sub(r"\D","", mem["phone"] or "")
        phone_ssml = f"[[digits:{phone_digits}]]" if phone_digits else (mem["phone"] or "no consta")
        resumen = f"{mem['name'] or 'sin nombre'}, {_line('vale','perfecto','de acuerdo')} [[b120]] " \
                  f"{'propietario' if mem['role']=='propietario' else 'inquilino'} en {zona}. Teléfono {phone_ssml}. " \
                  f"¿Está correcto?"
        return _twiml("<Response>"+ _gather_es("/voice/confirm-summary", allow_dtmf=True)+ _say_es_ssml(resumen)+"</Gather></Response>")

    # fallback
    return _twiml("<Response>"+ _gather_es("/voice/next")+ _say_es_ssml("Seguimos [[b120]] ¿me repites, por favor?")+"</Gather></Response>")

@app.post("/voice/confirm-summary")
def voice_confirm_summary():
    call_id=unquote_plus(request.form.get("CallSid",""))
    speech =unquote_plus(request.form.get("SpeechResult","")); digits=request.form.get("Digits","")
    yn="yes" if (digits=="1" or re.search(r"\b(si|sí|vale|correcto|claro|ok)\b",(speech or "").lower())) else \
       ("no"  if (digits=="2" or re.search(r"\bno\b",(speech or "").lower())) else "")
    mem=_IVR_MEM.get(call_id)
    if not mem:
        return _twiml('<Response><Redirect method="POST">/voice/answer</Redirect></Response>')
    if yn=="no":
        mem["step"]="ask_city"; mem["miss"]=0
        pregunta=("Vamos a corregirlo [[b120]] ¿en qué población está el inmueble?"
                  if mem["role"]=="propietario" else
                  "Vamos a corregirlo [[b120]] ¿en qué población quieres alquilar?")
        return _twiml("<Response>"+ _gather_es("/voice/next")+ _say_es_ssml(pregunta)+"</Gather></Response>")
    if yn!="yes":
        return _twiml("<Response>"+ _gather_es("/voice/confirm-summary", allow_dtmf=True)+
                      _say_es_ssml("¿Me confirmas [[b120]] por favor? Di sí o no [[b120]] o pulsa 1 o 2.")
                      +"</Gather></Response>")

    # Confirmado → asignación de franquiciado por territorio
    zona_lbl = PROVS.get(mem["zone"], mem["zone"].title() or "tu zona")
    # geocodificar población/ciudad a lat/lng
    lat=lng=None
    city_query = mem["zone"]
    if city_query and city_query not in PROVS:  # ciudad
        lt,lg=_geocode_city(city_query); lat,lng=lt,lg
    if (lat is None or lng is None) and city_query in PROVS:
        lt,lg=_geocode_city(PROVS[city_query]); lat,lng=lt,lg
    phones, owner = ([], "unassigned")
    if (lat is not None) and (lng is not None):
        phones, owner = _find_assignees_by_latlng(lat,lng,_slug_city(city_query))
    assignees = phones if phones else [CENTRAL_PHONE]

    transcript_text = " | ".join([t for t in mem.get("transcript",[]) if t])
    _save_task(call_id=call_id, role=mem["role"], zone=mem["zone"], name=mem["name"],
               phone=mem["phone"], assignees=assignees, recording_url="", transcript_text=transcript_text)

    resumen = (f"SpainRoom: {mem['role']} en {zona_lbl}. Nombre: {mem['name'] or 'N/D'}. "
               f"Tel: {mem['phone'] or 'N/D'}. Nota: {mem['note'] or 'N/D'}. Destino: {owner}")
    for to in assignees: _send_sms(to, resumen)

    gracias=_line("Perfecto, ya tengo todo [[b150]] la persona de tu zona te llamará en breve. ¡Gracias!",
                  "Gracias [[b150]] te llamarán desde tu zona en breve.")
    del _IVR_MEM[call_id]
    return _twiml("<Response>"+ _say_es_ssml(gracias) + "<Hangup/></Response>")

# Root y fallback
@app.route("/", methods=["GET","POST"])
def root_safe():
    if request.method=="POST":
        return _twiml(_say_es_ssml("Hola, te atiendo ahora mismo.") + '<Redirect method="POST">/voice/answer</Redirect>')
    return ("",404)

@app.route("/voice/fallback", methods=["GET","POST"])
def voice_fallback():
    return _twiml(_say_es_ssml("Un segundo, por favor.") + '<Redirect method="POST">/voice/answer</Redirect>')

# ==============================
# UI de tareas (admin)
# ==============================
@app.get("/admin/tasks")
def admin_tasks_page():
    html = """<!doctype html><html lang="es"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>SpainRoom · Tareas</title>
<style>:root{--card:#121923;--bg:#0c141d;--muted:#9fb0c3;--txt:#e7eef7;--btn:#2563eb}
body{margin:0;font-family:system-ui,Segoe UI,Roboto,Arial;background:#0b1118;color:var(--txt)}
header{padding:16px 18px;background:#0b1118;position:sticky;top:0;border-bottom:1px solid #1b2a3a}
.wrap{max-width:1120px;margin:20px auto;padding:0 14px}
.card{background:var(--card);border:1px solid #1b2a3a;border-radius:12px;padding:16px}
.row{display:flex;gap:12px;flex-wrap:wrap}
table{width:100%;border-collapse:collapse;margin-top:10px}
th,td{padding:9px;border-bottom:1px solid #1b2a3a;font-size:14px}
th{color:var(--muted);text-align:left}
tr:hover{background:#0e1722}
button,select,input,textarea{background:#0e1620;border:1px solid #1b2a3a;color:var(--txt);border-radius:10px;padding:10px}
button{background:var(--btn);border:0;cursor:pointer}
.pill{padding:4px 8px;border-radius:18px;font-size:12px}
.pending{background:#1f2a37}.done{background:#0b3b2f}
.toast{position:fixed;right:14px;bottom:14px;background:#0e1620;border:1px solid #1b2a3a;border-radius:10px;padding:10px 14px}
</style></head><body>
<header><h2 style="margin:0">SpainRoom · Tareas</h2></header>
<div class="wrap"><div class="card">
  <div class="row" style="align-items:end">
    <div><label>Zona</label><br/><select id="fZone"><option value="">(todas)</option></select></div>
    <div><label>Estado</label><br/><select id="fStatus"><option value="">(todos)</option><option>pending</option><option>done</option></select></div>
    <div style="margin-left:auto"><button id="refresh">Actualizar</button></div>
  </div>
  <div class="row">
    <div style="flex:2;min-width:520px">
      <table id="tbl"><thead><tr><th>Fecha</th><th>Rol</th><th>Zona</th><th>Nombre</th><th>Teléfono</th><th>Estado</th><th>Ver</th></tr></thead><tbody></tbody></table>
    </div>
    <div style="flex:1;min-width:280px">
      <div class="card" style="background:#0e1620">
        <h3 style="margin-top:0">Acción</h3>
        <div id="selInfo" style="color:#9fb0c3">Selecciona una tarea…</div>
        <div id="formBox" style="display:none">
          <div id="meta" style="color:#9fb0c3;margin-bottom:8px"></div>
          <label>Estado</label><br/><select id="status"><option>pending</option><option>done</option></select><br/><br/>
          <label>Notas</label><br/><textarea id="notes" rows="6" style="width:100%"></textarea><br/><br/>
          <button id="save">Guardar</button>
        </div>
      </div>
    </div>
  </div>
</div></div>
<div class="toast" id="toast" style="display:none"></div>
<script>
const $=s=>document.querySelector(s); const st={data:[],sel:null};
function toast(m){const t=$('#toast');t.textContent=m;t.style.display='block';setTimeout(()=>t.style.display='none',2000);}
async function load(){const r=await fetch('/tasks/list'); const d=await r.json(); st.data=d;
 const zones=[...new Set(d.map(x=>x.zone).filter(Boolean))].sort(); $('#fZone').innerHTML='<option value=\"\">(todas)</option>'+zones.map(z=>`<option>${z}</option>`).join(''); render();}
function pill(s){return `<span class="pill ${s==='done'?'done':'pending'}">${s||'pending'}</span>`;}
function render(){const z=$('#fZone').value||'', s=$('#fStatus').value||'', tb=$('#tbl tbody');
 const rows=st.data.filter(x=>(!z||x.zone===z)&&(!s||(x.status||'pending')===s));
 tb.innerHTML=rows.map((x,i)=>`<tr><td>${(x.created_at||'').replace('T',' ').replace('Z','')}</td><td>${x.role||''}</td><td>${x.zone||''}</td><td>${x.name||''}</td><td>${x.phone||''}</td><td>${pill(x.status||'pending')}</td><td><button class="v" data-i="${i}">Ver</button></td></tr>`).join('');
 tb.querySelectorAll('.v').forEach(b=>b.onclick=e=>{const item=rows[e.target.dataset.i]; st.sel=item; $('#selInfo').style.display='none'; $('#formBox').style.display='block';
  $('#status').value=item.status||'pending'; $('#notes').value='';
  $('#meta').innerHTML=`<div><b>${item.name||''}</b> — ${item.role||''} en <b>${item.zone||''}</b></div><div>${item.phone||''}</div>` +
    (item.recording?`<div><a target="_blank" href="${item.recording}">Grabación</a></div>`:'') +
    (item.transcript?`<div style="margin-top:6px;color:#9fb0c3">${item.transcript}</div>`:'');
 }) }
async function save(){ if(!st.sel){toast('Selecciona una tarea');return;}
 const payload={id:st.sel.id||null, call_sid:st.sel.call_sid||null, status:$('#status').value, notes:$('#notes').value||''};
 const r=await fetch('/tasks/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
 if(r.ok){toast('Guardado'); await load(); $('#formBox').style.display='none'; $('#selInfo').style.display='block';} else {toast('Error');} }
$('#refresh').onclick=load; $('#fZone').onchange=render; $('#fStatus').onchange=render; $('#save').onclick=save; load();
</script></body></html>
"""
    return _FlaskResponse(html, mimetype="text/html; charset=utf-8")

# ==============================
# Diagnóstico de rutas
# ==============================
@app.get("/__routes")
def __routes(): return {"routes":[f"{r.endpoint} -> {r.rule}" for r in app.url_map.iter_rules()]}, 200

# ==============================
# MAIN local
# ==============================
if __name__=="__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
