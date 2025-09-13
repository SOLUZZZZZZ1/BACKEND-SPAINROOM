# codigo_flask.py — SpainRoom Voice (Twilio 8 kHz ↔ OpenAI 24 kHz)
# -----------------------------------------------------------------
# • 24 kHz end-to-end (tono correcto)
# • TwiML GET/POST (+ <Parameter> callSid/from)
# • Anti-click 1: frames μ-law exactos (160 bytes / 20 ms)
# • Anti-click 2: “de-click” (micro-fade 1–2 frames al cortar por barge-in)
# • Barge-in fino + start-speak delay (no pisa, no retoma de golpe)
# • PREFILL de cola y política “drop-old” (sin ráfagas): adiós audio acelerado/entrecortado
# • Voz forzada es-ES y guion férreo “no hotel”
# • Captura LEAD (<<LEAD>>{...}<<END>>) + envío Slack/Webhook
# • Endpoints /diag_keys, /diag_runtime y /assign (regla 10k=1; tabla Barcelona)
# • Compatibilidad Python 3.13 (audioop-lts)
# • Logs limpios: CancelledError silenciada, HEAD / soportado
#
# Start Command (Render):
#   uvicorn codigo_flask:app --host 0.0.0.0 --port $PORT --proxy-headers

import os, json, base64, asyncio, contextlib, time, math, urllib.request
from typing import Optional, List, Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Body, HTTPException
from fastapi.responses import Response, JSONResponse, HTMLResponse
import websockets
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

# Compat audio: en Python 3.13 quitaron audioop del core
try:
    import audioop          # Python ≤ 3.12
except ModuleNotFoundError:
    import audioop_lts as audioop  # Python 3.13+

# ========= ENV / Config =========
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview-2025-06-03")
OPENAI_VOICE = os.getenv("OPENAI_VOICE", "sage")

TWILIO_WS_PATH = os.getenv("TWILIO_WS_PATH", "/ws/twilio")
PUBLIC_WS_URL = os.getenv("PUBLIC_WS_URL")

SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT",
    "Eres Nora de SpainRoom. Idioma y acento OBLIGATORIOS: español de España (es-ES), trato de 'usted'. "
    "SpainRoom NO es un hotel (mínimo 1 mes; sin desayuno ni servicios). "
    "Objetivo (primer paso): captar 1) ROL (propietario|inquilino), 2) POBLACIÓN, 3) ZONA/BARRIO, 4) NOMBRE COMPLETO, 5) TELÉFONO. "
    "Una pregunta cada vez; ≤ 12 palabras; hable ~30% más despacio; pausas naturales cortas. "
    "Si la respuesta es ambigua o fuera de tema, repregunte de forma cerrada. "
    "Cuando tenga los cinco, emita SOLO en TEXTO (no voz): "
    "<<LEAD>>{\"role\":\"propietario|inquilino\",\"poblacion\":\"POBLACION\",\"zona\":\"ZONA\","
    "\"nombre\":\"NOMBRE COMPLETO\",\"telefono\":\"TELEFONO\"}<<END>> y confirme en una frase. "
    "Mantenga SIEMPRE es-ES; PROHIBIDO cambiar o comentar acento/dialecto."
)

# Audio / ritmo (reloj estable: mantener PACE_MS == CHUNK_MS)
CHUNK_MS = int(os.getenv("CHUNK_MS", "20"))                   # 20 ms por frame
ULAW_CHUNK_BYTES = int(os.getenv("ULAW_CHUNK_BYTES", "160"))  # 20 ms @ 8 kHz μ-law
PACE_MS = int(os.getenv("PACE_MS", str(CHUNK_MS)))
HWM_FRAMES = int(os.getenv("HWM_FRAMES", "60"))               # umbral de cola; drop-old si lo supera
BURST_MAX = int(os.getenv("BURST_MAX", "0"))                  # 0 = sin ráfagas (no acelerar nunca)
PREROLL_MS = int(os.getenv("PREROLL_MS", "1400"))             # acolchado de arranque por turno

# Prefill: frames mínimos en cola antes de hablar (≈60 ms por defecto)
PREFILL_FRAMES = int(os.getenv("PREFILL_FRAMES", "3"))

SAYFIRST_TEXT = os.getenv("SAYFIRST_TEXT",
    "Bienvenido a SpainRoom. Alquilamos habitaciones a medio y largo plazo (mínimo un mes). "
    "Para atenderle: ¿Es usted propietario o inquilino?")
FOLLOWUP_GREETING_MS = int(os.getenv("FOLLOWUP_GREETING_MS", "300"))
FOLLOWUP_GREETING_TEXT = os.getenv("FOLLOWUP_GREETING_TEXT", "Para atenderle: ¿Es usted propietario o inquilino?")

# Turnos / pausas
PAUSE_EVERY_MS = int(os.getenv("PAUSE_EVERY_MS", "0"))        # 0 = sin micro-pausas artificiales
MAX_UTTER_MS = int(os.getenv("MAX_UTTER_MS", "3400"))
BARGE_VAD_DB = float(os.getenv("BARGE_VAD_DB", "-28"))
BARGE_RELEASE_MS = int(os.getenv("BARGE_RELEASE_MS", "1000"))
BARGE_SEND_SILENCE = os.getenv("BARGE_SEND_SILENCE", "1") == "1"
START_SPEAK_DELAY_MS = int(os.getenv("START_SPEAK_DELAY_MS", "380"))
MIN_BARGE_SPEECH_MS = int(os.getenv("MIN_BARGE_SPEECH_MS", "240"))

# De-click (micro fade-out al cortar)
DECLICK_ON = os.getenv("DECLICK_ON", "1") == "1"
DECLICK_FRAMES = int(os.getenv("DECLICK_FRAMES", "2"))       # 1–2 frames (20–40 ms)
DECLICK_FACTORS = [0.6, 0.3]

# Lead / entrega
CAPTURE_TAG = os.getenv("CAPTURE_TAG", "LEAD")
LEAD_WEBHOOK_URL = os.getenv("LEAD_WEBHOOK_URL", "")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

DEBUG = os.getenv("DEBUG", "0") == "1"

# ========= App =========
app = FastAPI(title="SpainRoom Voice Realtime", docs_url="/docs", redoc_url=None)

def _log(*a):  # logs solo si DEBUG=1
    if DEBUG: print("[SRV]", *a)

def _mask(key: str) -> str:
    if not key: return ""
    if len(key) <= 8: return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]

def _infer_ws_url(request: Request) -> str:
    if PUBLIC_WS_URL: return PUBLIC_WS_URL
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    proto = request.headers.get("x-forwarded-proto", "https")
    scheme = "wss" if proto == "https" else "ws"
    return f"{scheme}://{host}{TWILIO_WS_PATH}"

class RateCV:
    """Wrapper para audioop.ratecv con estado persistente."""
    def __init__(self, src_rate: int, dst_rate: int, sampwidth: int = 2, channels: int = 1):
        self.src_rate = src_rate; self.dst_rate = dst_rate
        self.sampwidth = sampwidth; self.channels = channels
        self.state = None
    def convert(self, pcm: bytes) -> bytes:
        if not pcm: return b""
        out, self.state = audioop.ratecv(pcm, self.sampwidth, self.channels,
                                         self.src_rate, self.dst_rate, self.state)
        return out

async def _post_json(url: str, payload: Dict):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    return await asyncio.to_thread(lambda: urllib.request.urlopen(req, timeout=6).read())

async def deliver_lead(lead: Dict):
    if not lead: return
    if LEAD_WEBHOOK_URL:
        with contextlib.suppress(Exception):
            await _post_json(LEAD_WEBHOOK_URL, lead); _log("Lead → webhook")
    if SLACK_WEBHOOK_URL:
        with contextlib.suppress(Exception):
            text = (f":house: *LEAD* — {lead.get('role','?')} | "
                    f"{lead.get('poblacion', lead.get('zona','?'))} | {lead.get('zona','?')} | "
                    f"{lead.get('nombre','?')} | {lead.get('telefono','?')}")
            await _post_json(SLACK_WEBHOOK_URL, {"text": text}); _log("Lead → Slack")

# ======== Asignación por población/zona → franquiciado ========
# Tabla Barcelona (2025) — habitantes por distrito
BCN_DIST = {
    "EIXAMPLE": 274_636,
    "SANT MARTI": 249_206,
    "SANTS-MONTJUIC": 179_000,
    "GRACIA": 121_000,
    "LES CORTS": 83_000,
    "SARRIA-SANT GERVASI": 150_000,
    "CIUTAT VELLA": 100_000,
    "NOU BARRIS": 168_000,
    "HORTA-GUINARDO": 170_000,
    "SANT ANDREU": 150_000,
}

def _norm(s: str) -> str:
    """Normaliza texto para matching de zonas/distritos."""
    if not s: return ""
    x = s.upper().strip()
    rep = {
        "Á":"A","É":"E","Í":"I","Ó":"O","Ú":"U","Ü":"U","Ç":"C","Ñ":"N",
        "-":" ","/":" ",
    }
    for a,b in rep.items(): x = x.replace(a,b)
    while "  " in x: x = x.replace("  "," ")
    return x

def _ceil_slots(pop: int) -> int:
    """Regla: 1 franquiciado por cada 10.000 habitantes (mínimo 1)."""
    import math
    return max(1, math.ceil(pop / 10_000))

def _guess_zone_slots(poblacion: str, zona: str) -> dict:
    """
    Devuelve info de zona estandarizada y nº de slots (franquiciados) por la regla 10k=1.
    Para Barcelona, intenta mapear a su distrito; si no, usa la ciudad completa.
    Para el resto, aplica mínimo 10.000 (1 slot) si no hay censo cargado.
    """
    pob, z = _norm(poblacion), _norm(zona)
    if pob in ("BARCELONA","BCN"):
        zc = z.replace(" BARCELONA","").replace(" DISTRITO","").strip()
        map_keys = {
            "EIXAMPLE":"EIXAMPLE",
            "SANT MARTI":"SANT MARTI",
            "SANTS MONTJUIC":"SANTS-MONTJUIC",
            "GRACIA":"GRACIA",
            "LES CORTS":"LES CORTS",
            "SARRIA SANT GERVASI":"SARRIA-SANT GERVASI",
            "CIUTAT VELLA":"CIUTAT VELLA",
            "NOU BARRIS":"NOU BARRIS",
            "HORTA GUINARDO":"HORTA-GUINARDO",
            "SANT ANDREU":"SANT ANDREU",
        }
        key = map_keys.get(zc, None)
        if key and key in BCN_DIST:
            pop = BCN_DIST[key]
            return {
                "city": "Barcelona",
                "district": key.title().replace("-", " "),
                "zone_key": f"Barcelona > {key.title().replace('-', ' ')}",
                "population": pop,
                "slots": _ceil_slots(pop),
            }
        total_pop = sum(BCN_DIST.values())
        return {
            "city": "Barcelona",
            "district": z.title() if z else "General",
            "zone_key": f"Barcelona > {z.title() if z else 'General'}",
            "population": total_pop,
            "slots": _ceil_slots(total_pop),
        }
    # TODO: Madrid por distritos/barrios con su tabla (mismo criterio 10k→1).
    return {
        "city": poblacion.title(),
        "district": z.title() if z else "General",
        "zone_key": f"{poblacion.title()} > {z.title() if z else 'General'}",
        "population": 10_000,
        "slots": _ceil_slots(10_000),
    }

def _pick_franchisee(zone_info: dict, nombre: str, telefono: str) -> str:
    """
    Asignación simple y estable por hash: reparte entre 'slots'.
    Devuelve un identificador legible del slot elegido.
    """
    slots = max(1, int(zone_info.get("slots", 1)))
    import hashlib
    seed = f"{nombre}|{telefono}"
    idx = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16) % slots
    base = zone_info["zone_key"]
    return f"{base} :: SLOT {idx+1:02d}"

async def deliver_task_to_slack(task: dict):
    """Envía la tarea al Slack Webhook (si está configurado)."""
    if not SLACK_WEBHOOK_URL:
        return
    text = (
        ":bell: *TAREA FRANQUICIADO*\n"
        f"*Zona:* {task.get('zone_key')}\n"
        f"*Asignado a:* {task.get('franchisee_id')}\n"
        f"*Lead:* {task['lead'].get('nombre','?')} · {task['lead'].get('telefono','?')} · {task['lead'].get('role','?')}\n"
        f"*Notas:* {task.get('notes','')}"
    )
    with contextlib.suppress(Exception):
        await _post_json(SLACK_WEBHOOK_URL, {"text": text})

@app.post("/assign")
async def assign_lead(payload: dict = Body(...)):
    """
    Recibe un LEAD (role, poblacion, zona, nombre, telefono, ...),
    calcula slots por norma 10k=1, elige franquiciado y devuelve la tarea creada.
    También envía aviso a Slack (si hay webhook) y a un webhook extra opcional (ASSIGN_TASK_WEBHOOK_URL).
    """
    role = (payload.get("role") or "").strip().lower()
    poblacion = (payload.get("poblacion") or payload.get("city") or "").strip()
    zona = (payload.get("zona") or payload.get("district") or "").strip()
    nombre = (payload.get("nombre") or "").strip()
    telefono = (payload.get("telefono") or payload.get("from") or "").strip()

    if role not in ("propietario","inquilino") or not poblacion or not nombre or not telefono:
        raise HTTPException(status_code=400, detail="Faltan campos (role/poblacion/nombre/telefono)")

    zone_info = _guess_zone_slots(poblacion, zona)
    franchisee_id = _pick_franchisee(zone_info, nombre, telefono)

    task = {
        "type": "TASK",
        "title": "Contactar lead entrante",
        "zone_key": zone_info["zone_key"],
        "franchisee_id": franchisee_id,
        "due_at": None,  # si quieres, añade vencimiento 24h
        "lead": {
            "role": role,
            "poblacion": poblacion,
            "zona": zona,
            "nombre": nombre,
            "telefono": telefono,
            "source": payload.get("source","twilio-voice"),
            "call_sid": payload.get("call_sid",""),
            "timestamp": payload.get("timestamp", int(time.time()*1000)),
        },
        "notes": "Llamada con interés en la zona. Prioridad 24h.",
    }

    # Aviso Slack (si hay webhook)
    await deliver_task_to_slack(task)

    # Webhook extra opcional (p.ej., tu CRM externo)
    with contextlib.suppress(Exception):
        extra = os.getenv("ASSIGN_TASK_WEBHOOK_URL", "")
        if extra:
            await _post_json(extra, task)

    return {"ok": True, "task": task, "zone": zone_info}

# ========= Rutas de diagnóstico / TwiML =========
@app.api_route("/", methods=["GET", "HEAD"])
async def root(request: Request):
    ws_url = _infer_ws_url(request)
    return HTMLResponse(f"<h3>SpainRoom Voice</h3><p>/health · /docs · /voice/answer_sayfirst (GET/POST)</p><code>{ws_url}</code>")

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
    return {
        "OPENAI_REALTIME_MODEL": OPENAI_REALTIME_MODEL,
        "realtime_ws": f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"
    }

@app.api_route("/voice/answer_sayfirst", methods=["GET","POST"])
async def voice_answer(request: Request):
    call_sid = ""; from_phone = ""
    with contextlib.suppress(Exception):
        if request.method == "POST":
            form = dict(await request.form())
            call_sid = form.get("CallSid", "") or form.get("callsid", "")
            from_phone = form.get("From", "") or form.get("from", "")
        else:
            qp = dict(request.query_params)
            call_sid = qp.get("CallSid", "") or qp.get("callsid", "")
            from_phone = qp.get("From", "") or qp.get("from", "")
    ws_url = _infer_ws_url(request)
    params_xml = ""
    if call_sid or from_phone:
        params_xml = (f'\n      <Parameter name="callSid" value="{call_sid}"/>'
                      f'\n      <Parameter name="from" value="{from_phone}"/>')
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="es-ES">{SAYFIRST_TEXT}</Say>
  <Connect>
    <Stream url="{ws_url}">{params_xml}
    </Stream>
  </Connect>
</Response>"""
    return Response(twiml, media_type="text/xml; charset=utf-8")

@app.api_route("/voice/fallback", methods=["GET","POST"])
async def voice_fallback():
    return Response('<?xml version="1.0"?><Response><Say language="es-ES">Lo siento, intente de nuevo.</Say></Response>',
                    media_type="text/xml; charset=utf-8")

# ========= WebSocket: Twilio ⇄ OpenAI =========
@app.websocket(TWILIO_WS_PATH)
async def twilio_stream(ws_twilio: WebSocket):
    await ws_twilio.accept(subprotocol="audio"); _log("WS accepted (audio)")
    if not OPENAI_API_KEY:
        await ws_twilio.send_text(json.dumps({"event":"error","message":"Falta OPENAI_API_KEY"}))
        await ws_twilio.close(); return

    # Estado WS
    stream_sid: Optional[str] = None; started = False
    start_evt = asyncio.Event()
    ws_ai: Optional[websockets.WebSocketClientProtocol] = None
    buffered_ulaw: List[bytes] = []
    call_sid = ""; from_phone = ""

    # Turn-taking
    barge_active = False; barge_last_voice = 0.0
    ai_spoken_ms = 0; speak_gate_until = 0.0; vad_hot_ms = 0

    # Resamplers
    up_8k_to_24k = RateCV(8000, 24000, 2, 1)
    down_24k_to_8k = RateCV(24000, 8000, 2, 1)

    # Salida Twilio + anti-click
    ulaw_out_queue: asyncio.Queue = asyncio.Queue(maxsize=4000)
    ulaw_carry = bytearray()
    last_ulaw_sent: Optional[bytes] = None  # para de-click

    def _declick_frames_from_last() -> List[str]:
        out: List[str] = []
        if not last_ulaw_sent or not DECLICK_ON: return out
        try:
            lin = audioop.ulaw2lin(last_ulaw_sent, 2)
            nframes = 2 if DECLICK_FRAMES >= 2 else 1
            for f in DECLICK_FACTORS[:nframes]:
                lin_f = audioop.mul(lin, 2, float(f))
                u = audioop.lin2ulaw(lin_f, 2)
                out.append(base64.b64encode(u).decode('ascii'))
        except Exception as e:
            _log("de-click error", e)
        return out

    async def enqueue_ulaw_frames(ulaw_bytes: bytes):
        nonlocal ulaw_carry
        ulaw_carry.extend(ulaw_bytes)
        # emitir SOLO frames completos de 160B
        while len(ulaw_carry) >= ULAW_CHUNK_BYTES:
            frame = bytes(ulaw_carry[:ULAW_CHUNK_BYTES])
            await ulaw_out_queue.put(base64.b64encode(frame).decode('ascii'))
            del ulaw_carry[:ULAW_CHUNK_BYTES]

    async def enqueue_silence(ms: int):
        nonlocal ai_spoken_ms
        ai_spoken_ms = 0
        frames = max(1, ms // CHUNK_MS)
        b64 = base64.b64encode(bytes([0xFF]) * ULAW_CHUNK_BYTES).decode("ascii")
        for _ in range(frames): await ulaw_out_queue.put(b64)

    async def twilio_sender():
        try:
            nonlocal stream_sid, barge_active, ai_spoken_ms, speak_gate_until, last_ulaw_sent
            next_t = time.monotonic(); sleep_s = max(0.0, PACE_MS / 1000.0); played_since_pause_ms = 0
            while True:
                now = time.monotonic()

                # Puerta de arranque + PREFILL (colchón mínimo)
                if (speak_gate_until and now < speak_gate_until) or ulaw_out_queue.qsize() < PREFILL_FRAMES:
                    if BARGE_SEND_SILENCE and stream_sid:
                        silent = base64.b64encode(bytes([0xFF]) * ULAW_CHUNK_BYTES).decode('ascii')
                        await ws_twilio.send_text(json.dumps({"event":"media","streamSid":stream_sid,"media":{"payload":silent}}))
                    if sleep_s > 0.0:
                        await asyncio.sleep(sleep_s)
                    continue

                # Si el cliente habla: acolchado de-click + silencio + limpiar backlog (sin ráfagas)
                if barge_active:
                    for b64 in _declick_frames_from_last():
                        await ws_twilio.send_text(json.dumps({"event":"media","streamSid":stream_sid,"media":{"payload":b64}}))
                    if BARGE_SEND_SILENCE and stream_sid:
                        silent = base64.b64encode(bytes([0xFF]) * ULAW_CHUNK_BYTES).decode('ascii')
                        await ws_twilio.send_text(json.dumps({"event":"media","streamSid":stream_sid,"media":{"payload":silent}}))
                    try:
                        while True:
                            _ = ulaw_out_queue.get_nowait(); ulaw_out_queue.task_done()
                    except asyncio.QueueEmpty:
                        pass
                    if sleep_s > 0.0:
                        await asyncio.sleep(sleep_s)
                    continue

                # Enviar 1 frame a ritmo real
                payload_b64 = await ulaw_out_queue.get()
                try:
                    raw = base64.b64decode(payload_b64)
                    if raw != bytes([0xFF]) * ULAW_CHUNK_BYTES:
                        last_ulaw_sent = raw
                except Exception:
                    pass
                await ws_twilio.send_text(json.dumps({"event":"media","streamSid":stream_sid,"media":{"payload":payload_b64}}))
                ulaw_out_queue.task_done()

                ai_spoken_ms += CHUNK_MS
                played_since_pause_ms += CHUNK_MS

                # Pausas naturales (desactivadas por defecto)
                if PAUSE_EVERY_MS > 0 and played_since_pause_ms >= PAUSE_EVERY_MS:
                    if stream_sid:
                        silent = base64.b64encode(bytes([0xFF]) * ULAW_CHUNK_BYTES).decode('ascii')
                        await ws_twilio.send_text(json.dumps({"event":"media","streamSid":stream_sid,"media":{"payload":silent}}))
                    played_since_pause_ms = 0
                    next_t = time.monotonic()
                    continue

                # Cortar frases demasiado largas
                if MAX_UTTER_MS > 0 and ai_spoken_ms >= MAX_UTTER_MS:
                    barge_active = True
                    barge_last_voice = time.monotonic()

                # Política “drop-old” (NUNCA acelerar): si hay backlog, tirar lo viejo
                qsz = ulaw_out_queue.qsize()
                if qsz > HWM_FRAMES:
                    to_drop = qsz - HWM_FRAMES
                    try:
                        for _ in range(to_drop):
                            _ = ulaw_out_queue.get_nowait(); ulaw_out_queue.task_done()
                    except asyncio.QueueEmpty:
                        pass
                    next_t = time.monotonic()
                    continue

                # Reloj real (sin ráfagas)
                if sleep_s > 0.0:
                    next_t += sleep_s
                    delay = next_t - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    else:
                        next_t = time.monotonic()
        except asyncio.CancelledError:
            _log("sender cancelled")
            return
        except Exception as e:
            _log("sender error:", e)
            return

    async def connect_ai_after_start():
        nonlocal ws_ai, ai_spoken_ms, barge_active, speak_gate_until
        await start_evt.wait()
        try:
            headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "OpenAI-Beta": "realtime=v1"}
            ai_url = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"
            _log("Connecting to OpenAI Realtime…", OPENAI_REALTIME_MODEL)
            ws_ai = await websockets.connect(ai_url, extra_headers=headers)
            _log("OpenAI Realtime connected")

            # Configurar sesión
            await ws_ai.send(json.dumps({
                "type": "session.update",
                "session": {
                    "voice": OPENAI_VOICE,
                    "instructions": SYSTEM_PROMPT,
                    "input_audio_format": {"type": "pcm16", "sample_rate_hz": 24000, "channels": 1},
                    "output_audio_format": {"type": "pcm16", "sample_rate_hz": 24000, "channels": 1},
                    "turn_detection": {"type": "server_vad"},
                    "modalities": ["audio","text"],
                },
            }))
            ai_spoken_ms = 0; barge_active = False

            # Volcar lo buffered (si llegó audio del cliente antes de abrir AI)
            while buffered_ulaw:
                ulaw = buffered_ulaw.pop(0)
                lin8 = audioop.ulaw2lin(ulaw, 2)
                pcm24 = up_8k_to_24k.convert(lin8)
                if pcm24:
                    await ws_ai.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(pcm24).decode("ascii"),
                    }))

            # Saludo inicial (voz forzada)
            await asyncio.sleep(FOLLOWUP_GREETING_MS / 1000.0)
            with contextlib.suppress(Exception):
                await ws_ai.send(json.dumps({
                    "type":"response.create",
                    "response":{"instructions":FOLLOWUP_GREETING_TEXT, "voice":OPENAI_VOICE}
                }))

            # Loop AI → Twilio
            try:
                txt_buf = ""
                async for raw in ws_ai:
                    evt = json.loads(raw); t = evt.get("type")

                    # Nuevo turno IA
                    if t in ("response.created","response.started"):
                        ai_spoken_ms = 0; barge_active = False
                        speak_gate_until = time.monotonic() + (START_SPEAK_DELAY_MS / 1000.0)

                    # Captura LEAD (texto incremental)
                    if t in ("response.output_text.delta",):
                        delta = evt.get("delta") or evt.get("text") or ""
                        if delta:
                            txt_buf += delta
                            st, en = f"<<{CAPTURE_TAG}>>", "<<END>>"
                            if st in txt_buf and en in txt_buf:
                                s = txt_buf.rfind(st); e = txt_buf.find(en, s)
                                if e != -1:
                                    payload = txt_buf[s+len(st):e].strip()
                                    lead = {}
                                    with contextlib.suppress(Exception): lead = json.loads(payload)
                                    lead.update({"timestamp": int(time.time()*1000), "source": "twilio-voice"})
                                    await deliver_lead(lead)

                    # Audio IA → Twilio
                    if t in ("response.audio.delta","response.output_audio.delta"):
                        if barge_active:  # no enviar si el cliente está hablando
                            continue
                        b64 = evt.get("audio") or evt.get("delta")
                        if b64 and started and stream_sid:
                            pcm24 = base64.b64decode(b64)
                            lin8 = down_24k_to_8k.convert(pcm24)
                            if not lin8: continue
                            ulaw8 = audioop.lin2ulaw(lin8, 2)
                            await enqueue_ulaw_frames(ulaw8)

            except (ConnectionClosedOK, ConnectionClosedError, asyncio.CancelledError):
                _log("AI socket closed.")
            except Exception as e:
                _log("ai_to_twilio error:", e)
        finally:
            with contextlib.suppress(Exception):
                if ws_ai is not None: await ws_ai.close()

    async def twilio_to_ai():
        try:
            nonlocal stream_sid, started, barge_active, barge_last_voice, vad_hot_ms, call_sid, from_phone, start_evt
            ACCENT_GUARD = (" Mantenga acento es-ES, no imite otros acentos ni lo comente. "
                            "No hotel (mínimo 1 mes). Pregunte datos en orden: rol, población, zona, nombre, teléfono.")
            while True:
                msg_text = await ws_twilio.receive_text()
                msg = json.loads(msg_text); ev = msg.get("event")

                if ev == "start":
                    stream_sid = msg["start"]["streamSid"]; started = True
                    with contextlib.suppress(Exception):
                        params = {p.get("name"): p.get("value") for p in msg["start"].get("customParameters", [])}
                        call_sid = params.get("callSid",""); from_phone = params.get("from","")
                    _log("Twilio start — streamSid:", stream_sid, "from:", from_phone)
                    start_evt.set()
                    await enqueue_silence(PREROLL_MS)

                elif ev == "media":
                    if not started: continue
                    ulaw = base64.b64decode(msg["media"]["payload"])
                    lin = audioop.ulaw2lin(ulaw, 2)
                    rms = max(1, audioop.rms(lin, 2)); db = 20.0 * math.log10(rms / 32767.0); now = time.monotonic()

                    # VAD: activar barge-in si hay voz del cliente suficiente
                    if db >= BARGE_VAD_DB:
                        vad_hot_ms = min(vad_hot_ms + CHUNK_MS, 2000)
                        if vad_hot_ms >= MIN_BARGE_SPEECH_MS:
                            barge_active = True; barge_last_voice = now; vad_hot_ms = 0
                            with contextlib.suppress(Exception):
                                if ws_ai is not None:
                                    await ws_ai.send(json.dumps({"type":"response.cancel"}))
                                    await ws_ai.send(json.dumps({
                                        "type":"session.update",
                                        "session":{"voice": OPENAI_VOICE, "instructions": SYSTEM_PROMPT + ACCENT_GUARD}
                                    }))
                    else:
                        vad_hot_ms = 0
                        if barge_active and (now - barge_last_voice) * 1000.0 >= BARGE_RELEASE_MS:
                            barge_active = False

                    # Cliente → IA (24 kHz) (si AI no lista, buffer μ-law)
                    if ws_ai is not None:
                        pcm24 = up_8k_to_24k.convert(lin)
                        if pcm24:
                            await ws_ai.send(json.dumps({
                                "type":"input_audio_buffer.append",
                                "audio": base64.b64encode(pcm24).decode("ascii"),
                            }))
                    else:
                        buffered_ulaw.append(ulaw)

                elif ev == "stop":
                    _log("Twilio stop")
                    with contextlib.suppress(Exception):
                        if ws_ai is not None:
                            await ws_ai.send(json.dumps({"type":"input_audio_buffer.commit"}))
                    break

        except WebSocketDisconnect:
            _log("Twilio WS disconnect")
        except Exception as e:
            _log("twilio_to_ai error:", e)

    # Lanzar tareas
    task_connect = asyncio.create_task(connect_ai_after_start())
    task_twilio  = asyncio.create_task(twilio_to_ai())
    task_sender  = asyncio.create_task(twilio_sender())

    done, pending = await asyncio.wait(
        {task_connect, task_twilio, task_sender},
        return_when=asyncio.FIRST_COMPLETED
    )
    # Cancelar el resto sin ensuciar logs
    for t in pending: t.cancel()
    with contextlib.suppress(Exception, asyncio.CancelledError):
        await asyncio.gather(*pending, return_exceptions=True)

# Dev local
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("codigo_flask:app", host="0.0.0.0", port=8000)
