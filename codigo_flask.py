
# codigo_flask.py — SpainRoom Voice (Twilio 8 kHz ↔ OpenAI 24 kHz) con barge‑in, anti-click y captura LEAD
# Requisitos: fastapi, uvicorn[standard], websockets
# ENV principales:
#   OPENAI_API_KEY, OPENAI_REALTIME_MODEL=gpt-4o-realtime-preview, OPENAI_VOICE=sage
#   TWILIO_WS_PATH=/ws/twilio, PUBLIC_WS_URL=wss://TU_DOMINIO/ws/twilio
#   CHUNK_MS=20, PACE_MS=20, HWM_FRAMES=50, BURST_MAX=8, PREROLL_MS=700
#   PAUSE_EVERY_MS=0 (0 = sin micro-pausas)
#   BARGE_VAD_DB=-30, BARGE_RELEASE_MS=650, BARGE_SEND_SILENCE=1
#   START_SPEAK_DELAY_MS=150, MIN_BARGE_SPEECH_MS=120
#   SAYFIRST_TEXT="Hola, soy Nora de SpainRoom.", FOLLOWUP_GREETING_MS=300, FOLLOWUP_GREETING_TEXT="..."
#   SYSTEM_PROMPT="Eres Nora ... (guía de captura)"
#   CAPTURE_TAG=LEAD  (para detectar <<LEAD>>{...}<<END>> en texto)
#
import os, json, base64, asyncio, contextlib, time, math
from typing import Optional, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response, JSONResponse, HTMLResponse
import websockets
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError
import audioop          # Python ≤ 3.12
except ModuleNotFoundError:
    import audioop_lts as audioop   # Python 3.13+

# ========= Config =========
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
OPENAI_VOICE = os.getenv("OPENAI_VOICE", "sage")
TWILIO_WS_PATH = os.getenv("TWILIO_WS_PATH", "/ws/twilio")
PUBLIC_WS_URL = os.getenv("PUBLIC_WS_URL")
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT",
    "Eres Nora de SpainRoom. Habla SIEMPRE en español de España (es-ES), tono estable. "
    "Primero identifica si es PROPIETARIO o INQUILINO y captura: 1) NOMBRE 2) ZONA. "
    "Cuando tengas los tres, emite en TEXTO una sola línea: "
    "<<LEAD>>{\"role\":\"propietario|inquilino\",\"nombre\":\"NOMBRE\",\"zona\":\"ZONA\"}<<END>> "
    "y confirma al cliente en 1 frase.")

# Audio / pacing
CHUNK_MS = int(os.getenv("CHUNK_MS", "20"))                   # 20 ms por frame
ULAW_CHUNK_BYTES = int(os.getenv("ULAW_CHUNK_BYTES", "160"))  # 20 ms @ 8 kHz μ-law
PACE_MS = int(os.getenv("PACE_MS", str(CHUNK_MS)))            # por defecto, tiempo real
HWM_FRAMES = int(os.getenv("HWM_FRAMES", "50"))               # backlog alto (≈1s si 20 ms/frame)
BURST_MAX = int(os.getenv("BURST_MAX", "8"))                  # frames extra por tick para recuperar
PREROLL_MS = int(os.getenv("PREROLL_MS", "700"))

SAYFIRST_TEXT = os.getenv("SAYFIRST_TEXT", "Hola, soy Nora de SpainRoom.")
FOLLOWUP_GREETING_MS = int(os.getenv("FOLLOWUP_GREETING_MS", "300"))
FOLLOWUP_GREETING_TEXT = os.getenv("FOLLOWUP_GREETING_TEXT", "¿Eres propietario con habitaciones libres o inquilino buscando habitación?")
DEBUG = os.getenv("DEBUG", "0") == "1"

# Pausas y barge-in
PAUSE_EVERY_MS = int(os.getenv("PAUSE_EVERY_MS", "0"))        # 0 = sin micro-pausas
BARGE_VAD_DB = float(os.getenv("BARGE_VAD_DB", "-30"))        # umbral dBFS para detectar voz del cliente
BARGE_RELEASE_MS = int(os.getenv("BARGE_RELEASE_MS", "650"))  # silencio necesario para volver a hablar
BARGE_SEND_SILENCE = os.getenv("BARGE_SEND_SILENCE", "1") == "1"
START_SPEAK_DELAY_MS = int(os.getenv("START_SPEAK_DELAY_MS", "150"))  # retardo antes de arrancar a hablar
MIN_BARGE_SPEECH_MS = int(os.getenv("MIN_BARGE_SPEECH_MS", "120"))    # ms mínimos de voz para cortar (evita falsos positivos)

MAX_UTTER_MS = int(os.getenv("MAX_UTTER_MS", "4200"))

CAPTURE_TAG = os.getenv("CAPTURE_TAG", "LEAD")

# ========= App =========
app = FastAPI(title="SpainRoom Voice Realtime", docs_url="/docs", redoc_url=None)

def _log(*a):
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

# ========= Rutas de diagnóstico =========
@app.get("/")
async def root(request: Request):
    ws_url = _infer_ws_url(request)
    html = f"""
    <h1>SpainRoom Voice</h1>
    <p>OK. Endpoints:</p>
    <ul>
      <li><code>/health</code></li>
      <li><code>/docs</code></li>
      <li><code>/voice/answer_sayfirst</code> (GET/POST) — WS: <code>{ws_url}</code></li>
    </ul>
    """
    return HTMLResponse(html)

@app.get("/health")
async def health():
    return JSONResponse({"ok": True})

@app.get("/diag_keys")
async def diag_keys():
    return JSONResponse({
        "OPENAI_API_KEY": _mask(OPENAI_API_KEY),
    })

# ========= TwiML =========
@app.api_route("/voice/answer_sayfirst", methods=["GET","POST"])
async def voice_answer(request: Request):
    ws_url = _infer_ws_url(request)
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="es-ES">{SAYFIRST_TEXT}</Say>
  <Connect><Stream url="{ws_url}" /></Connect>
</Response>"""
    return Response(twiml, media_type="text/xml; charset=utf-8")

@app.api_route("/voice/fallback", methods=["GET","POST"])
async def voice_fallback():
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="es-ES">Lo siento, ahora mismo no estoy disponible. Inténtalo de nuevo en un momento.</Say>
</Response>"""
    return Response(twiml, media_type="text/xml; charset=utf-8")

# ========= WebSocket: Twilio ⇄ OpenAI (μ-law 8k <-> PCM16 24 kHz) =========
@app.websocket(TWILIO_WS_PATH)
async def twilio_stream(ws_twilio: WebSocket):
    await ws_twilio.accept(subprotocol="audio")
    _log("WS accepted (audio)")

    if not OPENAI_API_KEY:
        await ws_twilio.send_text(json.dumps({"event": "error", "message": "Falta OPENAI_API_KEY"}))
        await ws_twilio.close(); return

    stream_sid: Optional[str] = None
    started = False
    ai_ready = asyncio.Event()
    start_evt = asyncio.Event()
    ws_ai: Optional[websockets.WebSocketClientProtocol] = None
    buffered_ulaw: List[bytes] = []

    # Estado turn-taking / barge-in
    barge_active: bool = False
    barge_last_voice: float = 0.0
    ai_spoken_ms: int = 0
    speak_gate_until: float = 0.0  # retardo antes de arrancar a hablar
    vad_hot_ms: int = 0            # acumulador de voz del cliente

    # Resamplers
    up_8k_to_24k = RateCV(8000, 24000, 2, 1)
    down_24k_to_8k = RateCV(24000, 8000, 2, 1)

    # Cola salida Twilio y buffer anti-click
    ulaw_out_queue: asyncio.Queue = asyncio.Queue(maxsize=4000)
    ulaw_carry = bytearray()  # siempre emitimos frames exactos de 160 bytes

    async def twilio_sender():
        nonlocal stream_sid, barge_active, ai_spoken_ms, speak_gate_until
        next_t = time.monotonic()
        sleep_s = max(0.0, PACE_MS / 1000.0)
        played_since_pause_ms = 0
        while True:
            now = time.monotonic()

            # Puerta de arranque: retarda el inicio de habla de la IA
            if speak_gate_until and now < speak_gate_until:
                if BARGE_SEND_SILENCE and stream_sid:
                    silent = base64.b64encode(bytes([0xFF]) * ULAW_CHUNK_BYTES).decode('ascii')
                    await ws_twilio.send_text(json.dumps({"event":"media","streamSid":stream_sid,"media":{"payload":silent}}))
                if sleep_s > 0.0: await asyncio.sleep(sleep_s)
                continue

            # Si el cliente habla, no reproducir la IA; mantener reloj con silencio
            if barge_active:
                try:
                    while True:
                        _ = ulaw_out_queue.get_nowait()
                        ulaw_out_queue.task_done()
                except asyncio.QueueEmpty:
                    pass
                if BARGE_SEND_SILENCE and stream_sid:
                    silent = base64.b64encode(bytes([0xFF]) * ULAW_CHUNK_BYTES).decode('ascii')
                    await ws_twilio.send_text(json.dumps({"event":"media","streamSid":stream_sid,"media":{"payload":silent}}))
                if sleep_s > 0.0: await asyncio.sleep(sleep_s)
                continue

            payload_b64 = await ulaw_out_queue.get()
            await ws_twilio.send_text(json.dumps({"event":"media","streamSid":stream_sid,"media":{"payload":payload_b64}}))
            ulaw_out_queue.task_done()

            ai_spoken_ms += CHUNK_MS
            played_since_pause_ms += CHUNK_MS

            # Micro-pausas opcionales
            if PAUSE_EVERY_MS > 0 and played_since_pause_ms >= PAUSE_EVERY_MS:
                if stream_sid:
                    silent = base64.b64encode(bytes([0xFF]) * ULAW_CHUNK_BYTES).decode('ascii')
                    await ws_twilio.send_text(json.dumps({"event":"media","streamSid":stream_sid,"media":{"payload":silent}}))
                played_since_pause_ms = 0
                next_t = time.monotonic()
                continue

            # Corta frases muy largas
            if MAX_UTTER_MS > 0 and ai_spoken_ms >= MAX_UTTER_MS:
                barge_active = True
                barge_last_voice = time.monotonic()

            # Ráfagas anti-backlog
            qsz = ulaw_out_queue.qsize()
            if qsz >= HWM_FRAMES:
                to_send = min(qsz - HWM_FRAMES, BURST_MAX)
                for _ in range(to_send):
                    payload_b64 = await ulaw_out_queue.get()
                    await ws_twilio.send_text(json.dumps({"event":"media","streamSid":stream_sid,"media":{"payload":payload_b64}}))
                    ulaw_out_queue.task_done()
                next_t = time.monotonic()
                continue

            # Pacing normal
            if sleep_s > 0.0:
                next_t += sleep_s
                delay = next_t - time.monotonic()
                if delay > 0: await asyncio.sleep(delay)
                else: next_t = time.monotonic()

    async def enqueue_ulaw_frames(ulaw_bytes: bytes):
        nonlocal ulaw_carry
        ulaw_carry.extend(ulaw_bytes)
        # Emitir solo frames completos de 160 bytes
        while len(ulaw_carry) >= ULAW_CHUNK_BYTES:
            frame = bytes(ulaw_carry[:ULAW_CHUNK_BYTES])
            await ulaw_out_queue.put(base64.b64encode(frame).decode('ascii'))
            del ulaw_carry[:ULAW_CHUNK_BYTES]

    async def enqueue_silence(ms: int):
        nonlocal ai_spoken_ms
        ai_spoken_ms = 0
        frames = max(1, ms // CHUNK_MS)
        chunk = bytes([0xFF]) * ULAW_CHUNK_BYTES
        b64 = base64.b64encode(chunk).decode("ascii")
        for _ in range(frames):
            await ulaw_out_queue.put(b64)

    async def connect_ai_after_start():
        nonlocal ws_ai, ai_spoken_ms, barge_active, speak_gate_until
        await start_evt.wait()
        try:
            headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "OpenAI-Beta": "realtime=v1"}
            ai_url = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"
            _log("Connecting to OpenAI Realtime…")
            ws_ai = await websockets.connect(ai_url, extra_headers=headers)
            _log("OpenAI Realtime connected")

            # Sesión: PCM16 24k E2E con modalidades audio+texto y VAD del servidor
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
            ai_spoken_ms = 0
            barge_active = False
            _log("Session configured; flushing buffered μ-law frames:", len(buffered_ulaw))

            # Volcar μ-law buffered → PCM y adjuntar a buffer de entrada
            while buffered_ulaw:
                ulaw = buffered_ulaw.pop(0)
                lin8 = audioop.ulaw2lin(ulaw, 2)
                pcm24 = up_8k_to_24k.convert(lin8)
                if pcm24:
                    await ws_ai.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(pcm24).decode("ascii"),
                    }))

            # Saludo posterior (forzando la misma voz)
            await asyncio.sleep(FOLLOWUP_GREETING_MS / 1000.0)
            try:
                await ws_ai.send(json.dumps({
                    "type": "response.create",
                    "response": {
                        "instructions": FOLLOWUP_GREETING_TEXT,
                        "voice": OPENAI_VOICE  # fuerza la misma voz en el saludo
                    }
                }))
            except Exception as e:
                _log("Optional greet failed:", e)

            # Bucle AI → Twilio con transcoding y captura LEAD
            try:
                txt_buf = ""
                async for raw in ws_ai:
                    evt = json.loads(raw)
                    t = evt.get("type")

                    # Reinicio de contadores al comenzar respuesta (y puerta de arranque)
                    if t in ("response.created", "response.started"):
                        ai_spoken_ms = 0
                        barge_active = False
                        speak_gate_until = time.monotonic() + (START_SPEAK_DELAY_MS / 1000.0)

                    # Captura de texto incremental (para LEAD)
                    if t in ("response.output_text.delta",):
                        delta = evt.get("delta") or evt.get("text") or ""
                        if delta:
                            txt_buf += delta
                            start_tag = f"<<{CAPTURE_TAG}>>"
                            end_tag = "<<END>>"
                            if start_tag in txt_buf and end_tag in txt_buf:
                                start_idx = txt_buf.rfind(start_tag)
                                end_idx = txt_buf.find(end_tag, start_idx)
                                if end_idx != -1:
                                    payload = txt_buf[start_idx+len(start_tag):end_idx].strip()
                                    _log(f"[{CAPTURE_TAG}]", payload)

                    # Audio saliente de la IA
                    if t in ("response.audio.delta", "response.output_audio.delta"):
                        if barge_active:
                            continue  # ignorar mientras el cliente habla
                        b64 = evt.get("audio") or evt.get("delta")
                        if b64 and stream_sid and started:
                            pcm24 = base64.b64decode(b64)
                            lin8k = down_24k_to_8k.convert(pcm24)
                            if not lin8k: continue
                            ulaw8k = audioop.lin2ulaw(lin8k, 2)
                            await enqueue_ulaw_frames(ulaw8k)

                    # Fin de respuesta
                    if t in ("response.completed", "response.stopped", "response.canceled"):
                        ai_spoken_ms = 0

            except (ConnectionClosedOK, ConnectionClosedError, asyncio.CancelledError):
                _log("AI socket closed")
            except Exception as e:
                _log("ai_to_twilio error:", e)
        finally:
            with contextlib.suppress(Exception):
                if ws_ai is not None:
                    await ws_ai.close()

    async def twilio_to_ai():
        nonlocal stream_sid, started, barge_active, barge_last_voice, vad_hot_ms
        try:
            while True:
                msg_text = await ws_twilio.receive_text()
                msg = json.loads(msg_text)
                ev = msg.get("event")

                if ev == "start":
                    stream_sid = msg["start"]["streamSid"]
                    started = True
                    _log("Twilio 'start' received; streamSid:", stream_sid)
                    start_evt.set()
                    await enqueue_silence(PREROLL_MS)

                elif ev == "media":
                    if not started: continue
                    b64 = msg["media"]["payload"]
                    ulaw = base64.b64decode(b64)

                    # VAD simple para barge-in
                    lin_probe = audioop.ulaw2lin(ulaw, 2)
                    rms = max(1, audioop.rms(lin_probe, 2))
                    db = 20.0 * math.log10(rms / 32767.0)
                    now = time.monotonic()
                    if db >= BARGE_VAD_DB:
                        vad_hot_ms = min(vad_hot_ms + CHUNK_MS, 2000)
                        if vad_hot_ms >= MIN_BARGE_SPEECH_MS:
                            barge_active = True
                            barge_last_voice = now
                            vad_hot_ms = 0
                            # Cancelar respuesta en curso
                            with contextlib.suppress(Exception):
                                if ws_ai is not None:
                                    await ws_ai.send(json.dumps({"type": "response.cancel"}))
                    else:
                        vad_hot_ms = 0
                        if barge_active and (now - barge_last_voice) * 1000.0 >= BARGE_RELEASE_MS:
                            barge_active = False

                    # Cliente → IA (24 kHz)
                    if ws_ai is not None:
                        pcm24 = up_8k_to_24k.convert(lin_probe)
                        if pcm24:
                            await ws_ai.send(json.dumps({
                                "type": "input_audio_buffer.append",
                                "audio": base64.b64encode(pcm24).decode("ascii"),
                            }))

                elif ev == "stop":
                    _log("Twilio 'stop' received; closing")
                    with contextlib.suppress(Exception):
                        if ws_ai is not None:
                            await ws_ai.send(json.dumps({"type": "input_audio_buffer.commit"}))
                    break

        except WebSocketDisconnect:
            _log("Twilio WS disconnect")
        except Exception as e:
            _log("twilio_to_ai error:", e)

    # Lanzar tareas
    task_connect = asyncio.create_task(connect_ai_after_start())
    task_twilio = asyncio.create_task(twilio_to_ai())
    task_sender = asyncio.create_task(twilio_sender())
    done, pending = await asyncio.wait(
        {task_connect, task_twilio, task_sender}, return_when=asyncio.FIRST_COMPLETED
    )
    for t in pending:
        t.cancel()
        with contextlib.suppress(Exception):
            await t

# Dev local
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("codigo_flask:app", host="0.0.0.0", port=8000)
