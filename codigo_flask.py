# SpainRoom · Backend (Render: gunicorn codigo_flask:app)
# - Voz por turnos (SSML sin <speak> / amazon:*), _twiml seguro
# - Slots: Rol → Población (obligatoria) → Nombre → Teléfono → Nota → Confirmación
# - Small-talk solo cuando faltan rol/población
# - Captura robusta de población (heurística + geocodificación del texto)
# - Tareas JSONL + UI /admin/tasks
# - Territorios: /admin/territories (Leaflet) + /territories/auto_seed (siembra automática por ciudad)
# - WAF ligero (no inspecciona /voice, /health, /__routes)

from flask import Flask, request, jsonify, Response, Response as _FlaskResponse, has_request_context
import requests, json, os, re, random, tempfile, shutil
from math import floor
from urllib.parse import unquote_plus
from datetime import datetime, timezone

app = Flask(__name__)

# -------------------- Config --------------------
CENTRAL_PHONE = os.getenv("CENTRAL_PHONE", "+12252553716")
SMS_FROM      = os.getenv("TWILIO_MESSAGING_FROM", "+12252553716")
VOICE_FROM    = os.getenv("TWILIO_VOICE_FROM", "+12252553716")
TTS_VOICE     = os.getenv("TTS_VOICE", "Polly.Conchita")
TERR_FILE     = os.getenv("TERR_FILE", "/tmp/spainroom_territories.json")
GRID_SIZE_DEG = float(os.getenv("GRID_SIZE_DEG", "0.05"))  # ≈5–6 km
TASKS_FILE    = "/tmp/spainroom_tasks.jsonl"

# -------------------- TwiML helper --------------------
def _twiml(body: str) -> Response:
    b = (body or "").strip()
    if not b.startswith("<Response"): b = f"<Response>{b}</Response>"
    return Response(b, mimetype="text/xml")

def _now_iso(): return datetime.now(tz=timezone.utc).isoformat()

# -------------------- WAF (ligero) --------------------
DEF_CFG = {
    "MAX_BODY": int(os.getenv("DEFENSE_MAX_BODY", "524288")),
    "ALLOW_METHODS": set((os.getenv("DEFENSE_ALLOW_METHODS", "GET,POST,OPTIONS")).split(",")),
    "ANOMALY_THRESHOLD": int(os.getenv("DEFENSE_ANOMALY_THRESHOLD", "8")),
    "SKIP_PREFIXES": [p.strip() for p in os.getenv("DEFENSE_SKIP_PREFIXES", "/voice,/__routes,/health").split(",") if p.strip()],
}
_SQLI = [r"(?i)\bunion\b.+\bselect\b", r"(?i)\bor\s+1=1\b"]
_XSS  = [r"(?i)<script\b", r"(?i)javascript:"]

def _waf_skip():
    if not has_request_context(): return True
    return any((request.path or "").startswith(p) for p in DEF_CFG["SKIP_PREFIXES"])

@app.before_request
def _waf():
    if _waf_skip(): return
    score = 0
    if request.method not in DEF_CFG["ALLOW_METHODS"]: score += 2
    q = request.query_string.decode("utf-8","ignore")
    b = (request.get_data(cache=False, as_text=True) or "")[:2048]
    if any(re.search(p,q) for p in _SQLI+_XSS): score+=3
    if any(re.search(p,b) for p in _SQLI+_XSS): score+=3
    if score >= DEF_CFG["ANOMALY_THRESHOLD"]: return Response(status=403)

@app.after_request
def _sec(r):
    r.headers.setdefault("Strict-Transport-Security","max-age=63072000; includeSubDomains; preload")
    r.headers.setdefault("X-Content-Type-Options","nosniff")
    r.headers.setdefault("X-Frame-Options","DENY")
    r.headers.setdefault("Referrer-Policy","no-referrer")
    r.headers.setdefault("Permissions-Policy","geolocation=(), microphone=(), camera=()")
    r.headers.setdefault("Content-Security-Policy","default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    return r

# -------------------- Salud --------------------
@app.get("/health")
def health(): return jsonify(ok=True, service="BACKEND-SPAINROOM"), 200

# -------------------- SSML helpers --------------------
def _ssml(text: str) -> str:
    s=text
    s=re.sub(r"\[\[b(\d{2,4})\]\]", lambda m:f'<break time="{m.group(1)}ms"/>', s)  # [[b250]]
    s=re.sub(r"\[\[digits:([\d\s\+]+)\]\]", lambda m:f'<say-as interpret-as="digits">{m.group(1)}</say-as>', s)
    return f'<prosody rate="medium" pitch="+2%">{s}</prosody>'

def _say_es_ssml(text: str) -> str:
    return f'<Say language="es-ES" voice="{TTS_VOICE}">{_ssml(text)}</Say>'

def _line(*opts): return random.choice([o for o in opts if o])

def _gather_es(action: str, timeout="10", end_silence="auto", allow_dtmf=False) -> str:
    hints=("sí, si, no, propietario, inquilino, jaen, madrid, valencia, sevilla, "
           "barcelona, malaga, granada, soy, me llamo, mi nombre es, uno,dos,tres,cuatro,cinco, "
           "seis,siete,ocho,nueve,cero")
    mode="speech dtmf" if allow_dtmf else "speech"
    return (f'<Gather input="{mode}" language="es-ES" timeout="{timeout}" '
            f'speechTimeout="{end_silence}" speechModel="phone_call" bargeIn="true" '
            f'action="{action}" method="POST" actionOnEmptyResult="true" hints="{hints}">')

# -------------------- Territorios (datos + helpers) --------------------
PROVS = {"jaen":"Jaén","madrid":"Madrid","valencia":"Valencia","sevilla":"Sevilla",
         "barcelona":"Barcelona","malaga":"Málaga","granada":"Granada"}

def _slug(s:str)->str:
    s=(s or "").lower().strip()
    for a,b in {"á":"a","é":"e","í":"i","ó":"o","ú":"u","ü":"u","ñ":"n"}.items(): s=s.replace(a,b)
    return re.sub(r"[^a-z0-9]+","-",s).strip("-")

def _tile_key(lat:float,lng:float,size:float=GRID_SIZE_DEG)->str:
    return f"tile_{size:.3f}_{floor(lat/size)}_{floor(lng/size)}"

def _terr_load():
    if os.path.exists(TERR_FILE):
        try:
            with open(TERR_FILE,"r",encoding="utf-8") as f: return json.load(f)
        except: pass
    return {"tiles":{}, "microzones":[]}

def _terr_save(obj):
    try:
        with open(TERR_FILE,"w",encoding="utf-8") as f: json.dump(obj,f,ensure_ascii=False,indent=2)
    except: pass

def _in_bbox(lat,lng,b): return (b[0] <= lat <= b[2]) and (b[1] <= lng <= b[3])

def _geocode_city(city:str, want_bbox=False):
    try:
        url=f"https://nominatim.openstreetmap.org/search?q={city}, España&format=json&limit=1&addressdetails=1"
        r=requests.get(url, headers={"User-Agent":"SpainRoom/1.0"}, timeout=8)
        if r.status_code==200 and r.json():
            d=r.json()[0]; lat=float(d["lat"]); lng=float(d["lon"])
            if want_bbox and "boundingbox" in d:
                bb=d["boundingbox"]; bbox=[float(bb[0]), float(bb[2]), float(bb[1]), float(bb[3])]  # [minlat,minlng,maxlat,maxlng]
                return lat,lng,bbox
            return lat,lng,None
    except: pass
    return None,None,None

TERR=_terr_load()

def _find_phones(lat,lng,city_slug):
    for mz in TERR.get("microzones",[]):
        if mz.get("city")==city_slug and _in_bbox(lat,lng,mz.get("bbox",[0,0,0,0])):
            return mz.get("phones") or [], f"{mz.get('city')}::{mz.get('name')}"
    key=_tile_key(lat,lng)
    if key in TERR.get("tiles",{}):
        t=TERR["tiles"][key]; return t.get("phones") or [], key
    return [], "unassigned"

# -------------------- Parsers --------------------
def _norm(s:str)->str:
    s=(s or "").lower().strip()
    for a,b in {"á":"a","é":"e","í":"i","ó":"o","ú":"u","ü":"u","ñ":"n"}.items(): s=s.replace(a,b)
    return s

def _role(s:str)->str:
    s=_norm(s)
    if "propiet" in s or "duen" in s: return "propietario"
    if "inquil" in s or "alquil" in s or "habitacion" in s: return "inquilino"
    return ""

def _city(s:str)->str:
    s=_norm(s)
    alias={"barna":"barcelona","md":"madrid","vlc":"valencia","sevill":"sevilla"}
    for k,v in alias.items():
        if k in s: s=v
    for key in PROVS.keys():
        if key in s or s==key: return key
    return ""

def _name_from_cued(s:str)->str:
    s=(s or "")
    m=re.search(r"(?i)(me llamo|mi nombre es|soy)\s+(.+)", s)
    if not m: return ""
    tail=m.group(2)
    tail=re.split(r"(?i)(mi\s*telefono|mi\s*teléfono|telefono|tel\.?|móvil|movil)", tail)[0]
    tail=re.sub(r"[\d\+\-\.\(\)]"," ", tail)
    tokens=[t for t in re.split(r"\s+", tail.strip()) if t][:3]
    return " ".join(tokens).title()

def _name_from_free(s:str)->str:
    s=(s or "").strip()
    if re.search(r"(?i)(telefono|tel\.?|m(ó|o)vil|movil|\d)", s): return ""
    tokens=[t for t in re.split(r"\s+", s) if t]
    if 1 <= len(tokens) <= 4:
        bad={"soy","me","llamo","nombre","mi","es","el","la","de"}
        if all(t.lower() not in bad for t in tokens):
            return " ".join(tokens).title()
    return ""

def _phone_from(s:str, digits:str)->str:
    d=re.sub(r"\D","", digits or "")
    if len(d)>=9: return "+34"+d if not d.startswith(("34","+")) else ("+"+d if not d.startswith("+") else d)
    s=_norm(s)
    for k,v in {"uno":"1","dos":"2","tres":"3","cuatro":"4","cinco":"5","seis":"6","siete":"7","ocho":"8","nueve":"9","cero":"0"}.items():
        s=s.replace(k,v)
    d=re.sub(r"\D","", s)
    if len(d)>=9: return "+34"+d if not d.startswith(("34","+")) else ("+"+d if not d.startswith("+") else d)
    return ""

def _geocode_guess(text:str):
    q=(text or "").strip()
    if len(q)<3: return None,None,None
    try:
        url=f"https://nominatim.openstreetmap.org/search?q={q}, España&format=json&limit=1"
        r=requests.get(url, headers={"User-Agent":"SpainRoom/1.0"}, timeout=8)
        if r.status_code==200 and r.json():
            d=r.json()[0]; name=d.get("display_name","").split(",")[0]
            return _slug(name), float(d["lat"]), float(d["lon"])
    except: pass
    return None,None,None

# -------------------- Estado por llamada --------------------
# step: ask_role → ask_city (OBLIGATORIO) → ask_name → ask_phone → ask_note → confirm
_IVR = {}  # CallSid -> { step, role, zone, name, phone, note, transcript:[], miss, geo_lat, geo_lng }

@app.get("/voice/health")
def voice_health(): return jsonify(ok=True, service="voice"), 200

@app.route("/voice/answer", methods=["GET","POST"])
def voice_answer():
    cid = unquote_plus(request.form.get("CallSid","") or request.args.get("CallSid","") or "")
    _IVR[cid]={"step":"ask_role","role":"","zone":"","name":"","phone":"","note":"",
               "miss":0,"transcript":[],"geo_lat":None,"geo_lng":None}
    text=_line(
        "Hola [[b200]] soy de SpainRoom. ¿Eres propietario o inquilino [[b200]] y de qué población hablamos?",
        "¡Hola! [[b150]] Cuéntame en una frase [[b150]] si eres propietario o inquilino [[b150]] y la población."
    )
    return _twiml("<Response>"+_gather_es("/voice/next")+_say_es_ssml(text)+"</Gather>"
                  +_say_es_ssml("No te escuché bien [[b200]] vamos otra vez.")
                  +'<Redirect method="POST">/voice/answer</Redirect></Response>')

def _advance(mem):
    # Avance estricto: NO avanzamos más allá de ask_city mientras no haya zone
    if mem["step"]=="ask_role" and mem["role"]: mem["step"]="ask_city"; mem["miss"]=0
    if mem["step"]=="ask_city" and mem["zone"]: mem["step"]="ask_name"; mem["miss"]=0
    if mem["step"]=="ask_name" and mem["name"]: mem["step"]="ask_phone"; mem["miss"]=0
    if mem["step"]=="ask_phone" and mem["phone"]: mem["step"]="ask_note"; mem["miss"]=0
    if mem["step"]=="ask_note" and mem["note"]: mem["step"]="confirm"; mem["miss"]=0

@app.route("/voice/next", methods=["POST"])
def voice_next():
    cid = unquote_plus(request.form.get("CallSid",""))
    speech = unquote_plus(request.form.get("SpeechResult","")); digits=request.form.get("Digits","")
    mem=_IVR.setdefault(cid,{"step":"ask_role","role":"","zone":"","name":"",
                             "phone":"","note":"","miss":0,"transcript":[],"geo_lat":None,"geo_lng":None})
    s=(speech or "").strip()
    if s: mem["transcript"].append(s)

    # ---- Relleno rápido, PERO sin saltar población obligatoria ----
    if not mem["role"]:
        r=_role(s)
        if r: mem["role"]=r

    if not mem["zone"]:
        c=_city(s)
        if c: mem["zone"]=c
        else:
            z,lt,lg=_geocode_guess(s)
            if z: mem["zone"]=z; mem["geo_lat"]=lt; mem["geo_lng"]=lg

    # Nombre y teléfono solo se guardan si YA tenemos población
    if mem["zone"]:
        if not mem["name"]:
            n=_name_from_cued(s) or _name_from_free(s)
            if n: mem["name"]=n
        if not mem["phone"]:
            ph=_phone_from(s,digits)
            if ph: mem["phone"]=ph

    _advance(mem)

    # ---- Small-talk SOLO si falta rol/ciudad y estamos en esos pasos ----
    kind=None
    if (mem["step"]=="ask_role" and not mem["role"]) or (mem["step"]=="ask_city" and not mem["zone"]):
        kk=s.lower()
        if any(k in kk for k in ['hola','buenas','qué tal','que tal']): kind='greeting'
        elif any(k in kk for k in ['buen dia','buen día','tiempo','clima']): kind='weather'
        elif any(k in kk for k in ['hora es','qué hora']): kind='time'
        elif any(k in kk for k in ['gracias','muchas gracias']): kind='thanks'
    if kind=='greeting':
        return _twiml("<Response>"+_gather_es("/voice/next")+_say_es_ssml("Hola [[b150]] encantada. ¿Eres propietario o inquilino [[b150]] y de qué población?")+"</Gather></Response>")
    if kind=='weather':
        return _twiml("<Response>"+_gather_es("/voice/next")+_say_es_ssml("¡Qué bien! [[b150]] Dime [[b120]] ¿eres propietario o inquilino y en qué población?")+"</Gather></Response>")
    if kind=='time':
        return _twiml("<Response>"+_gather_es("/voice/next")+_say_es_ssml("Estoy aquí ahora mismo para ayudarte [[b150]] ¿propietario o inquilino [[b120]] y de qué población?")+"</Gather></Response>")
    if kind=='thanks':
        return _twiml("<Response>"+_gather_es("/voice/next")+_say_es_ssml("¡A ti! [[b120]] Seguimos [[b100]] ¿propietario o inquilino y de qué población?")+"</Gather></Response>")

    # ---- Flujo por pasos ----
    st=mem["step"]
    if st=="ask_role":
        if not mem["role"]:
            mem["miss"]+=1
            prompt="¿Eres propietario o inquilino?" if mem["miss"]<2 else "Solo dime [[b120]] propietario [[b120]] o inquilino."
            return _twiml("<Response>"+_gather_es("/voice/next")+_say_es_ssml(prompt)+"</Gather></Response>")
        mem["step"]="ask_city"; mem["miss"]=0; st="ask_city"

    if st=="ask_city":
        if not mem["zone"]:
            mem["miss"]+=1
            pregunta=("¿En qué población está el inmueble?" if mem["role"]=="propietario" else "¿En qué población quieres alquilar?")
            if mem["miss"]>=2: pregunta="Solo la población [[b120]] por ejemplo Jaén o Madrid."
            return _twiml("<Response>"+_gather_es("/voice/next")+_say_es_ssml(pregunta)+"</Gather></Response>")
        mem["step"]="ask_name"; mem["miss"]=0; st="ask_name"

    if st=="ask_name":
        if not mem["name"] and s:
            n=_name_from_cued(s) or _name_from_free(s)
            if n: mem["name"]=n
        if not mem["name"]:
            mem["miss"]+=1
            return _twiml("<Response>"+_gather_es("/voice/next")+_say_es_ssml("¿Cuál es tu nombre completo?")+" </Gather></Response>")
        mem["step"]="ask_phone"; mem["miss"]=0; st="ask_phone"

    if st=="ask_phone":
        if not mem["phone"]:
            mem["miss"]+=1
            txt="¿Cuál es un teléfono de contacto? [[b180]] Puedes decirlo o marcarlo en el teclado."
            if mem["miss"]>=2: txt="Marca ahora tu número [[b150]] o díctalo despacio."
            return _twiml("<Response>"+_gather_es("/voice/next", allow_dtmf=True)+_say_es_ssml(txt)+"</Gather></Response>")
        mem["step"]="ask_note"; mem["miss"]=0; st="ask_note"

    if st=="ask_note":
        if not mem["note"]:
            mem["miss"]+=1
            return _twiml("<Response>"+_gather_es("/voice/next")+_say_es_ssml("Cuéntame brevemente el motivo de la llamada.")+"</Gather></Response>")
        mem["step"]="confirm"; mem["miss"]=0; st="confirm"

    if st=="confirm":
        zona_lbl = mem["zone"].title().replace("-", " ")
        phone_digits = re.sub(r"\D","", mem["phone"] or "")
        phone_ssml = f"[[digits:{phone_digits}]]" if phone_digits else (mem["phone"] or "no consta")
        resumen = f"{mem['name'] or 'sin nombre'}, {('propietario' if mem['role']=='propietario' else 'inquilino')} en {zona_lbl}. Teléfono {phone_ssml}. ¿Está correcto?"
        return _twiml("<Response>"+_gather_es("/voice/confirm-summary", allow_dtmf=True)+_say_es_ssml(resumen)+"</Gather></Response>")

    return _twiml("<Response>"+_gather_es("/voice/next")+_say_es_ssml("Seguimos [[b120]] ¿me repites, por favor?")+"</Gather></Response>")

@app.post("/voice/confirm-summary")
def voice_confirm_summary():
    cid=unquote_plus(request.form.get("CallSid",""))
    speech=unquote_plus(request.form.get("SpeechResult","")); digits=request.form.get("Digits","")
    yn="yes" if (digits=="1" or re.search(r"\b(si|sí|vale|correcto|claro|ok)\b",(speech or "").lower())) else \
       ("no" if (digits=="2" or re.search(r"\bno\b",(speech or "").lower())) else "")
    mem=_IVR.get(cid)
    if not mem: return _twiml('<Response><Redirect method="POST">/voice/answer</Redirect></Response>')
    if yn=="no":
        mem["step"]="ask_city"; mem["miss"]=0
        pregunta=("Vamos a corregirlo [[b120]] ¿en qué población está el inmueble?"
                  if mem["role"]=="propietario" else "Vamos a corregirlo [[b120]] ¿en qué población quieres alquilar?")
        return _twiml("<Response>"+_gather_es("/voice/next")+_say_es_ssml(pregunta)+"</Gather></Response>")
    if yn!="yes":
        return _twiml("<Response>"+_gather_es("/voice/confirm-summary", allow_dtmf=True)+
                      _say_es_ssml("¿Me confirmas [[b120]] por favor? Di sí o no [[b120]] o pulsa 1 o 2.")+"</Gather></Response>")

    # Asignación (demo): geocode final si hace falta
    lt=lg=None
    if mem.get("geo_lat") is not None and mem.get("geo_lng") is not None:
        lt,lg=mem["geo_lat"], mem["geo_lng"]
    else:
        lt,lg=_geocode_city(mem["zone"].replace("-"," "))[0:2]
    phones, owner = ([], "unassigned")
    if (lt is not None) and (lg is not None):
        phones, owner = _find_phones(lt,lg,_slug(mem["zone"]))
    assignees = phones if phones else [CENTRAL_PHONE]

    transcript_text = " | ".join([t for t in mem.get("transcript",[]) if t])
    task={"created_at":_now_iso(),"call_sid":cid,"role":mem["role"],"zone":mem["zone"],
          "name":mem["name"],"phone":mem["phone"],"assignees":assignees,
          "recording":"","transcript":transcript_text,"status":"pending"}
    try:
        with open(TASKS_FILE,"a",encoding="utf-8") as f: f.write(json.dumps(task,ensure_ascii=False)+"\n")
    except: pass

    resumen=(f"SpainRoom: {mem['role']} en {mem['zone'].title().replace('-',' ')}. "
             f"Nombre: {mem['name'] or 'N/D'}. Tel: {mem['phone'] or 'N/D'}. "
             f"Nota: {mem['note'] or 'N/D'}. Destino: {owner}")
    for to in assignees:
        try:
            from twilio.rest import Client
            Client(os.getenv("TWILIO_ACCOUNT_SID",""), os.getenv("TWILIO_AUTH_TOKEN","")).messages.create(
                from_=SMS_FROM, to=to, body=resumen
            )
        except: pass

    thanks=_line("Perfecto, ya tengo todo [[b150]] la persona de tu zona te llamará en breve. ¡Gracias!",
                 "Gracias [[b150]] te llamarán desde tu zona en breve.")
    del _IVR[cid]
    return _twiml("<Response>"+_say_es_ssml(thanks)+"<Hangup/></Response>")

# -------------------- UI tareas --------------------
@app.get("/admin/tasks")
def admin_tasks_page():
    html = """<!doctype html><html lang="es"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>SpainRoom · Tareas</title>
<style>body{margin:0;font-family:system-ui,Segoe UI,Roboto,Arial;background:#0b1118;color:#e7eef7}
header{padding:16px 18px;background:#0b1118;border-bottom:1px solid #1b2a3a}
.wrap{max-width:1120px;margin:20px auto;padding:0 14px}
.card{background:#121923;border:1px solid #1b2a3a;border-radius:12px;padding:16px}
table{width:100%;border-collapse:collapse;margin-top:10px}
th,td{padding:9px;border-bottom:1px solid #1b2a3a;font-size:14px} th{color:#9fb0c3;text-align:left}
button,select,input,textarea{background:#0e1620;border:1px solid #1b2a3a;color:#e7eef7;border-radius:10px;padding:10px}
button{background:#2563eb;border:0;cursor:pointer}
.pill{padding:4px 8px;border-radius:18px;font-size:12px}.pending{background:#1f2a37}.done{background:#0b3b2f}
.toast{position:fixed;right:14px;bottom:14px;background:#0e1620;border:1px solid #1b2a3a;border-radius:10px;padding:10px 14px}
</style></head><body>
<header><h2 style="margin:0">SpainRoom · Tareas</h2></header>
<div class="wrap"><div class="card">
  <div style="display:flex;gap:12px;align-items:end;flex-wrap:wrap">
    <div><label>Zona</label><br/><select id="fZone"><option value="">(todas)</option></select></div>
    <div><label>Estado</label><br/><select id="fStatus"><option value="">(todos)</option><option>pending</option><option>done</option></select></div>
    <div style="margin-left:auto"><button id="refresh">Actualizar</button></div>
  </div>
  <div style="display:flex;gap:12px;flex-wrap:wrap">
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
 const payload={call_sid:st.sel.call_sid||null, status:$('#status').value, notes:$('#notes').value||''};
 const r=await fetch('/tasks/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
 if(r.ok){toast('Guardado'); await load(); $('#formBox').style.display='none'; $('#selInfo').style.display='block';} else {toast('Error');} }
$('#refresh').onclick=load; $('#fZone').onchange=render; $('#fStatus').onchange=render; $('#save').onclick=save; load();
</script></body></html>"""
    return _FlaskResponse(html, mimetype="text/html; charset=utf-8")

# -------------------- UI / Admin TERRITORIOS (Leaflet) --------------------
@app.get("/admin/territories")
def admin_territories():
    html = f"""<!doctype html><html lang="es"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>SpainRoom · Territorios</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.css"/>
<style>
  body{{margin:0;font-family:system-ui,Segoe UI,Roboto,Arial;background:#0b1118;color:#e7eef7}}
  header{{padding:12px 16px;background:#0b1118;border-bottom:1px solid #1b2a3a}}
  .wrap{{display:flex;gap:12px;flex-wrap:wrap;padding:12px}}
  #map{{height:70vh;min-height:520px;border:1px solid #1b2a3a;border-radius:10px}}
  .card{{background:#121923;border:1px solid #1b2a3a;border-radius:12px;padding:12px}}
  input,select,textarea,button{{background:#0e1620;border:1px solid #1b2a3a;color:#e7eef7;border-radius:10px;padding:8px}}
  button{{background:#2563eb;border:0;cursor:pointer}}
  table{{width:100%;border-collapse:collapse;margin-top:8px}}
  th,td{{padding:6px;border-bottom:1px solid #1b2a3a;font-size:13px}}
  th{{color:#9fb0c3;text-align:left}}
</style></head><body>
<header><h2 style="margin:0">SpainRoom · Territorios</h2></header>
<div class="wrap">
  <div class="card" style="flex:2;min-width:600px">
    <div id="map"></div>
  </div>
  <div class="card" style="flex:1;min-width:300px">
    <h3 style="margin:0 0 8px 0">Microzona (bbox)</h3>
    <div style="font-size:13px;color:#9fb0c3">Dibuja un rectángulo en el mapa y completa:</div>
    <label>Ciudad/Barrio (slug auto si vacío)</label><br/>
    <input id="city" placeholder="madrid / retiro" style="width:100%"/><br/><br/>
    <label>Teléfonos (coma separada)</label><br/>
    <input id="phones" placeholder="+3468..., +3460..." style="width:100%"/><br/><br/>
    <div style="display:flex;gap:8px">
      <button id="saveBbox">Guardar microzona</button>
      <button id="clearBbox" style="background:#374151">Limpiar selección</button>
    </div>
    <hr style="border:0;border-top:1px solid #1b2a3a;margin:12px 0">
    <h3 style="margin:0 0 8px 0">Rejilla (tile)</h3>
    <label>Tamaño (deg)</label>
    <input id="tileSize" type="number" step="0.01" value="{GRID_SIZE_DEG}" style="width:90px"/>
    <button id="claimTile">Reclamar tile aquí</button>
    <div style="font-size:12px;color:#9fb0c3;margin-top:6px">Usa el centro del mapa.</div>
  </div>
</div>

<div class="wrap">
  <div class="card" style="flex:1;min-width:400px">
    <h3 style="margin:0 0 8px 0">Microzonas</h3>
    <table id="tblMz"><thead><tr><th>Ciudad</th><th>Nombre</th><th>bbox</th><th>Phones</th><th></th></tr></thead><tbody></tbody></table>
  </div>
  <div class="card" style="flex:1;min-width:400px">
    <h3 style="margin:0 0 8px 0">Tiles</h3>
    <table id="tblTiles"><thead><tr><th>Clave</th><th>Label</th><th>Phones</th><th></th></tr></thead><tbody></tbody></table>
  </div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.js"></script>
<script>
let map=L.map('map').setView([40.4168,-3.7038],6);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19}).addTo(map);

let drawnItems=new L.FeatureGroup().addTo(map);
let drawControl=new L.Control.Draw({draw:{polygon:false,polyline:false,circle:false,circlemarker:false,marker:false,rectangle:true},edit:{featureGroup:drawnItems}});
map.addControl(drawControl);
let selBbox=null;

map.on(L.Draw.Event.CREATED,function(e){
  drawnItems.clearLayers();
  let layer=e.layer; drawnItems.addLayer(layer);
  selBbox=layer.getBounds();
});

document.getElementById('clearBbox').onclick=()=>{ drawnItems.clearLayers(); selBbox=null; };
document.getElementById('saveBbox').onclick=async()=>{
  if(!selBbox){alert('Dibuja una microzona'); return;}
  const city=(document.getElementById('city').value||'').trim();
  const phones=(document.getElementById('phones').value||'').split(',').map(s=>s.trim()).filter(Boolean);
  const sw=selBbox.getSouthWest(), ne=selBbox.getNorthEast();
  const body={mode:'bbox', city:city, label: city||'zona', bbox:[sw.lat,sw.lng,ne.lat,ne.lng], phones:phones};
  const r=await fetch('/territories/claim',{method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if(r.ok){alert('Microzona guardada'); loadAll();} else {alert('Error al guardar');}
};

document.getElementById('claimTile').onclick=async()=>{
  const size=parseFloat(document.getElementById('tileSize').value)||{GRID_SIZE_DEG};
  const c=map.getCenter();
  const body={mode:'tile', lat:c.lat, lng:c.lng, size:size, label:'tile '+size, phones:[]};
  const r=await fetch('/territories/claim',{method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if(r.ok){alert('Tile reclamado'); loadAll();} else {alert('Error al reclamar tile');}
};

async function loadAll(){
  drawnItems.clearLayers();
  const r=await fetch('/territories/list'); const data=await r.json();
  // pintar microzonas
  const tbMz=document.querySelector('#tblMz tbody'); tbMz.innerHTML='';
  (data.microzones||[]).forEach((mz)=>{
    const b=mz.bbox; L.rectangle([[b[0],b[1]],[b[2],b[3]]],{color:'#22c55e',weight:1}).addTo(drawnItems);
    const tr=document.createElement('tr');
    tr.innerHTML=`<td>${mz.city}</td><td>${mz.name}</td><td>${b.map(x=>x.toFixed(3)).join(',')}</td><td>${(mz.phones||[]).join('<br/>')}</td><td><button data-city="${mz.city}" data-label="${mz.name}" class="delMz">Borrar</button></td>`;
    tbMz.appendChild(tr);
  });
  // tiles
  const tbT=document.querySelector('#tblTiles tbody'); tbT.innerHTML='';
  (Object.entries(data.tiles||{})).forEach(([key,t])=>{
    const parts=key.split('_'); const size=parseFloat(parts[1]); const li=parseInt(parts[2]); const lj=parseInt(parts[3]);
    const sw=[(li)*size,(lj)*size], ne=[(li+1)*size,(lj+1)*size];
    L.rectangle([sw,ne],{color:'#3b82f6',weight:1,dashArray:'4 3'}).addTo(drawnItems);
    const tr=document.createElement('tr');
    tr.innerHTML=`<td>${key}</td><td>${t.label||''}</td><td>${(t.phones||[]).join('<br/>')}</td><td><button data-key="${key}" class="delTile">Borrar</button></td>`;
    tbT.appendChild(tr);
  });
  document.querySelectorAll('.delMz').forEach(btn=>btn.onclick=async()=>{
    const r=await fetch('/territories/unclaim',{method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({mode:'bbox', city:btn.dataset.city, label:btn.dataset.label})});
    if(r.ok){loadAll();} else {alert('No se pudo borrar');}
  });
  document.querySelectorAll('.delTile').forEach(btn=>btn.onclick=async()=>{
    const r=await fetch('/territories/unclaim',{method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({mode:'tile', key:btn.dataset.key})});
    if(r.ok){loadAll();} else {alert('No se pudo borrar');}
  });
}
loadAll();
</script>
</body></html>"""
    return _FlaskResponse(html, mimetype="text/html; charset=utf-8")

# -------------------- API Territorios (+ auto_seed) --------------------
@app.get("/territories/list")
def terr_list():
    global TERR
    return jsonify(TERR),200

@app.post("/territories/claim")
def terr_claim():
    global TERR
    payload=request.get_json(force=True) or {}
    mode=payload.get("mode")
    if mode=="tile":
        lat=float(payload.get("lat")); lng=float(payload.get("lng"))
        size=float(payload.get("size", GRID_SIZE_DEG))
        key=_tile_key(lat,lng,size)
        TERR["tiles"][key]={"label":payload.get("label",""),"phones":payload.get("phones") or []}
        _terr_save(TERR); return jsonify(ok=True,key=key),200
    elif mode=="bbox":
        city=_slug(payload.get("city","")); bbox=payload.get("bbox"); name=payload.get("label","zona")
        if not (city and isinstance(bbox,list) and len(bbox)==4): return jsonify(ok=False,error="invalid_bbox"),400
        TERR["microzones"].append({"city":city,"name":name,"bbox":bbox,"phones":payload.get("phones") or []})
        _terr_save(TERR); return jsonify(ok=True),200
    return jsonify(ok=False,error="mode_required"),400

@app.post("/territories/unclaim")
def terr_unclaim():
    global TERR
    payload=request.get_json(force=True) or {}
    mode=payload.get("mode")
    if mode=="tile":
        key=payload.get("key",""); TERR["tiles"].pop(key, None); _terr_save(TERR); return jsonify(ok=True),200
    elif mode=="bbox":
        city=_slug(payload.get("city","")); name=payload.get("label","")
        TERR["microzones"]=[mz for mz in TERR.get("microzones",[]) if not (mz.get("city")==city and mz.get("name")==name)]
        _terr_save(TERR); return jsonify(ok=True),200
    return jsonify(ok=False,error="mode_required"),400

@app.post("/territories/auto_seed")
def terr_auto_seed():
    """
    JSON:
    {
      "city": "Madrid",
      "mode": "madrid_barrios" | "barcelona_distritos" | "by_population",
      "population": 3200000,          # opcional (by_population)
      "phones": ["+34..."]            # opcional (se copia a todas las celdas)
    }
    """
    global TERR
    data = request.get_json(force=True) or {}
    city = (data.get("city") or "").strip()
    mode = (data.get("mode") or "by_population").lower()
    pop  = int(data.get("population") or 0)
    phones = data.get("phones") or []
    if not city: return jsonify(ok=False,error="city_required"),400

    lat,lng,bbox = _geocode_city(city, want_bbox=True)
    if bbox is None: return jsonify(ok=False,error="bbox_not_found"),400

    # decidir nº celdas
    if mode=="madrid_barrios":
        cells = 21  # distritos
    elif mode=="barcelona_distritos":
        cells = 10
    else:
        # by_population
        if   pop < 10000:   cells = 1
        elif pop < 20000:   cells = 2
        elif pop < 50000:   cells = 3
        elif pop < 100000:  cells = 5
        elif pop < 200000:  cells = 8
        elif pop < 500000:  cells = 12
        else:               cells = 20

    # partir bbox en grid aproximada (rows*cols >= cells)
    import math
    rows = int(math.sqrt(cells))
    cols = math.ceil(cells/rows)
    minlat,minlng,maxlat,maxlng = bbox[0], bbox[1], bbox[2], bbox[3]
    dlat=(maxlat-minlat)/rows; dlng=(maxlng-minlng)/cols

    city_slug=_slug(city)
    # limpiar microzonas existentes de esa city si las hubiera
    TERR["microzones"]=[mz for mz in TERR.get("microzones",[]) if mz.get("city")!=city_slug]

    idx=1
    for r in range(rows):
        for c in range(cols):
            if idx>cells: break
            bb=[minlat+r*dlat, minlng+c*dlng, minlat+(r+1)*dlat, minlng+(c+1)*dlng]
            TERR["microzones"].append({"city":city_slug,"name":f"zona-{idx}","bbox":bb,"phones":phones})
            idx+=1

    _terr_save(TERR)
    return jsonify(ok=True, city=city_slug, cells=cells), 200

# -------------------- DIAGNÓSTICO --------------------
@app.get("/__routes")
def __routes(): return {"routes":[f"{r.endpoint} -> {r.rule}" for r in app.url_map.iter_rules()]}, 200

# -------------------- MAIN --------------------
if __name__=="__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
