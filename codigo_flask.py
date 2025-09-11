# ==============================
# VOZ — Helpers y prompts
# ==============================

def _ssml(text: str) -> str:
    s=text
    s=re.sub(r"\[\[b(\d{2,4})\]\]", lambda m:f'<break time="{m.group(1)}ms"/>', s)  # [[b250]]
    s=re.sub(r"\[\[digits:([\d\s\+]+)\]\]", lambda m:f'<say-as interpret-as="digits">{m.group(1)}</say-as>', s)
    return f'<prosody rate="medium" pitch="+2%">{s}</prosody>'

def _say_es_ssml(text: str) -> str:
    return f'<Say language="es-ES" voice="{TTS_VOICE}">{_ssml(text)}</Say>'

def _gather_es(action: str, timeout="10", end_silence="auto", allow_dtmf=False) -> str:
    hints=("sí, si, no, propietario, inquilino, jaen, madrid, valencia, sevilla, "
           "barcelona, malaga, granada, soy, me llamo, mi nombre es, uno,dos,tres,cuatro,cinco, "
           "seis,siete,ocho,nueve,cero")
    mode="speech dtmf" if allow_dtmf else "speech"
    return (f'<Gather input="{mode}" language="es-ES" timeout="{timeout}" '
            f'speechTimeout="{end_silence}" speechModel="phone_call" bargeIn="true" '
            f'action="{action}" method="POST" actionOnEmptyResult="true" hints="{hints}">')

# Copys por paso (según nº de intentos)
PROMPTS = {
    "ask_role": [
        "¿Eres propietario o inquilino?",
        "Solo dime [[b120]] propietario [[b120]] o inquilino."
    ],
    "ask_city_prop": [
        "¿En qué población está el inmueble?",
        "Solo la población [[b120]] por ejemplo Jaén o Madrid."
    ],
    "ask_city_tenant": [
        "¿En qué población quieres alquilar?",
        "Solo la población [[b120]] por ejemplo Jaén o Madrid."
    ],
    "ask_name": [
        "¿Cuál es tu nombre completo?",
        "Tu nombre y primer apellido está bien."
    ],
    "ask_phone": [
        "¿Cuál es un teléfono de contacto? [[b180]] Puedes decirlo o marcarlo en el teclado.",
        "Marca ahora tu número [[b150]] o díctalo despacio."
    ],
    "ask_note": [
        "Cuéntame brevemente el motivo de la llamada.",
        "Con una frase corta vale."
    ]
}

def _prompt(step: str, mem: dict) -> str:
    miss = mem.get("miss", 0)
    if step == "ask_city":
        key = "ask_city_prop" if mem.get("role") == "propietario" else "ask_city_tenant"
        arr = PROMPTS[key]
    else:
        arr = PROMPTS.get(step, ["¿Podrías repetir?"])
    return arr[0] if miss == 0 else arr[min(1, len(arr)-1)]

# ==============================
# Parsers ligeros (rol, ciudad, nombre, teléfono)
# ==============================

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
    tail=re.split(r"(?i)(mi\s*telefono|mi\s*tel[eé]fono|telefono|tel\.?|m[oó]vil|movil)", tail)[0]
    tail=re.sub(r"[\d\+\-\.\(\)]"," ", tail)
    tokens=[t for t in re.split(r"\s+", tail.strip()) if t][:3]
    return " ".join(tokens).title()

def _name_from_free(s:str)->str:
    s=(s or "").strip()
    if re.search(r"(?i)(tel[eé]fono|tel\.?|m[oó]vil|movil|\d)", s): return ""
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

# ==============================
# Estado por llamada y flujo /voice/*
# ==============================

# step: ask_role → ask_city (OBLIGATORIO) → ask_name → ask_phone → ask_note → confirm
_IVR = {}  # CallSid -> { step, role, zone, name, phone, note, transcript:[], miss, geo_lat, geo_lng }

@app.get("/voice/health")
def voice_health(): return jsonify(ok=True, service="voice"), 200

@app.route("/voice/answer", methods=["GET","POST"])
def voice_answer():
    cid = unquote_plus(request.form.get("CallSid","") or request.args.get("CallSid","") or "")
    _IVR[cid]={"step":"ask_role","role":"","zone":"","name":"","phone":"","note":"",
               "miss":0,"transcript":[],"geo_lat":None,"geo_lng":None}
    saludo = _line(
        "Hola [[b200]] soy de SpainRoom. ¿Eres propietario o inquilino [[b200]] y de qué población hablamos?",
        "¡Hola! [[b150]] Dime si eres propietario o inquilino [[b150]] y la población."
    )
    return _twiml("<Response>"+_gather_es("/voice/next")+_say_es_ssml(saludo)+"</Gather>"
                  +_say_es_ssml("No te escuché bien [[b200]] vamos otra vez.")
                  +'<Redirect method="POST">/voice/answer</Redirect></Response>')

def _advance(mem):
    # No pasamos de ciudad sin zone
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

    # ---- Multi-slot rellenado, pero no saltamos ciudad ----
    if not mem["role"]:
        r=_role(s)
        if r: mem["role"]=r
    if not mem["zone"]:
        c=_city(s)
        if c: mem["zone"]=c
        else:
            z,lt,lg=_geocode_guess(s)
            if z: mem["zone"]=z; mem["geo_lat"]=lt; mem["geo_lng"]=lg
    if mem["zone"]:
        if not mem["name"]:
            n=_name_from_cued(s) or _name_from_free(s)
            if n: mem["name"]=n
        if not mem["phone"]:
            ph=_phone_from(s,digits)
            if ph: mem["phone"]=ph

    _advance(mem)

    # ---- Small talk solo si falta rol/ciudad ----
    if (mem["step"] in ("ask_role","ask_city")):
        kk=s.lower()
        if any(k in kk for k in ['hola','buenas','qué tal','que tal']):
            return _twiml("<Response>"+_gather_es("/voice/next")+_say_es_ssml("Hola [[b150]] ¿Eres propietario o inquilino [[b150]] y de qué población?")+"</Gather></Response>")

    # ---- Flujo por pasos con reprompts variado y límites ----
    st=mem["step"]

    if st=="ask_role":
        if not mem["role"]:
            mem["miss"]+=1
            return _twiml("<Response>"+_gather_es("/voice/next")+_say_es_ssml(_prompt("ask_role",mem))+"</Gather></Response>")
        mem["step"]="ask_city"; mem["miss"]=0; st="ask_city"

    if st=="ask_city":
        if not mem["zone"]:
            mem["miss"]+=1
            # a los 2 intentos, evitamos bucle: pasamos con "sin-especificar" (irá a Central)
            if mem["miss"]>=2:
                mem["zone"]="sin-especificar"
                mem["step"]="ask_name"; mem["miss"]=0; st="ask_name"
            else:
                return _twiml("<Response>"+_gather_es("/voice/next")+_say_es_ssml(_prompt("ask_city",mem))+"</Gather></Response>")
        else:
            mem["step"]="ask_name"; mem["miss"]=0; st="ask_name"

    if st=="ask_name":
        if not mem["name"] and s:
            n=_name_from_cued(s) or _name_from_free(s)
            if n: mem["name"]=n
        if not mem["name"]:
            mem["miss"]+=1
            return _twiml("<Response>"+_gather_es("/voice/next")+_say_es_ssml(_prompt("ask_name",mem))+"</Gather></Response>")
        mem["step"]="ask_phone"; mem["miss"]=0; st="ask_phone"

    if st=="ask_phone":
        if not mem["phone"]:
            mem["miss"]+=1
            return _twiml("<Response>"+_gather_es("/voice/next", allow_dtmf=True)+_say_es_ssml(_prompt("ask_phone",mem))+"</Gather></Response>")
        mem["step"]="ask_note"; mem["miss"]=0; st="ask_note"

    if st=="ask_note":
        if s:
            mem["note"]=s; mem["step"]="confirm"; mem["miss"]=0; st="confirm"
        else:
            mem["miss"]+=1
            if mem["miss"]>=2:
                mem["note"]="(no especificado)"
                mem["step"]="confirm"; mem["miss"]=0; st="confirm"
            else:
                return _twiml("<Response>"+_gather_es("/voice/next")+_say_es_ssml(_prompt("ask_note",mem))+"</Gather></Response>")

    if st=="confirm":
        zona_lbl = mem["zone"].title().replace("-", " ")
        phone_digits = re.sub(r"\D","", mem["phone"] or "")
        phone_ssml = f"[[digits:{phone_digits}]]" if phone_digits else (mem["phone"] or "no consta")
        resumen = f"{mem['name'] or 'sin nombre'}, {('propietario' if mem['role']=='propietario' else 'inquilino')} en {zona_lbl}. Teléfono {phone_ssml}. ¿Está correcto?"
        return _twiml("<Response>"+_gather_es("/voice/confirm-summary", allow_dtmf=True)+_say_es_ssml(resumen)+"</Gather></Response>")

    # fallback
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
        return _twiml("<Response>"+_gather_es("/voice/next")+_say_es_ssml(_prompt("ask_city",mem))+"</Gather></Response>")
    if yn!="yes":
        return _twiml("<Response>"+_gather_es("/voice/confirm-summary", allow_dtmf=True)+
                      _say_es_ssml("¿Me confirmas [[b120]] por favor? Di sí o no [[b120]] o pulsa 1 o 2.")+"</Gather></Response>")

    # Asignación: si no hay geo, pasará a central
    lt=lg=None
    if mem.get("geo_lat") is not None and mem.get("geo_lng") is not None:
        lt,lg=mem["geo_lat"], mem["geo_lng"]
    else:
        lt,lg,_ = _geocode_city(mem["zone"].replace("-"," "), want_bbox=False)
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
