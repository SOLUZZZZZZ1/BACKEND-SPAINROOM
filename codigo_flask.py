# SpainRoom — Voice Backend (ConversationRelay) — ES STABLE (Greeting + No-Loop, No Barge-In)
# Start: python -m uvicorn codigo_flask:app --host 0.0.0.0 --port $PORT --proxy-headers

import os, json, re, time, contextlib, hashlib
from typing import Dict, Any
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import Response, JSONResponse
from xml.sax.saxutils import quoteattr

app = FastAPI(title="SpainRoom Voice — ConversationRelay (ES Stable)")

def _twiml(xml: str) -> Response: return Response(content=xml, media_type="application/xml")
def _env(k: str, default: str = "") -> str: return os.getenv(k, default)
def _host(req: Request) -> str: return req.headers.get("host") or req.url.hostname or "localhost"

@app.get("/health")
async def health(): return JSONResponse({"ok": True})

@app.get("/diag_runtime")
async def diag_runtime():
    keys = ["CR_TTS_PROVIDER","CR_LANGUAGE","CR_TRANSCRIPTION_LANGUAGE","CR_VOICE",
            "CR_WELCOME","SPEAK_SLEEP_MS","COOLDOWN_MS","MIN_TTS_GAP_MS","POST_PROMPT_OFF",
            "ASSIGN_URL","ASSIGN_URL_EXPANSION","ASSIGN_URL_SUPPORT"]
    return JSONResponse({k: _env(k) for k in keys})

@app.api_route("/voice/answer_cr", methods=["GET","POST"])
async def answer_cr(request: Request):
    host = _host(request)
    ws = f"wss://{host}/cr"
    lang = _env("CR_LANGUAGE","es-ES"); tr=_env("CR_TRANSCRIPTION_LANGUAGE",lang)
    prov=_env("CR_TTS_PROVIDER","Google"); voice=_env("CR_VOICE","es-ES-Standard-A")
    welcome=_env("CR_WELCOME","Bienvenido a SpainRoom.")
    attrs = [f"url={quoteattr(ws)}", f"language={quoteattr(lang)}", f"transcriptionLanguage={quoteattr(tr)}",
             f"ttsProvider={quoteattr(prov)}", 'interruptible="speech"', 'reportInputDuringAgentSpeech="none"']
    if welcome.strip(): attrs.append(f"welcomeGreeting={quoteattr(welcome.strip())}")
    if voice.strip():   attrs.append(f"voice={quoteattr(voice.strip())}")
    twiml = '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n  <Connect>\n    <ConversationRelay %s />\n  </Connect>\n</Response>' % (" ".join(attrs))
    return _twiml(twiml)

@app.websocket("/cr")
async def cr(ws: WebSocket):
    await ws.accept()
    COOLDOWN_MS = int(_env("COOLDOWN_MS","3000"))
    MIN_TTS_GAP_MS = int(_env("MIN_TTS_GAP_MS","600"))
    POST_PROMPT_OFF = _env("POST_PROMPT_OFF","1") == "1"
    SPEAK_SLEEP_MS = int(_env("SPEAK_SLEEP_MS","0"))
    session: Dict[str, Any] = {"step":"await_setup","lead":{"role":"","poblacion":"","zona":"","nombre":"","telefono":""},
        "last_q":None,"last_q_ts":0.0,"last_user":None,"last_user_ts":0.0,"last_tts_ts":0.0,
        "fr_mode":None,"fr_categoria":"","fr_detalle":""}
    def _now(): return time.monotonic()*1000.0
    def _norm(t): return re.sub(r"\s+"," ",(t or "").strip())
    async def speak(txt, interruptible=True):
        now=_now()
        if (now - session["last_tts_ts"]) < MIN_TTS_GAP_MS: return
        await ws.send_json({"type":"text","token":txt,"last":True,"interruptible":bool(interruptible)})
        session["last_tts_ts"] = _now()
        try:
            import asyncio; await asyncio.sleep(SPEAK_SLEEP_MS/1000.0)
        except Exception: pass
    def _dup_user(t):
        t=_norm(t).lower(); now=_now()
        if session["last_user"]==t and (now-session["last_user_ts"])<COOLDOWN_MS: return True
        session["last_user"]=t; session["last_user_ts"]=now; return False
    async def ask_once(key):
        now=_now()
        if session["last_q"]==key and (now-session["last_q_ts"])<COOLDOWN_MS: return
        session["last_q"]=key; session["last_q_ts"]=now
        prompts={"role":"Para atenderle: ¿Es usted propietario o inquilino?",
                 "city":"¿En qué población está interesado?",
                 "zone":"¿Qué zona o barrio?",
                 "name":"¿Su nombre completo?",
                 "phone":"¿Su teléfono de contacto, por favor?"}
        if key in prompts: await speak(prompts[key])
    INFO_RE = re.compile(r"\b(info|informaci[oó]n|m[aá]s info|saber m[aá]s|cu[ée]ntame|dime|explica|detalles)\b", re.I)
    HELP_RE = re.compile(r"\b(ayuda|asesor|llamar|contacto|por favor|tel[eé]fono)\b", re.I)
    PRICES_RE = re.compile(r"\b(precio|precios|tarifa|coste|costos)\b", re.I)
    CONTRACT_RE = re.compile(r"\b(contrato|firm[ao]|logalty)\b", re.I)
    PROCESS_RE = re.compile(r"\b(proceso|pasos|c[oó]mo func|como func)\b", re.I)
    DOCS_RE = re.compile(r"\b(document|dni|pasaporte|papeles)\b", re.I)
    FR_RE = re.compile(r"\b(franquic|royalty|licencia|territor|expansi[oó]n|soy franquiciado|mi zona)\b", re.I)
    async def info_general():
        await speak("SpainRoom alquila habitaciones medio y largo plazo. No somos hotel.")
        await speak("Proceso: solicitud, verificación, contrato digital y entrada.")
    async def info_topic(tl):
        if PRICES_RE.search(tl):
            await speak("El precio depende de ciudad y habitación. Mínimo un mes.")
            await speak("Podemos comparar opciones en su presupuesto."); return True
        if CONTRACT_RE.search(tl):
            await speak("Contrato electrónico con validez legal y justificantes.")
            await speak("Todo el proceso es online y trazable."); return True
        if PROCESS_RE.search(tl):
            await speak("Pasos: solicitud, verificación, contrato digital y entrada.")
            await speak("Le guiamos en cada paso y resolvemos dudas."); return True
        if DOCS_RE.search(tl):
            await speak("Inquilino: DNI o pasaporte y teléfono verificado.")
            await speak("Podemos pedir referencias si procede."); return True
        return False
    async def finish_normal():
        lead=session["lead"].copy()
        await speak("Gracias. Tomamos sus datos. Le contactaremos en breve.", interruptible=False)
        url = _env("ASSIGN_URL","")
        if url:
            try:
                import urllib.request
                req = urllib.request.Request(url, data=json.dumps(lead, ensure_ascii=False).encode("utf-8"),
                                             headers={"Content-Type":"application/json"})
                with urllib.request.urlopen(req, timeout=2.0) as r: _ = r.read()
            except Exception: pass
        print("<<LEAD>>"+json.dumps(lead, ensure_ascii=False)+"<<END>>", flush=True)
        session["step"]="post"  # no preguntar en post
    async def finish_franchise(mode: str):
        if mode=="prospect":
            payload = {"lead_type":"franchise_prospect","nombre":session["lead"].get("nombre",""),
                       "telefono":session["lead"].get("telefono",""),"ciudad":session["lead"].get("poblacion",""),
                       "zona":session["lead"].get("zona",""),"experiencia":""}
            url = _env("ASSIGN_URL_EXPANSION", _env("ASSIGN_URL",""))
            await speak("Gracias. Expansión le llamará en 24–48 horas.", interruptible=False)
        else:
            payload = {"lead_type":"franchisee_support","nombre":session["lead"].get("nombre",""),
                       "zona":session["lead"].get("zona",""),"email_corp":"",
                       "telefono":session["lead"].get("telefono",""),"categoria":session.get("fr_categoria",""),
                       "detalle":session.get("fr_detalle",""),"prioridad":"media"}
            url = _env("ASSIGN_URL_SUPPORT", _env("ASSIGN_URL",""))
            await speak("Gracias. Soporte franquiciados registra su caso hoy.", interruptible=False)
        if url:
            try:
                import urllib.request
                req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                             headers={"Content-Type":"application/json"})
                with urllib.request.urlopen(req, timeout=2.0) as r: _ = r.read()
            except Exception: pass
        print("<<LEAD>>"+json.dumps(payload, ensure_ascii=False)+"<<END>>", flush=True)
        session["step"]="post"
    async def handle(txt: str):
        t=_norm(txt); tl=t.lower(); now=_now()
        if session["last_user"]==tl and (now-session["last_user_ts"])<COOLDOWN_MS: return
        session["last_user"]=tl; session["last_user_ts"]=now
        s=session["step"]; lead=session["lead"]
        FR = re.compile(r"\b(franquic|royalty|licencia|territor|expansi[oó]n|soy franquiciado|mi zona)\b", re.I)
        if FR.search(tl) and session.get("fr_mode") is None:
            session["fr_mode"]="ask"; await speak("¿Es franquiciado actual o desea información de franquicia?"); return
        if HELP_RE.search(tl) and session.get("fr_mode") is None:
            if not lead.get("telefono"):
                session["step"]="phone"; await speak("Para ayudarle ahora, ¿su teléfono de contacto?"); return
            await speak(f"De acuerdo. Un asesor le llamará al {lead['telefono']} en breve."); session["step"]="post"; return
        if session.get("fr_mode") is None and (INFO_RE.search(tl) or PRICES_RE.search(tl) or CONTRACT_RE.search(tl) or PROCESS_RE.search(tl) or DOCS_RE.search(tl)):
            if not await info_topic(tl): await info_general(); return
        if session.get("fr_mode"):
            mode=session["fr_mode"]
            if mode=="ask":
                if "actual" in tl or "soy franquiciado" in tl:
                    session["fr_mode"]="support"; session["step"]="fr_cat"; await speak("Motivo: pagos, contratos, CRM, operativa o incidencias?"); return
                if "informaci" in tl or "ser franquiciado" in tl or "abrir" in tl:
                    session["fr_mode"]="prospect"; session["step"]="fr_city"; await speak("¿En qué ciudad o zona desea operar?"); return
                await speak("¿Es franquiciado actual o desea información de franquicia?"); return
            if mode=="prospect":
                if s=="fr_city":
                    if len(tl)>=2: lead["poblacion"]=t.title(); session["step"]="zone"; await speak("¿Qué zona o barrio?"); return
                    await speak("¿En qué ciudad o zona desea operar?"); return
                if s=="zone":
                    if len(tl)>=2: lead["zona"]=t.title(); session["step"]="name"; await speak("¿Su nombre completo?"); return
                    await speak("¿Qué zona o barrio?"); return
                if s=="name":
                    if len(t.split())>=2: lead["nombre"]=t; session["step"]="phone"; await speak("¿Su teléfono de contacto?"); return
                    await speak("¿Su nombre completo, por favor?"); return
                if s=="phone":
                    d="".join(ch for ch in t if ch.isdigit())
                    if d.startswith("34") and len(d)>=11: d=d[-9:]
                    if len(d)==9 and d[0] in "6789": lead["telefono"]=d; session["step"]="fr_exp"; await speak("¿Tiene experiencia inmobiliaria u operativa?"); return
                    await speak("¿Me facilita un teléfono de nueve dígitos?"); return
                if s=="fr_exp": session["fr_detalle"]=t; await finish_franchise("prospect"); return
            if mode=="support":
                if s=="fr_cat":
                    cats=["pagos","contratos","crm","operativa","incidenc"]
                    for c in cats:
                        if c in tl:
                            session["fr_categoria"]="pagos" if "pago" in tl else ("contratos" if "contrato" in tl else ("crm" if "crm" in tl else ("operativa" if "operat" in tl else "incidencia")))
                            break
                    session["step"]="fr_det"; await speak("¿Detalle breve del caso?"); return
                if s=="fr_det": session["fr_detalle"]=t; 
                if s=="fr_det" and not lead.get("telefono"): session["step"]="phone"; await speak("¿Su teléfono de contacto?"); return
                if s=="fr_det" and lead.get("telefono"): await finish_franchise("support"); return
                if s=="phone":
                    d="".join(ch for ch in t if ch.isdigit())
                    if d.startswith("34") and len(d)>=11: d=d[-9:]
                    if len(d)>=9: lead["telefono"]=d; await finish_franchise("support"); return
                    await speak("¿Me facilita un teléfono de nueve dígitos?"); return
            await speak("¿Es franquiciado actual o desea información de franquicia?"); return
        # 5 campos
        if s=="role":
            if "propiet" in tl: lead["role"]="propietario"; session["step"]="city"; await speak("Gracias."); await speak("¿En qué población está interesado?"); return
            if "inquil" in tl or "alquil" in tl: lead["role"]="inquilino"; session["step"]="city"; await speak("Gracias."); await speak("¿En qué población está interesado?"); return
            await speak("Para atenderle: ¿Es usted propietario o inquilino?"); return
        if s=="city":
            if len(tl)>=2: lead["poblacion"]=t.title(); session["step"]="zone"; await speak("¿Qué zona o barrio?"); return
            await speak("¿En qué población está interesado?"); return
        if s=="zone":
            if len(tl)>=2: lead["zona"]=t.title(); session["step"]="name"; await speak("¿Su nombre completo?"); return
            await speak("¿Qué zona o barrio?"); return
        if s=="name":
            if len(t.split())>=2: lead["nombre"]=t; session["step"]="phone"; await speak("¿Su teléfono de contacto, por favor?"); return
            await speak("¿Su nombre completo, por favor?"); return
        if s=="phone":
            d="".join(ch for ch in t if ch.isdigit())
            if d.startswith("34") and len(d)>=11: d=d[-9:]
            if len(d)==9 and d[0] in "6789": lead["telefono"]=d; await finish_normal(); return
            await speak("¿Me facilita un teléfono de nueve dígitos?"); return
        if s=="post":
            return

@app.post("/assign")
async def assign(payload: dict):
    zone_key = f"{(payload.get('poblacion') or payload.get('ciudad','') or '').strip().lower()}-{(payload.get('zona','') or '').strip().lower()}"
    fid = hashlib.sha1(zone_key.encode("utf-8")).hexdigest()[:10]
    task = {"title":"Contactar lead","zone_key":zone_key,"franchisee_id":fid,"lead":payload,"created_at":int(time.time())}
    return JSONResponse({"ok": True, "task": task})
