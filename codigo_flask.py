
# codigo_flask.py — SpainRoom Voice (Twilio ↔ OpenAI Realtime) + Stripe + Diagnóstico
# Ejecutar: uvicorn codigo_flask:app --host 0.0.0.0 --port $PORT --proxy-headers

import os, json, base64, asyncio, contextlib, time
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Header, HTTPException
from fastapi.responses import Response, JSONResponse, HTMLResponse

try:
    import audioop  # stdlib (<=3.11). Si no está, instala audioop-lts.
except Exception:  # fallback
    import audioop_lts as audioop

import websockets

# =========================
#   ENV / Configuración
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview-2025-06-03")
OPENAI_VOICE = os.getenv("OPENAI_VOICE", "sage")

PUBLIC_WS_URL = os.getenv("PUBLIC_WS_URL", "")
TWILIO_WS_PATH = os.getenv("TWILIO_WS_PATH", "/ws/twilio")

SAYFIRST_TEXT = os.getenv("SAYFIRST_TEXT", "Bienvenido a SpainRoom. Alquilamos habitaciones a medio y largo plazo (mínimo un mes). Para atenderle: ¿Es usted propietario o inquilino?")
FOLLOWUP_GREETING_TEXT = os.getenv("FOLLOWUP_GREETING_TEXT", "Para atenderle: ¿Es usted propietario o inquilino?")
FOLLOWUP_GREETING_MS = int(os.getenv("FOLLOWUP_GREETING_MS", "300") or "300")
CAPTURE_TAG = os.getenv("CAPTURE_TAG","LEAD")

LEAD_WEBHOOK_URL = os.getenv("LEAD_WEBHOOK_URL","")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL","")

# Audio / timing
CHUNK_MS = int(os.getenv("CHUNK_MS","20") or "20")
PACE_MS  = int(os.getenv("PACE_MS","20") or "20")
ULAW_CHUNK_BYTES = int(os.getenv("ULAW_CHUNK_BYTES","160") or "160")  # 20ms a 8kHz µ-law
HWM_FRAMES = int(os.getenv("HWM_FRAMES","64") or "64")
BURST_MAX  = int(os.getenv("BURST_MAX","0") or "0")
PREFILL_FRAMES = int(os.getenv("PREFILL_FRAMES","2") or "2")
DROP_OLD = os.getenv("DROP_OLD","1") == "1"

# Compat por si alguna referencia antigua usa 'BURST_M_'
BURST_M_ = BURST_MAX

# Anti-click & arranque suave
PREROLL_MS = int(os.getenv("PREROLL_MS","2000") or "2000")
START_SPEAK_DELAY_MS = int(os.getenv("START_SPEAK_DELAY_MS","520") or "520")
DECLICK_ON = os.getenv("DECLICK_ON","1") == "1"
DECLICK_FRAMES = int(os.getenv("DECLICK_FRAMES","3") or "3")
BARGE_SEND_SILENCE = os.getenv("BARGE_SEND_SILENCE","1") == "1"

# Barge-in (opcional). Por defecto, desactivado (evita autointerrupciones).
BARGE_ENABLE = os.getenv("BARGE_ENABLE","0") == "1"
BARGE_VAD_DB = int(os.getenv("BARGE_VAD_DB","-18") or "-18")
MIN_BARGE_SPEECH_MS = int(os.getenv("MIN_BARGE_SPEECH_MS","650") or "650")
BARGE_RELEASE_MS = int(os.getenv("BARGE_RELEASE_MS","1800") or "1800")

# Ritmo de habla
PAUSE_EVERY_MS = int(os.getenv("PAUSE_EVERY_MS","0") or "0")
MAX_UTTER_MS = int(os.getenv("MAX_UTTER_MS","0") or "0")  # 0 = desactivado (no autocortar)

# Idioma/acento
ALLOW_ENGLISH = os.getenv("ALLOW_ENGLISH","0") == "1"
FORCE_LANGUAGE = os.getenv("FORCE_LANGUAGE","es-ES")
FORCE_ACCENT = os.getenv("FORCE_ACCENT","es-ES")
DISABLE_AUTO_LANG = os.getenv("DISABLE_AUTO_LANG","1") == "1"

SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", """Eres Nora de SpainRoom. Idioma y acento OBLIGATORIOS: español de España (es-ES), trato de "usted".
SpainRoom NO es un hotel (mínimo 1 mes; sin desayuno ni servicios).
Objetivo (primer paso): captar 1) ROL, 2) POBLACIÓN/CIUDAD, 3) ZONA/BARRIO, 4) NOMBRE COMPLETO, 5) TELÉFONO.
Una pregunta cada vez; ≤ 12 palabras; hable ~35% más despacio; pausas naturales cortas.
Si la respuesta es ambigua o fuera de tema, repregunte de forma cerrada.
Cuando tenga los cinco, emita SOLO en TEXTO: <<LEAD>>{"role":"propietario|inquilino","poblacion":"POBLACION","zona":"ZONA","nombre":"NOMBRE COMPLETO","telefono":"TELEFONO"}<<END>> y confirme en 1 frase.
Mantenga SIEMPRE es-ES; PROHIBIDO cambiar o comentar acento/dialecto.
""")

# =========================
#   Utilidades
# =========================
def _log(*a): print(*a, flush=True)

def _mask(x: str, keep:int=6) -> str:
    if not x: return ""
    return x[:keep] + "…" + "*"*max(0, len(x)-keep)

async def _post_json(url: str, data: dict):
    import urllib.request
    try:
        req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.read()
    except Exception as e:
        _log("deliver_lead error:", e)

async def deliver_lead(payload: dict):
    if LEAD_WEBHOOK_URL:
        await _post_json(LEAD_WEBHOOK_URL, payload)
    if SLACK_WEBHOOK_URL:
        try:
            await _post_json(SLACK_WEBHOOK_URL, {"text": f"{CAPTURE_TAG} {json.dumps(payload, ensure_ascii=False)}"})
        except Exception as e:
            _log("slack error:", e)

def ulaw8k_to_pcm24k(ulaw_bytes: bytes) -> bytes:
    """Convierte µ-law 8kHz -> PCM16 24kHz mono"""
    lin8k = audioop.ulaw2lin(ulaw_bytes, 2)
    converted, _ = audioop.ratecv(lin8k, 2, 1, 8000, 24000, None)
    return converted

def pcm24k_to_ulaw8k(pcm_bytes: bytes) -> bytes:
    """Convierte PCM16 24kHz mono -> µ-law 8kHz mono"""
    lin8k, _ = audioop.ratecv(pcm_bytes, 2, 1, 24000, 8000, None)
    ulaw = audioop.lin2ulaw(lin8k, 2)
    return ulaw

# =========================
#   FastAPI
# =========================
app = FastAPI(title="SpainRoom Voice")

@app.get("/")
async def root(request: Request):
    scheme = "wss" if request.url.scheme == "https" else "ws"
    host = request.headers.get("host") or request.url.hostname or "localhost"
    ws_url = PUBLIC_WS_URL or f"{scheme}://{host}{TWILIO_WS_PATH}"
    return HTMLResponse(f"<h3>SpainRoom Voice</h3><p>/health · /docs · /voice/answer_sayfirst</p><code>{ws_url}</code>")

@app.get("/health")
async def health():
    return JSONResponse({"ok": True})

@app.get("/diag_keys")
async def diag_keys():
    return JSONResponse({
        "OPENAI_API_KEY": _mask(OPENAI_API_KEY),
        "LEAD_WEBHOOK_URL": bool(LEAD_WEBHOOK_URL),
        "SLACK_WEBHOOK_URL": bool(SLACK_WEBHOOK_URL),
    })

@app.get("/diag_runtime")
async def diag_runtime():
    keys = [
        "OPENAI_REALTIME_MODEL","OPENAI_VOICE","ALLOW_ENGLISH","FORCE_LANGUAGE","FORCE_ACCENT","DISABLE_AUTO_LANG",
        "CHUNK_MS","PACE_MS","ULAW_CHUNK_BYTES","HWM_FRAMES","BURST_MAX","PREFILL_FRAMES","DROP_OLD",
        "PREROLL_MS","START_SPEAK_DELAY_MS","DECLICK_ON","DECLICK_FRAMES","BARGE_SEND_SILENCE",
        "BARGE_ENABLE","BARGE_VAD_DB","MIN_BARGE_SPEECH_MS","BARGE_RELEASE_MS",
        "MAX_UTTER_MS","PAUSE_EVERY_MS",
        "SAYFIRST_TEXT","FOLLOWUP_GREETING_TEXT","FOLLOWUP_GREETING_MS",
        "TWILIO_WS_PATH","PUBLIC_WS_URL","CAPTURE_TAG"
    ]
    return JSONResponse({k: os.getenv(k, "") for k in keys})

# Twilio Answer: saluda y conecta el Stream WS
@app.api_route("/voice/answer_sayfirst", methods=["GET","POST"])
async def voice_answer(request: Request):
    def getp(d, k): 
        return (d.get(k) or d.get(k.lower()) or "")
    call_sid = ""; from_phone = ""
    if request.method == "POST":
        form = dict(await request.form())
        call_sid = getp(form,"CallSid"); from_phone = getp(form,"From")
    else:
        qp = dict(request.query_params)
        call_sid = getp(qp,"CallSid"); from_phone = getp(qp,"From")
    scheme = "wss" if request.url.scheme == "https" else "ws"
    host = request.headers.get("host") or request.url.hostname or "localhost"
    ws_url = PUBLIC_WS_URL or f"{scheme}://{host}{TWILIO_WS_PATH}"
    params_xml = ""
    if call_sid or from_phone:
        params_xml = f'\n      <Parameter name="callSid" value="{call_sid}"/>\n      <Parameter name="from" value="{from_phone}"/>'
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="es-ES">{SAYFIRST_TEXT}</Say>
  <Pause length="{max(1, FOLLOWUP_GREETING_MS//1000)}" />
  <Connect>
    <Stream url="{ws_url}">{params_xml}
    </Stream>
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")

@app.post("/voice/fallback")
async def voice_fallback():
    return Response(content="<Response><Say>Disculpe, vuelva a intentarlo más tarde.</Say></Response>", media_type="application/xml")

# =========================
#  Twilio WS  ↔  OpenAI Realtime
# =========================
def _ensure_bytes(b64: str) -> bytes:
    return base64.b64decode(b64) if b64 else b""

def ulaw_to_pcm24k_b64(b64: str) -> str:
    ulaw = _ensure_bytes(b64)
    if not ulaw: return ""
    pcm = audioop.ulaw2lin(ulaw, 2)
    converted, _ = audioop.ratecv(pcm, 2, 1, 8000, 24000, None)
    return base64.b64encode(converted).decode("ascii")

@app.websocket(TWILIO_WS_PATH)
async def twilio_stream(ws_twilio: WebSocket):
    await ws_twilio.accept()
    stream_sid: str = ""
    ws_ai = None

    # colas
    ai_pcm24k_out: asyncio.Queue[bytes] = asyncio.Queue()      # audio delta desde AI
    twilio_ulaw_out: asyncio.Queue[bytes] = asyncio.Queue(maxsize=HWM_FRAMES)  # frames a Twilio

    # ---- Sender a Twilio (espera a 'start' y a streamSid) ----
    async def twilio_sender():
        # Esperar a tener streamSid válido
        while not stream_sid:
            await asyncio.sleep(0.01)

        # preroll de silencio
        silent = bytes([0xFF]) * ULAW_CHUNK_BYTES
        for _ in range(max(0, PREROLL_MS // CHUNK_MS)):
            await ws_twilio.send_text(json.dumps({
                "event":"media","streamSid":stream_sid,
                "media":{"payload":base64.b64encode(silent).decode("ascii")}
            }))
            await asyncio.sleep(CHUNK_MS/1000)

        pending_voice_start_delay_ms = START_SPEAK_DELAY_MS

        while True:
            ulaw = await twilio_ulaw_out.get()
            try:
                if pending_voice_start_delay_ms > 0 and ulaw != silent:
                    await asyncio.sleep(pending_voice_start_delay_ms/1000)
                    pending_voice_start_delay_ms = 0

                payload_b64 = base64.b64encode(ulaw).decode("ascii")
                await ws_twilio.send_text(json.dumps({
                    "event":"media","streamSid":stream_sid,
                    "media":{"payload":payload_b64}
                }))
            finally:
                twilio_ulaw_out.task_done()

    # ---- Convertidor AI->Twilio (PCM24k -> µ-law 8k) ----
    async def ai_to_twilio():
        acc = b""
        FRAME_BYTES_24K = 960  # 20 ms @ 24 kHz, 16-bit mono
        while True:
            pcm = await ai_pcm24k_out.get()
            if pcm == b"__END__":
                ai_pcm24k_out.task_done()
                continue
            acc += pcm
            while len(acc) >= FRAME_BYTES_24K:
                frame = acc[:FRAME_BYTES_24K]; acc = acc[FRAME_BYTES_24K:]
                lin8k, _ = audioop.ratecv(frame, 2, 1, 24000, 8000, None)
                ulaw = audioop.lin2ulaw(lin8k, 2)
                # anti-ráfagas
                if DROP_OLD:
                    while twilio_ulaw_out.qsize() > HWM_FRAMES:
                        try:
                            twilio_ulaw_out.get_nowait(); twilio_ulaw_out.task_done()
                        except Exception:
                            break
                await twilio_ulaw_out.put(ulaw)
            ai_pcm24k_out.task_done()

    # ---- OpenAI Realtime: conectar y bombear audio out->Twilio ----
    async def open_ai_and_pump():
        nonlocal ws_ai
        url = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1",
        }
        ws_ai = await websockets.connect(url, extra_headers=headers, max_size=10*1024*1024, ping_interval=20, ping_timeout=20)
        # Sesión: fijar idioma/acento, formatos de audio
        session_update = {
            "type": "session.update",
            "session": {
                "instructions": SYSTEM_PROMPT,
                "voice": OPENAI_VOICE,
                "input_audio_format": {"type":"pcm16","sample_rate_hz":24000},
                "output_audio_format": {"type":"pcm16","sample_rate_hz":24000},
                "modalities": ["text","audio"],
                "language": "es-ES",
            },
        }
        await ws_ai.send(json.dumps(session_update))

        # Pedir la primera pregunta (después del <Say> de Twilio)
        await ws_ai.send(json.dumps({
            "type": "response.create",
            "response": {"instructions": FOLLOWUP_GREETING_TEXT}
        }))

        async def reader():
            while True:
                try:
                    msg = await ws_ai.recv()
                except Exception as e:
                    break
                evt = json.loads(msg)
                et = evt.get("type")
                if et == "response.output_audio.delta":
                    b = base64.b64decode(evt.get("delta",""))
                    await ai_pcm24k_out.put(b)
                elif et in ("response.output_audio.end","response.completed"):
                    await ai_pcm24k_out.put(b"__END__")
                elif et == "error":
                    # Loguear y continuar para no parar la llamada
                    _ = evt

        asyncio.create_task(reader())
        return ws_ai

    # Lanzar tareas auxiliares (el sender arranca después de "start")
    ai_to_twilio_task = asyncio.create_task(ai_to_twilio())
    sender_task: Optional[asyncio.Task] = None
    ws_ai = await open_ai_and_pump()

    # Bucle principal Twilio
    try:
        while True:
            raw = await ws_twilio.receive_text()
            data = json.loads(raw)
            ev = data.get("event")

            if ev == "start":
                stream_sid = data.get("streamSid","")
                if sender_task is None:
                    sender_task = asyncio.create_task(twilio_sender())
                continue

            if ev == "media":
                # Audio entrante desde Twilio (µ-law base64) -> AI
                b64 = data.get("media",{}).get("payload","")
                if not b64:
                    continue
                # Convertir a PCM 24k y enviar a AI como input
                if ws_ai:
                    try:
                        await ws_ai.send(json.dumps({"type":"input_audio_buffer.append","audio": ulaw_to_pcm24k_b64(b64)}))
                        await ws_ai.send(json.dumps({"type":"input_audio_buffer.commit"}))
                        await ws_ai.send(json.dumps({"type":"response.create"}))
                    except Exception as e:
                        _ = e
                continue

            if ev == "stop":
                break

    except WebSocketDisconnect:
        pass
    except Exception as e:
        _ = e
    finally:
        with contextlib.suppress(Exception):
            await twilio_ulaw_out.put(bytes([0xFF]) * ULAW_CHUNK_BYTES)  # cierre suave
        with contextlib.suppress(Exception):
            if ws_ai: await ws_ai.close()
        with contextlib.suppress(Exception):
            if sender_task: sender_task.cancel()
            ai_to_twilio_task.cancel()

# =========================
#   /assign (stub simple)
# =========================
@app.post("/assign")
async def assign(payload: dict):
    """
    Stub de asignación: recibe un LEAD y devuelve task creada.
    Espera: {"role":"propietario|inquilino","poblacion":"...","zona":"...","nombre":"...","telefono":"..."}
    """
    import hashlib
    key = f"{payload.get('poblacion','')}-{payload.get('zona','')}".lower().strip()
    fid = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    task = {
        "title": "Contactar lead",
        "zone_key": key,
        "franchisee_id": fid,
        "lead": payload,
        "created_at": int(time.time())
    }
    await deliver_lead(task)
    return JSONResponse({"ok": True, "task": task})

# =========================
#   Stripe (webhook + helpers)
# =========================
try:
    import stripe
    _STRIPE_LIB = True
except Exception:
    _STRIPE_LIB = False

STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")  # opcional para create-intent / release

if _STRIPE_LIB and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

@app.post("/stripe/webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None, alias="Stripe-Signature")):
    payload = await request.body()
    event = None

    if _STRIPE_LIB and STRIPE_WEBHOOK_SECRET and stripe_signature:
        try:
            event = stripe.Webhook.construct_event(
                payload=payload,
                sig_header=stripe_signature,
                secret=STRIPE_WEBHOOK_SECRET
            )
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"signature: {e}"}, status_code=400)
    else:
        # modo tolerante (CLI sin verificación)
        try:
            event = json.loads(payload.decode("utf-8"))
        except Exception:
            event = {"type": "unknown"}

    etype = (event or {}).get("type","unknown")
    return JSONResponse({"ok": True, "type": etype})

# Política de reparto
OWNER_SHARE_BPS = int(os.getenv("OWNER_SHARE_BPS","7000"))
PLATFORM_SHARE_BPS = int(os.getenv("PLATFORM_SHARE_BPS","3000"))
OWNER_HOLD_DAYS = int(os.getenv("OWNER_HOLD_DAYS","20"))
FRANCHISEE_HOLD_DAYS = int(os.getenv("FRANCHISEE_HOLD_DAYS", str(OWNER_HOLD_DAYS)))
FRANCHISEE_OF_PLATFORM_PCT = int(os.getenv("FRANCHISEE_OF_PLATFORM_PCT","50"))
REQUIRE_INVOICE_FRANCHISEE = os.getenv("REQUIRE_INVOICE_FRANCHISEE","1") == "1"
TRANSFER_GROUP_PREFIX = os.getenv("TRANSFER_GROUP_PREFIX","sr_")
MIN_PAYOUT_EUR = float(os.getenv("MIN_PAYOUT_EUR","0"))

def _cents(eur: float) -> int:
    return int(round(float(eur) * 100.0))

@app.post("/stripe/create-intent")
async def stripe_create_intent(payload: dict):
    if not (_STRIPE_LIB and STRIPE_SECRET_KEY):
        raise HTTPException(400, "Stripe no configurado en servidor")
    amount_cents = int(payload["amount_cents"])
    reserva_id = str(payload["reserva_id"])
    owner_acct = payload.get("owner_acct","")
    franchisee_acct = payload.get("franchisee_acct","")
    tg = f"{TRANSFER_GROUP_PREFIX}{reserva_id}"
    pi = stripe.PaymentIntent.create(
        amount=amount_cents,
        currency="eur",
        automatic_payment_methods={"enabled": True},
        transfer_group=tg,
        metadata={
            "reserva_id": reserva_id,
            "owner_acct": owner_acct,
            "franchisee_acct": franchisee_acct,
            "owner_share_bps": str(OWNER_SHARE_BPS),
        },
    )
    return {"client_secret": pi.client_secret}

@app.post("/stripe/release")
async def stripe_release(payload: dict):
    if not (_STRIPE_LIB and STRIPE_SECRET_KEY):
        raise HTTPException(400, "Stripe no configurado en servidor")
    reserva_id = str(payload["reserva_id"])
    amount_cents = int(payload["amount_cents"])
    owner_acct = str(payload["owner_acct"])
    franchisee_acct = payload.get("franchisee_acct")
    invoice_fr_ok = bool(payload.get("invoice_fr_ok", False))
    hold_until_epoch = int(payload.get("hold_until_epoch", 0))

    now = int(time.time())
    tg = f"{TRANSFER_GROUP_PREFIX}{reserva_id}"

    # 1) Propietario
    owner_amount = amount_cents * OWNER_SHARE_BPS // 10000
    paid_owner = False
    if not hold_until_epoch:
        hold_until_epoch = now + OWNER_HOLD_DAYS * 24 * 3600
    if now >= hold_until_epoch and owner_amount > 0:
        stripe.Transfer.create(destination=owner_acct, amount=owner_amount, currency="eur", transfer_group=tg)
        paid_owner = True

    # 2) Franquiciado (parte de plataforma) — factura + plazo
    paid_fr = False
    if franchisee_acct:
        fr_base = amount_cents * PLATFORM_SHARE_BPS // 10000
        fr_amount = fr_base * FRANCHISEE_OF_PLATFORM_PCT // 100
        if invoice_fr_ok and now >= hold_until_epoch and fr_amount > 0:
            stripe.Transfer.create(destination=franchisee_acct, amount=fr_amount, currency="eur", transfer_group=tg)
            paid_fr = True

    return {"ok": True, "owner_transfer": paid_owner, "franchisee_transfer": paid_fr, "reserva_id": reserva_id}
