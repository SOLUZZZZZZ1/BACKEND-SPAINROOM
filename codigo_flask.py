
# SpainRoom — Voice Backend (ConversationRelay) — ES STABLE (Info x3 + Urgencia + Goodbye)
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
            "CR_WELCOME","SPEAK_SLEEP_MS","COOLDOWN_MS","MIN_TTS_GAP_MS","ASSIGN_URL"]
    return JSONResponse({k: _env(k) for k in keys})


@app.api_route("/voice/answer_cr", methods=["GET","POST"])
async def answer_cr(request: Request):
    host = _host(request)
    ws = f"wss://{host}/cr"
    lang = _env("CR_LANGUAGE","es-ES"); tr=_env("CR_TRANSCRIPTION_LANGUAGE",lang)
    prov=_env("CR_TTS_PROVIDER","Google"); voice=_env("CR_VOICE","es-ES-Standard-A")
    welcome=_env("CR_WELCOME","Bienvenido a SpainRoom.")
    # Sin barge-in: máxima estabilidad
    attrs = [f"url={quoteattr(ws)}", f"language={quoteattr(lang)}", f"transcriptionLanguage={quoteattr(tr)}",
             f"ttsProvider={quoteattr(prov)}", 'interruptible="speech"', 'reportInputDuringAgentSpeech="none"']
    if welcome.strip(): attrs.append(f"welcomeGreeting={quoteattr(welcome.strip())}")
    if voice.strip():   attrs.append(f"voice={quoteattr(voice.strip())}")
    twiml = '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n  <Connect>\n    <ConversationRelay %s />\n  </Connect>\n</Response>' % (" ".join(attrs))
    return _twiml(twiml)


@app.websocket("/cr")
async def cr(ws: WebSocket):
    await ws.accept()
    COOLDOWN_MS    = int(_env("COOLDOWN_MS","2200"))
    MIN_TTS_GAP_MS = int(_env("MIN_TTS_GAP_MS","0"))   # 0 → permite encadenar líneas seguidas
    SPEAK_SLEEP_MS = int(_env("SPEAK_SLEEP_MS","0"))

    session: Dict[str, Any] = {
        "step": "await_setup",
        "lead": {"role":"","poblacion":"","zona":"","nombre":"","telefono":""},
        "last_q": None, "last_q_ts": 0.0,
        "last_user": None, "last_user_ts": 0.0,
        "last_tts_ts": 0.0,
        "help_pending": False,
        "urgencia": ""  # inmediata | hoy | mañana | sin_prisa
    }

    def _now(): return time.monotonic()*1000.0
    def _norm(t): return re.sub(r"\s+"," ", (t or "").strip())

    async def speak(txt, interruptible=True):
        now=_now()
        if (now - session["last_tts_ts"]) < MIN_TTS_GAP_MS:
            return
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
        prompts = {
            "role":"Para atenderle: ¿Es usted propietario o inquilino?",
            "city":"¿En qué población está interesado?",
            "zone":"¿Qué zona o barrio?",
            "name":"¿Su nombre completo?",
            "phone":"¿Su teléfono de contacto, por favor?"
        }
        if key in prompts: await speak(prompts[key])

    async def ask_urgency():
        await speak("¿Nivel de urgencia del contacto? Inmediata, hoy, mañana o sin prisa.")

    # Intents
    HELP_RE  = re.compile(r"\b(ayuda|asesor|llamar|contacto|tel[eé]fono)\b", re.I)
    INFO_RE  = re.compile(r"\b(info|informaci[oó]n|m[aá]s info|saber m[aá]s|cu[ée]ntame|dime|explica|detalles)\b", re.I)
    PRICE_RE = re.compile(r"\b(precio|precios|tarifa|coste|costos)\b", re.I)
    CONT_RE  = re.compile(r"\b(contrato|firm[ao]|logalty)\b", re.I)
    PROC_RE  = re.compile(r"\b(proceso|pasos|c[oó]mo func|como func)\b", re.I)
    DOCS_RE  = re.compile(r"\b(document|dni|pasaporte|papeles)\b", re.I)

    async def info_general():
        await speak("SpainRoom alquila habitaciones de medio y largo plazo.")
        await speak("No somos hotel ni turismo; convivencia segura y responsable.")
        await speak("Proceso: solicitud, verificación, contrato digital y entrada.")

    async def info_topic(tl: str) -> bool:
        if PRICE_RE.search(tl):
            await speak("El precio depende de ciudad y habitación; mínimo un mes.")
            await speak("Gastos según vivienda; te damos opciones comparadas.")
            await speak("Ajustamos al presupuesto y zona que prefiera.")
            return True
        if CONT_RE.search(tl):
            await speak("Contrato electrónico con validez legal y justificantes.")
            await speak("Cláusulas claras: uso de espacios, visitas y depósito.")
            await speak("Todo online; trazabilidad y copia para ambas partes.")
            return True
        if PROC_RE.search(tl):
            await speak("Pasos: solicitud, verificación, contrato digital y entrada.")
            await speak("Revisión de requisitos y match por ciudad y zona.")
            await speak("Soporte cercano; incidencias por teléfono y email.")
            return True
        if DOCS_RE.search(tl):
            await speak("Inquilino: DNI o pasaporte y teléfono verificado.")
            await speak("Podemos pedir referencias según el caso y la vivienda.")
            await speak("Propietario: DNI y checklist básico del hogar.")
            return True
        return False

    def _norm_urg(tl: str) -> str:
        tl = tl.lower()
        if "inmedi" in tl or "ahora" in tl or "urgent" in tl: return "inmediata"
        if "hoy" in tl: return "hoy"
        if "mañ" in tl or "manana" in tl: return "mañana"
        if "sin prisa" in tl or "cuando puedas" in tl: return "sin_prisa"
        # por defecto, hoy si no está claro
        return "hoy"

    async def finish_lead(origen="lead"):
        lead=session["lead"].copy()
        if session.get("urgencia"): lead["urgencia"]=session["urgencia"]
        if origen=="lead":
            await speak("Gracias. Tomamos sus datos. Le contactaremos en breve.", interruptible=False)
        elif origen=="help":
            texto = {
                "inmediata": "Gracias. Le llamamos de inmediato.",
                "hoy": "Gracias. Le llamamos hoy.",
                "mañana": "Gracias. Le llamamos mañana.",
                "sin_prisa": "Gracias. Le llamamos en breve, sin prisa."
            }[lead.get("urgencia","hoy")]
            await speak(texto, interruptible=False)

        url=_env("ASSIGN_URL","")
        if url:
            try:
                import urllib.request
                req=urllib.request.Request(url, data=json.dumps(lead, ensure_ascii=False).encode("utf-8"),
                                           headers={"Content-Type":"application/json"})
                with urllib.request.urlopen(req, timeout=2.0) as r: _=r.read()
            except Exception: pass
        print("<<LEAD>>"+json.dumps(lead, ensure_ascii=False)+"<<END>>", flush=True)
        session["step"]="post"  # no repreguntar en post

    async def handle(txt: str):
        t=_norm(txt); tl=t.lower()
        if _dup_user(t): return
        s=session["step"]; lead=session["lead"]

        # Escalado ayuda → pedir urgencia
        if HELP_RE.search(tl) and not session["help_pending"]:
            session["help_pending"]=True
            if not lead.get("telefono"):
                session["step"]="phone"; await speak("Para ayudarle ahora, ¿su teléfono de contacto?"); return
            session["step"]="urgency"; await ask_urgency(); return

        # Estado 'urgency': guardar y cerrar con finish_lead('help')
        if s=="urgency":
            session["urgencia"]=_norm_urg(tl)
            await finish_lead(origen="help"); return

        # Si acabamos de pedir teléfono por ayuda, al recibirlo ir a urgencia
        if s=="phone" and session["help_pending"]:
            d="".join(ch for ch in t if ch.isdigit())
            if d.startswith("34") and len(d)>=11: d=d[-9:]
            if len(d)>=9: lead["telefono"]=d; session["step"]="urgency"; await ask_urgency(); return
            await speak("¿Me facilita un teléfono de nueve dígitos?"); return

        # Información (dos o tres líneas). Si ya estamos en post → despedida y colgar.
        if INFO_RE.search(tl):
            in_post = (s=="post")
            if not await info_topic(tl):
                await info_general()
            if in_post:
                await speak("Gracias por llamar a SpainRoom. ¡Hasta pronto!", interruptible=False)
                try:
                    await ws.send_json({"type":"end","handoffData":"{\"reason\":\"goodbye\"}"})
                except Exception: pass
            return

        # 5 campos estándar
        if s=="role":
            if "propiet" in tl: lead["role"]="propietario"; session["step"]="city"; await speak("Gracias."); await ask_once("city"); return
            if "inquil" in tl or "alquil" in tl: lead["role"]="inquilino"; session["step"]="city"; await speak("Gracias."); await ask_once("city"); return
            await ask_once("role"); return
        if s=="city":
            if len(tl)>=2: lead["poblacion"]=t.title(); session["step"]="zone"; await ask_once("zone"); return
            await ask_once("city"); return
        if s=="zone":
            if len(tl)>=2: lead["zona"]=t.title(); session["step"]="name"; await ask_once("name"); return
            await ask_once("zone"); return
        if s=="name":
            if len(t.split())>=2: lead["nombre"]=t; session["step"]="phone"; await ask_once("phone"); return
            await speak("¿Su nombre completo, por favor?"); return
        if s=="phone":
            d="".join(ch for ch in t if ch.isdigit())
            if d.startswith("34") and len(d)>=11: d=d[-9:]
            if len(d)==9 and d[0] in "6789": lead["telefono"]=d; await finish_lead(); return
            await speak("¿Me facilita un teléfono de nueve dígitos?"); return
        if s=="post":
            # Silencio en post; a la espera de 'información' o 'ayuda'
            return

    # Event loop
    try:
        while True:
            msg = await ws.receive_json()
            tp = msg.get("type")
            if tp=="setup":
                session["step"]="role"; await ask_once("role")
            elif tp=="prompt":
                if msg.get("last", True):
                    await handle(msg.get("voicePrompt","") or "")
            elif tp=="interrupt":
                session["last_tts_ts"]=0.0
            elif tp=="error":
                await ws.send_json({"type":"text","token":"Disculpe. Estamos teniendo problemas.","last":True,"interruptible":False}); break
    except Exception as e:
        print("CR ws error:", e, flush=True)
    finally:
        with contextlib.suppress(Exception): await ws.close()

# /assign passthrough
@app.post("/assign")
async def assign(payload: dict):
    zone_key = f"{(payload.get('poblacion') or payload.get('ciudad','') or '').strip().lower()}-{(payload.get('zona','') or '').strip().lower()}"
    fid = hashlib.sha1(zone_key.encode("utf-8")).hexdigest()[:10]
    task = {"title":"Contactar lead","zone_key":zone_key,"franchisee_id":fid,"lead":payload,"created_at":int(time.time())}
    return JSONResponse({"ok": True, "task": task})
