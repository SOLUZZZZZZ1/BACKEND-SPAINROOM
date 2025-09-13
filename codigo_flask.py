
# ================= SpainRoom — Voice Backend (ConversationRelay) =================
# FastAPI app para Twilio Voice usando <ConversationRelay> (STT+TTS gestionados por Twilio)
# - Captura 5 campos: ROL, POBLACIÓN, ZONA, NOMBRE, TELÉFONO
# - Emite LEAD y lo envía a /assign si ASSIGN_URL está configurado
# - Endpoints de salud y diagnóstico de ENV
# Ejecuta con: uvicorn codigo_flask:app --host 0.0.0.0 --port $PORT --proxy-headers
# ================================================================================

import os, json, re, time, contextlib, hashlib
from typing import Dict, Any

from fastapi import FastAPI, Request, WebSocket, Header
from fastapi.responses import Response, JSONResponse, HTMLResponse

# -----------------------------
# Aplicación
# -----------------------------
app = FastAPI(title="SpainRoom Voice — ConversationRelay")

# -----------------------------
# Utilidades
# -----------------------------
def _twiml(xml: str) -> Response:
    return Response(content=xml, media_type="application/xml")

def _env(k: str, default: str = "") -> str:
    return os.getenv(k, default)

def _normalize_ws_host(request: Request) -> str:
    """Devuelve host público (para wss://)"""
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
        # No bloqueamos la conversación por fallos de notificación
        pass

# -----------------------------
# Home / Salud / Diagnóstico
# -----------------------------
@app.get("/")
async def root(request: Request):
    host = _normalize_ws_host(request)
    ws_url = f"wss://{host}/cr"
    return HTMLResponse(f"""
    <h2>SpainRoom Voice — ConversationRelay</h2>
    <p>Twilio Voice Webhook (Voice URL): <code>/voice/answer_cr</code></p>
    <p>WebSocket CR: <code>{ws_url}</code></p>
    <p>Health: <code>/health</code> · Docs: <code>/docs</code></p>
    """)

@app.get("/health")
async def health():
    return JSONResponse({"ok": True})

@app.get("/diag_runtime")
async def diag_runtime():
    keys = [
        "CR_TTS_PROVIDER","CR_LANGUAGE","CR_TRANSCRIPTION_LANGUAGE","CR_VOICE",
        "ASSIGN_URL","CI_SERVICE_SID","CR_WELCOME"
    ]
    return JSONResponse({k: _env(k) for k in keys})

# -----------------------------
# TwiML: <ConversationRelay>
# -----------------------------
@app.api_route("/voice/answer_cr", methods=["GET","POST"])
async def answer_cr(request: Request):
    host = _normalize_ws_host(request)
    ws_url = f"wss://{host}/cr"

    lang        = _env("CR_LANGUAGE", "es-ES")
    trans_lang  = _env("CR_TRANSCRIPTION_LANGUAGE", lang)
    tts_provider= _env("CR_TTS_PROVIDER", "Google")  # Google es-ES suele sonar muy bien
    tts_voice   = _env("CR_VOICE", "")               # p.ej. es-ES-Neural2-A (opcional)
    ci_sid      = _env("CI_SERVICE_SID", "")         # observabilidad (opcional)
    welcome     = _env("CR_WELCOME", "Bienvenido a SpainRoom. Mínimo un mes; no somos hotel.")

    attrs = [
        f'url="{ws_url}"',
        f'language="{lang}"',
        f'transcriptionLanguage="{trans_lang}"',
        f'ttsProvider="{tts_provider}"',
        f'welcomeGreeting="{welcome}"',
        'interruptible="speech"',
        'reportInputDuringAgentSpeech="none"'
    ]
    if tts_voice:
        attrs.append(f'voice="{tts_voice}"')
    if ci_sid:
        attrs.append(f'intelligenceService="{ci_sid}"')

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <ConversationRelay {' '.join(attrs)} />
  </Connect>
</Response>"""
    return _twiml(twiml)

@app.post("/voice/fallback")
async def voice_fallback():
    return _twiml("""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say language="es-ES">Disculpe. Estamos teniendo problemas. Inténtelo más tarde.</Say>
</Response>""")

# -----------------------------
# WebSocket ConversationRelay (texto ↔ texto)
# -----------------------------
@app.websocket("/cr")
async def conversation_relay(ws: WebSocket):
    await ws.accept()

    session: Dict[str, Any] = {
        "step": "role",  # role -> city -> zone -> name -> phone -> done
        "lead": {"role":"","poblacion":"","zona":"","nombre":"","telefono":""},
    }

    async def speak(text: str, interruptible: bool = True):
        # Twilio hace TTS; aquí sólo enviamos texto
        await ws.send_json({"type": "text", "token": text, "last": True, "interruptible": bool(interruptible)})

    def _norm(t: str) -> str:
        return re.sub(r"\s+", " ", (t or "").strip())

    def _digits(t: str) -> str:
        return "".join(ch for ch in (t or "") if ch.isdigit())

    async def ask_current():
        s = session["step"]
        if s == "role":
            await speak("Para atenderle: ¿Es usted propietario o inquilino?")
        elif s == "city":
            await speak("¿Población o ciudad?")
        elif s == "zone":
            await speak("¿Zona o barrio?")
        elif s == "name":
            await speak("¿Su nombre completo?")
        elif s == "phone":
            await speak("¿Su teléfono de contacto, por favor?")

    async def finish_lead():
        lead = session["lead"].copy()

        # Confirmación al cliente
        await speak("Gracias. Tomamos sus datos. Le contactamos en breve.", interruptible=False)

        # Enviar LEAD a backend si ASSIGN_URL está configurada
        assign_url = _env("ASSIGN_URL", "")
        if assign_url:
            try:
                await _post_json(assign_url, lead, timeout=2.0)
            except Exception:
                pass

        # Log útil en servidor
        print("<<LEAD>"+json.dumps(lead, ensure_ascii=False)+"<<END>>", flush=True)

        # Finaliza sesión CR
        await ws.send_json({"type": "end", "handoffData": "{\"reason\":\"lead_captured\"}"})

    async def handle_prompt(user_text: str):
        t = _norm(user_text).lower()
        s = session["step"]
        lead = session["lead"]

        if s == "role":
            if "propiet" in t:          # propietario/propietaria
                lead["role"] = "propietario"
                session["step"] = "city"
                await speak("Gracias. ¿Población o ciudad?")
            elif "inquil" in t or "alquil" in t:
                lead["role"] = "inquilino"
                session["step"] = "city"
                await speak("Gracias. ¿Población o ciudad?")
            else:
                await speak("¿Propietario o inquilino?")

        elif s == "city":
            if len(t) >= 2:
                lead["poblacion"] = _norm(user_text).title()
                session["step"] = "zone"
                await speak("Perfecto. ¿Zona o barrio?")
            else:
                await speak("¿Población o ciudad?")

        elif s == "zone":
            if len(t) >= 2:
                lead["zona"] = _norm(user_text).title()
                session["step"] = "name"
                await speak("Gracias. ¿Su nombre completo?")
            else:
                await speak("¿Zona o barrio?")

        elif s == "name":
            if len(t.split()) >= 2:
                lead["nombre"] = _norm(user_text)
                session["step"] = "phone"
                await speak("Por último, ¿su teléfono de contacto?")
            else:
                await speak("¿Su nombre completo, por favor?")

        elif s == "phone":
            d = _digits(user_text)
            # Normaliza prefijo +34 (si viene) y validador simple español
            if d.startswith("34") and len(d) >= 11:
                d = d[-9:]
            if len(d) == 9 and d[0] in "6789":
                lead["telefono"] = d
                session["step"] = "done"
                await finish_lead()
            else:
                await speak("¿Me facilita un teléfono de nueve dígitos?")

        else:
            await speak("Gracias. Enseguida le atendemos.")

    try:
        # Primera pregunta al abrir
        await ask_current()

        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")

            if mtype == "setup":
                # CR listo; no hacemos nada (la bienvenida ya la dijo Twilio)
                pass

            elif mtype == "prompt":
                # Texto del cliente (ya transcrito por Twilio)
                user_text = msg.get("voicePrompt","") or ""
                is_last   = bool(msg.get("last", True))
                if user_text and is_last:
                    await handle_prompt(user_text)

            elif mtype == "interrupt":
                # El cliente interrumpe la locución: reiteramos la pregunta actual
                await ask_current()

            elif mtype == "dtmf":
                # Ignoramos tonos
                pass

            elif mtype == "error":
                await speak("Disculpe. Estamos teniendo problemas. Inténtelo más tarde.", interruptible=False)
                break

            # Otros mensajes CR se ignoran

    except Exception as e:
        print("CR ws error:", e, flush=True)
    finally:
        with contextlib.suppress(Exception):
            await ws.close()

# -----------------------------
# /assign  (stub simple que crea tarea)
# -----------------------------
@app.post("/assign")
async def assign(payload: dict):
    """
    Crea una tarea "Contactar lead" y devuelve franchisee_id estable por zona.
    Espera: {"role":"propietario|inquilino","poblacion":"...","zona":"...","nombre":"...","telefono":"..."}
    """
    zone_key = f"{(payload.get('poblacion') or '').strip().lower()}-{(payload.get('zona') or '').strip().lower()}"
    fid = hashlib.sha1(zone_key.encode("utf-8")).hexdigest()[:10]
    task = {
        "title": "Contactar lead",
        "zone_key": zone_key,
        "franchisee_id": fid,
        "lead": payload,
        "created_at": int(time.time())
    }
    return JSONResponse({"ok": True, "task": task})

# -----------------------------
# Stripe webhook (opcional)
# -----------------------------
try:
    import stripe
    _STRIPE_OK = True
except Exception:
    _STRIPE_OK = False

@app.post("/stripe/webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None, alias="Stripe-Signature")):
    payload = await request.body()
    secret = _env("STRIPE_WEBHOOK_SECRET", "")
    if _STRIPE_OK and secret and stripe_signature:
        try:
            event = stripe.Webhook.construct_event(payload=payload, sig_header=stripe_signature, secret=secret)
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"signature: {e}"}, status_code=400)
        etype = event.get("type","unknown")
        return JSONResponse({"ok": True, "type": etype})
    else:
        # modo tolerante para pruebas sin librería/secret
        try:
            event = json.loads(payload.decode("utf-8"))
            etype = event.get("type","unknown")
        except Exception:
            etype = "unknown"
        return JSONResponse({"ok": True, "type": etype})
