
# SpainRoom — Voice Backend (ConversationRelay) — ES STABLE (Ayuda en post + De-dup)
# Start: uvicorn codigo_flask:app --host 0.0.0.0 --port $PORT --proxy-headers

import os, json, re, time, contextlib, hashlib
from typing import Dict, Any
from fastapi import FastAPI, Request, WebSocket, Header
from fastapi.responses import Response, JSONResponse, HTMLResponse
from xml.sax.saxutils import quoteattr

app = FastAPI(title="SpainRoom Voice — ConversationRelay (ES STABLE, Ayuda)")

def _twiml(xml: str) -> Response:
    return Response(content=xml, media_type="application/xml")

def _env(k: str, default: str = "") -> str:
    return os.getenv(k, default)

def _normalize_ws_host(request: Request) -> str:
    return request.headers.get("host") or request.url.hostname or "localhost"

async def _post_json(url: str, payload: dict, timeout: float = 2.0) -> None:
    import urllib.request
    try:
        req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            _ = r.read()
    except Exception:
        pass

def _digits(t: str) -> str:
    return "".join(ch for ch in (t or "") if ch.isdigit())

@app.get("/")
async def root(request: Request):
    host = _normalize_ws_host(request)
    ws_url = f"wss://{host}/cr"
    html = (
        "<h2>SpainRoom Voice — ConversationRelay (ES)</h2>\n"
        "<p>Voice URL: <code>/voice/answer_cr</code></p>\n"
        f"<p>WebSocket CR: <code>{ws_url}</code></p>\n"
        "<p>Health: <code>/health</code> · Diag: <code>/diag_runtime</code></p>"
    )
    return HTMLResponse(html)

@app.get("/health")
async def health():
    return JSONResponse({"ok": True})

@app.get("/diag_runtime")
async def diag_runtime():
    keys = ["CR_TTS_PROVIDER","CR_LANGUAGE","CR_TRANSCRIPTION_LANGUAGE","CR_VOICE",
            "CR_WELCOME","SPEAK_SLEEP_MS","ASSIGN_URL"]
    return JSONResponse({k: _env(k) for k in keys})

@app.api_route("/voice/answer_cr", methods=["GET","POST"])
async def answer_cr(request: Request):
    host = _normalize_ws_host(request)
    ws_url = f"wss://{host}/cr"
    lang        = _env("CR_LANGUAGE", "es-ES")
    trans_lang  = _env("CR_TRANSCRIPTION_LANGUAGE", lang)
    tts_provider= _env("CR_TTS_PROVIDER", "Google")
    tts_voice   = _env("CR_VOICE", "")
    welcome     = _env("CR_WELCOME", "")  # mejor vacío para no duplicar
    attrs = [
        f"url={quoteattr(ws_url)}",
        f"language={quoteattr(lang)}",
        f"transcriptionLanguage={quoteattr(trans_lang)}",
        f"ttsProvider={quoteattr(tts_provider)}",
        'interruptible="speech"',
        'reportInputDuringAgentSpeech="none"',
    ]
    if welcome.strip():
        attrs.append(f"welcomeGreeting={quoteattr(welcome.strip())}")
    if tts_voice:
        attrs.append(f"voice={quoteattr(tts_voice)}")
    twiml = '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n  <Connect>\n    <ConversationRelay %s />\n  </Connect>\n</Response>' % (" ".join(attrs))
    return _twiml(twiml)

@app.post("/voice/fallback")
async def voice_fallback():
    return _twiml('<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n  <Say language="es-ES">Disculpe. Estamos teniendo problemas. Inténtelo más tarde.</Say>\n</Response>')

# ---------------- Conocimiento SpainRoom + De-dup ----------------
KNOWLEDGE: Dict[str, Dict[str, Any]] = {
    "que_hace": {
        "patterns": ["que hac","qué hac","quienes sois","qué es spainroom","que es spainroom"],
        "answers": [
            "SpainRoom alquila habitaciones de medio y largo plazo. No somos hotel.",
            "Intermediamos, validamos documentación y firmamos digitalmente para su seguridad."
        ]
    },
    "minimo_precios": {
        "patterns": ["minimo","mínimo","precio","precios","tarifa"],
        "answers": [
            "La estancia mínima es de un mes. El precio depende de la habitación y la zona.",
            "Le ayudamos a comparar opciones disponibles en su ciudad."
        ]
    },
    "documentos": {
        "patterns": ["document","dni","pasaporte","requisitos"],
        "answers": [
            "Para inquilinos: DNI o pasaporte y comprobante del teléfono declarado.",
            "La firma es electrónica y guardamos justificantes para su tranquilidad."
        ]
    },
    "proceso": {
        "patterns": ["proceso","como func","cómo func","pasos"],
        "answers": [
            "El proceso es simple: solicitud → verificación → contrato digital → entrada.",
            "Le guiamos en cada paso y resolvemos dudas en el momento."
        ]
    },
    "pagos": {
        "patterns": ["pago","stripe","cobro","tarjeta"],
        "answers": [
            "Los pagos son seguros con Stripe. La plataforma cobra y gestiona las transferencias.",
            "Propietarios y franquiciados reciben sus pagos según la política acordada."
        ]
    },
    "propietarios": {
        "patterns": ["propietar","duen","dueñ","propiedad"],
        "answers": [
            "Para propietarios: publicamos, filtramos inquilinos, hacemos contrato y cobramos.",
            "Requisitos básicos: cerradura, cama 135×200 y buen estado."
        ]
    },
    "soporte": {
        "patterns": ["soporte","ayuda","contacto","telefono","teléfono","llamar","asesor"],
        "answers": [
            "Tiene soporte durante la estancia por chat y teléfono.",
            "Si quiere, tomamos sus datos y le llama un asesor."
        ]
    },
    "contrato": {
        "patterns": ["contrato","logalty","firma"],
        "answers": [
            "Los contratos se firman digitalmente con plena validez legal.",
            "Guardamos los justificantes para auditoría y tranquilidad de ambas partes."
        ]
    }
}

@app.websocket("/cr")
async def conversation_relay(ws: WebSocket):
    await ws.accept()

    session: Dict[str, Any] = {
        "step": "await_setup",
        "lead": {"role":"","poblacion":"","zona":"","nombre":"","telefono":""},
        "last_q": None, "last_q_ts": 0.0,
        "last_user": None, "last_user_ts": 0.0,
        "info_hits": {k:0 for k in KNOWLEDGE.keys()}
    }

    def _now_ms() -> float: return time.monotonic()*1000.0

    async def speak(text: str, interruptible: bool = True):
        await ws.send_json({"type":"text","token":text,"last":True,"interruptible":bool(interruptible)})
        try:
            import asyncio
            await asyncio.sleep(int(_env("SPEAK_SLEEP_MS","0"))/1000.0)
        except Exception:
            pass

    def _norm(t: str) -> str: return re.sub(r"\s+"," ",(t or "").strip())

    def _dup_user(t: str) -> bool:
        t=_norm(t).lower(); now=_now_ms()
        if session["last_user"] == t and (now - session["last_user_ts"]) < 1200: return True
        session["last_user"]=t; session["last_user_ts"]=now; return False

    async def ask_once(step_key: str):
        now=_now_ms()
        if session["last_q"] == step_key and (now - session["last_q_ts"]) < 1200: return
        session["last_q"]=step_key; session["last_q_ts"]=now
        prompts={
            "role":"Para atenderle: ¿Es usted propietario o inquilino?",
            "city":"¿En qué población está interesado?",
            "zone":"¿Qué zona o barrio?",
            "name":"¿Su nombre completo?",
            "phone":"¿Su teléfono de contacto, por favor?",
            "post":"¿Desea más información o ayuda?"
        }
        await speak(prompts.get(step_key,""))

    def _match_topic(tl: str):
        for k, cfg in KNOWLEDGE.items():
            for pat in cfg["patterns"]:
                if pat in tl:
                    return k
        return None

    def _is_yes_help(tl: str) -> bool:
        tl=tl.lower()
        yes_words = ["ayuda","asesor","llamar","llamada","contacto","sí","si ","quiero","necesito","por favor"]
        return any(w in tl for w in yes_words)

    async def answer_topic(topic: str):
        idx = session["info_hits"].get(topic,0)
        answers = KNOWLEDGE[topic]["answers"]
        text = answers[idx % len(answers)]
        session["info_hits"][topic] = idx + 1
        await speak(text)

    async def finish():
        lead=session["lead"].copy()
        await speak("Gracias. Tomamos sus datos. Le contactaremos en breve.", interruptible=False)
        au=_env("ASSIGN_URL","")
        if au:
            try: await _post_json(au, lead, timeout=2.0)
            except Exception: pass
        print("<<LEAD>>"+json.dumps(lead, ensure_ascii=False)+"<<END>>", flush=True)
        session["step"]="post"; await ask_once("post")

    async def handle_text(user_text: str):
        if _dup_user(user_text): return
        t=_norm(user_text); tl=t.lower(); s=session["step"]; lead=session["lead"]

        # Ayuda explícita en cualquier momento
        if _is_yes_help(tl):
            # Si no hay teléfono, pedirlo y saltar a captura
            if not lead.get("telefono"):
                session["step"]="phone"
                await speak("Perfecto. Para ayudarle ahora mismo, ¿su teléfono de contacto?")
                return
            else:
                # Ya tenemos teléfono: confirmamos ayuda y cerramos
                await speak(f"De acuerdo. Un asesor le llamará al {lead['telefono']} en breve.")
                session["step"]="post"
                await ask_once("post")
                return

        topic = _match_topic(tl)
        if topic:
            await answer_topic(topic)
            if s != "await_setup":
                await ask_once(s)
            return

        if s=="post":
            # Respuestas genéricas en post si no pide ayuda ni info
            await speak("¿Quiere que le llame un asesor? Si es así, dígame 'ayuda'.")
            return

        if s=="role":
            if "propiet" in tl:
                lead["role"]="propietario"; session["step"]="city"; await speak("Gracias."); await ask_once("city")
            elif "inquil" in tl or "alquil" in tl:
                lead["role"]="inquilino";  session["step"]="city"; await speak("Gracias."); await ask_once("city")
            else:
                await ask_once("role")

        elif s=="city":
            if len(tl)>=2: lead["poblacion"]=t.title(); session["step"]="zone"; await ask_once("zone")
            else: await ask_once("city")

        elif s=="zone":
            if len(tl)>=2: lead["zona"]=t.title(); session["step"]="name"; await ask_once("name")
            else: await ask_once("zone")

        elif s=="name":
            if len(t.split())>=2: lead["nombre"]=t; session["step"]="phone"; await ask_once("phone")
            else: await speak("¿Su nombre completo, por favor?")

        elif s=="phone":
            d=_digits(t); 
            if d.startswith("34") and len(d)>=11: d=d[-9:]
            if len(d)==9 and d[0] in "6789": lead["telefono"]=d; await finish()
            else: await speak("¿Me facilita un teléfono de nueve dígitos?")

        elif s=="await_setup":
            pass

    try:
        while True:
            msg = await ws.receive_json()
            tp = msg.get("type")
            if tp=="setup":
                session["step"]="role"; await ask_once("role")
            elif tp=="prompt":
                txt = msg.get("voicePrompt","") or ""
                if msg.get("last", True) and txt: await handle_text(txt)
            elif tp=="interrupt":
                await ask_once(session["step"])
            elif tp=="dtmf":
                pass
            elif tp=="error":
                await speak("Disculpe. Estamos teniendo problemas. Inténtelo más tarde.", interruptible=False); break
    except Exception as e:
        print("CR ws error:", e, flush=True)
    finally:
        with contextlib.suppress(Exception):
            await ws.close()

@app.post("/assign")
async def assign(payload: dict):
    zone_key = f"{(payload.get('poblacion') or '').strip().lower()}-{(payload.get('zona') or '').strip().lower()}"
    fid = hashlib.sha1(zone_key.encode("utf-8")).hexdigest()[:10]
    task = {"title":"Contactar lead","zone_key":zone_key,"franchisee_id":fid,"lead":payload,"created_at":int(time.time())}
    return JSONResponse({"ok": True, "task": task})
