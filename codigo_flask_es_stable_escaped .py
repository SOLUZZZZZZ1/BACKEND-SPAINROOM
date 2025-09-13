
# SpainRoom — Voice Backend (ConversationRelay) — ES STABLE (TwiML Escaped, Fixed)
# FastAPI app para Twilio Voice usando <ConversationRelay> (STT+TTS por Twilio)
# - Español solo. Espera 'setup' antes de hablar.
# - TwiML con atributos ESCAPADOS (evita errores de XML por comillas/símbolos).
# - FSM 5 campos (rol, población, zona, nombre, teléfono) + respuestas de información.
# Endpoints: /voice/answer_cr · /voice/fallback · WS /cr · /assign · /health · /diag_runtime
# Ejecutar: uvicorn codigo_flask_es_stable_escaped:app --host 0.0.0.0 --port $PORT --proxy-headers

import os, json, re, time, contextlib, hashlib
from typing import Dict, Any
from fastapi import FastAPI, Request, WebSocket, Header
from fastapi.responses import Response, JSONResponse, HTMLResponse
from xml.sax.saxutils import quoteattr

app = FastAPI(title="SpainRoom Voice — ConversationRelay (ES, Escaped TwiML)")

def _twiml(xml: str) -> Response:
    return Response(content=xml, media_type="application/xml")

def _env(k: str, default: str = "") -> str:
    return os.getenv(k, default)

def _normalize_ws_host(request: Request) -> str:
    return request.headers.get("host") or request.url.hostname or "localhost"

async def _post_json(url: str, payload: dict, timeout: float = 2.0) -> None:
    import urllib.request
    try:
        req = urllib.request.Request(url,
                                     data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
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
        "<h2>SpainRoom Voice — ConversationRelay (ES, Escaped TwiML)</h2>\n"
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
            "CR_WELCOME","SPEAK_SLEEP_MS","ASSIGN_URL","CI_SERVICE_SID"]
    return JSONResponse({k: _env(k) for k in keys})

# TwiML ConversationRelay (saludo configurable y atributos escapados)
@app.api_route("/voice/answer_cr", methods=["GET","POST"])
async def answer_cr(request: Request):
    host = _normalize_ws_host(request)
    ws_url = f"wss://{host}/cr"
    lang        = _env("CR_LANGUAGE", "es-ES")
    trans_lang  = _env("CR_TRANSCRIPTION_LANGUAGE", lang)
    tts_provider= _env("CR_TTS_PROVIDER", "Google")
    tts_voice   = _env("CR_VOICE", "")
    ci_sid      = _env("CI_SERVICE_SID", "")
    welcome     = _env("CR_WELCOME", "Para atenderle: ¿Es usted propietario o inquilino?")

    # Atributos escapados (quoteattr añade comillas y escapa &, <, >, ")
    attrs = [
        f"url={quoteattr(ws_url)}",
        f"language={quoteattr(lang)}",
        f"transcriptionLanguage={quoteattr(trans_lang)}",
        f"ttsProvider={quoteattr(tts_provider)}",
        
# SpainRoom — Voice Backend (ConversationRelay) — ES STABLE (TwiML Escaped, Fixed)
# FastAPI app para Twilio Voice usando <ConversationRelay> (STT+TTS por Twilio)
# - Español solo. Espera 'setup' antes de hablar.
# - TwiML con atributos ESCAPADOS (evita errores de XML por comillas/símbolos).
# - FSM 5 campos (rol, población, zona, nombre, teléfono) + respuestas de información.
# Endpoints: /voice/answer_cr · /voice/fallback · WS /cr · /assign · /health · /diag_runtime
# Ejecutar: uvicorn codigo_flask_es_stable_escaped:app --host 0.0.0.0 --port $PORT --proxy-headers

import os, json, re, time, contextlib, hashlib
from typing import Dict, Any
from fastapi import FastAPI, Request, WebSocket, Header
from fastapi.responses import Response, JSONResponse, HTMLResponse
from xml.sax.saxutils import quoteattr

app = FastAPI(title="SpainRoom Voice — ConversationRelay (ES, Escaped TwiML)")

def _twiml(xml: str) -> Response:
    return Response(content=xml, media_type="application/xml")

def _env(k: str, default: str = "") -> str:
    return os.getenv(k, default)

def _normalize_ws_host(request: Request) -> str:
    return request.headers.get("host") or request.url.hostname or "localhost"

async def _post_json(url: str, payload: dict, timeout: float = 2.0) -> None:
    import urllib.request
    try:
        req = urllib.request.Request(url,
                                     data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
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
        "<h2>SpainRoom Voice — ConversationRelay (ES, Escaped TwiML)</h2>\n"
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
            "CR_WELCOME","SPEAK_SLEEP_MS","ASSIGN_URL","CI_SERVICE_SID"]
    return JSONResponse({k: _env(k) for k in keys})

# TwiML ConversationRelay (saludo configurable y atributos escapados)
@app.api_route("/voice/answer_cr", methods=["GET","POST"])
async def answer_cr(request: Request):
    host = _normalize_ws_host(request)
    ws_url = f"wss://{host}/cr"
    lang        = _env("CR_LANGUAGE", "es-ES")
    trans_lang  = _env("CR_TRANSCRIPTION_LANGUAGE", lang)
    tts_provider= _env("CR_TTS_PROVIDER", "Google")
    tts_voice   = _env("CR_VOICE", "")
    ci_sid      = _env("CI_SERVICE_SID", "")
    welcome     = _env("CR_WELCOME", "Para atenderle: ¿Es usted propietario o inquilino?")

    # Atributos escapados (quoteattr añade comillas y escapa &, <, >, ")
    attrs = [
        f"url={quoteattr(ws_url)}",
        f"language={quoteattr(lang)}",
        f"transcriptionLanguage={quoteattr(trans_lang)}",
        f"ttsProvider={quoteattr(tts_provider)}",
        "interruptible="speech"", 
        'reportInputDuringAgentSpeech="none"',
    ]
    if welcome.strip():
        attrs.append(f"welcomeGreeting={quoteattr(welcome.strip())}")
    if tts_voice:
        attrs.append(f"voice={quoteattr(tts_voice)}")
    if ci_sid:
        attrs.append(f"intelligenceService={quoteattr(ci_sid)}")

    twiml = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<Response>\n  <Connect>\n    <ConversationRelay %s />\n  </Connect>\n</Response>" % (" ".join(attrs))
    return _twiml(twiml)

@app.post("/voice/fallback")
async def voice_fallback():
    return _twiml("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<Response>\n  <Say language=\"es-ES\">Disculpe. Estamos teniendo problemas. Inténtelo más tarde.</Say>\n</Response>")

# WebSocket ConversationRelay (texto ↔ texto)
@app.websocket("/cr")
async def conversation_relay(ws: WebSocket):
    await ws.accept()

    session: Dict[str, Any] = {"step": "await_setup", "lead": {"role":"","poblacion":"","zona":"","nombre":"","telefono":""}}

    async def speak(text: str, interruptible: bool = True):
        await ws.send_json({"type": "text", "token": text, "last": True, "interruptible": bool(interruptible)})
        try:
            import asyncio
            await asyncio.sleep(int(_env("SPEAK_SLEEP_MS","0"))/1000.0)
        except Exception:
            pass


        'reportInputDuringAgentSpeech="none"',
    ]
    if welcome.strip():
        attrs.append(f"welcomeGreeting={quoteattr(welcome.strip())}")
    if tts_voice:
        attrs.append(f"voice={quoteattr(tts_voice)}")
    if ci_sid:
        attrs.append(f"intelligenceService={quoteattr(ci_sid)}")

    twiml = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<Response>\n  <Connect>\n    <ConversationRelay %s />\n  </Connect>\n</Response>" % (" ".join(attrs))
    return _twiml(twiml)

@app.post("/voice/fallback")
async def voice_fallback():
    return _twiml("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<Response>\n  <Say language=\"es-ES\">Disculpe. Estamos teniendo problemas. Inténtelo más tarde.</Say>\n</Response>")

# WebSocket ConversationRelay (texto ↔ texto)
@app.websocket("/cr")
async def conversation_relay(ws: WebSocket):
    await ws.accept()

    session: Dict[str, Any] = {"step": "await_setup", "lead": {"role":"","poblacion":"","zona":"","nombre":"","telefono":""}}

    async def speak(text: str, interruptible: bool = True):
        await ws.send_json({"type": "text", "token": text, "last": True, "interruptible": bool(interruptible)})
        try:
            import asyncio
            await asyncio.sleep(int(_env("SPEAK_SLEEP_MS","0"))/1000.0)
        except Exception:
            pass

    def _norm(t: str) -> str:
        return re.sub(r"\s+", " ", (t or "").strip())

    def _is_no(t: str) -> bool:
        t=t.lower()
        return any(x in t for x in ["no","nada","está bien","esta bien","gracias","todo bien","eso es todo","adiós","adios"])

    def _is_info(t: str) -> bool:
        t=t.lower()
        keys=["qué hac","que hac","informaci","cómo func","como func","quiénes sois","quienes sois",
              "qué es spainroom","que es spainroom","hotel","precio","pago","pagos","contrato","document","mínimo","minimo"]
        return any(k in t for k in keys)

    async def info():
        await speak("SpainRoom alquila habitaciones de medio y largo plazo.")
        await speak("Intermediamos, validamos y firmamos digitalmente.")
        await speak("Pagos seguros con Stripe y soporte cercano.")

    async def ask():
        s=session["step"]
        if   s=="role":  await speak("Para atenderle: ¿Es usted propietario o inquilino?")
        elif s=="city":  await speak("¿En qué población está interesado?")
        elif s=="zone":  await speak("¿Qué zona o barrio?")
        elif s=="name":  await speak("¿Su nombre completo?")
        elif s=="phone": await speak("¿Su teléfono de contacto, por favor?")
        elif s=="post":  await speak("¿Desea más información o ayuda?")

    async def finish():
        lead=session["lead"].copy()
        await speak("Gracias. Tomamos sus datos. Le contactaremos en breve.", interruptible=False)
        au=_env("ASSIGN_URL","")
        if au:
            try:
                await _post_json(au, lead, timeout=2.0)
            except Exception:
                pass
        print("<<LEAD>>"+json.dumps(lead, ensure_ascii=False)+"<<END>>", flush=True)
        session["step"]="post"; await ask()

    async def handle(t: str):
        t_norm=_norm(t); tl=t_norm.lower(); s=session["step"]; lead=session["lead"]

        if _is_info(tl):
            await info()
            if s!="await_setup": await ask()
            return

        if s=="post" and _is_no(tl):
            await speak("Gracias por llamar a SpainRoom. ¡Hasta pronto!", interruptible=False)
            await ws.send_json({"type":"end"})
            return

        if s=="role":
            if "propiet" in tl:
                lead["role"]="propietario"; session["step"]="city";  await speak("Gracias."); await ask()
            elif "inquil" in tl or "alquil" in tl:
                lead["role"]="inquilino";  session["step"]="city";  await speak("Gracias."); await ask()
            else:
                await speak("¿Propietario o inquilino?")

        elif s=="city":
            if len(tl)>=2:
                lead["poblacion"]=t_norm.title(); session["step"]="zone"; await ask()
            else:
                await ask()

        elif s=="zone":
            if len(tl)>=2:
                lead["zona"]=t_norm.title(); session["step"]="name"; await ask()
            else:
                await ask()

        elif s=="name":
            if len(t_norm.split())>=2:
                lead["nombre"]=t_norm; session["step"]="phone"; await ask()
            else:
                await speak("¿Su nombre completo, por favor?")

        elif s=="phone":
            d=_digits(t_norm)
            if d.startswith("34") and len(d)>=11:
                d=d[-9:]
            if len(d)==9 and d[0] in "6789":
                lead["telefono"]=d; await finish()
            else:
                await speak("¿Me facilita un teléfono de nueve dígitos?")

        elif s=="await_setup":
            pass

        elif s=="post":
            await info(); await ask()

    try:
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")

            if mtype=="setup":
                session["step"]="role"; await ask()

            elif mtype=="prompt":
                txt = msg.get("voicePrompt","") or ""
                if msg.get("last", True) and txt:
                    await handle(txt)

            elif mtype=="interrupt":
                await ask()

            elif mtype=="dtmf":
                pass

            elif mtype=="error":
                await speak("Disculpe. Estamos teniendo problemas. Inténtelo más tarde.", interruptible=False)
                break
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
