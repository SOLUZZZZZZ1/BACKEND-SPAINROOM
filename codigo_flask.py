
# codigo_flask.py — SpainRoom Voice (Twilio 8 kHz ↔ OpenAI 24 kHz) con barge‑in y TwiML GET/POST
# Incluye ruta "/" para ver si está vivo en Render.
import os, json, base64, asyncio, contextlib, time, math
from typing import Optional, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response, JSONResponse, HTMLResponse
import websockets
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError
import audioop

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
OPENAI_VOICE = os.getenv("OPENAI_VOICE", "sage")
TWILIO_WS_PATH = os.getenv("TWILIO_WS_PATH", "/ws/twilio")
PUBLIC_WS_URL = os.getenv("PUBLIC_WS_URL")
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT",
    "Eres Nora de SpainRoom. Habla ~15% más despacio, frases cortas y pausas naturales. "
    "Si el cliente empieza a hablar, para de inmediato y escucha. Responde en 1–2 frases.")

CHUNK_MS = int(os.getenv("CHUNK_MS", "20"))
ULAW_CHUNK_BYTES = int(os.getenv("ULAW_CHUNK_BYTES", "160"))
PACE_MS = int(os.getenv("PACE_MS", str(CHUNK_MS)))
HWM_FRAMES = int(os.getenv("HWM_FRAMES", "50"))
BURST_MAX = int(os.getenv("BURST_MAX", "8"))
PREROLL_MS = int(os.getenv("PREROLL_MS", "400"))
SAYFIRST_TEXT = os.getenv("SAYFIRST_TEXT", "Hola, soy Nora de SpainRoom.")
FOLLOWUP_GREETING_MS = int(os.getenv("FOLLOWUP_GREETING_MS", "600"))
FOLLOWUP_GREETING_TEXT = os.getenv("FOLLOWUP_GREETING_TEXT", "¿En qué puedo ayudarte?")
DEBUG = os.getenv("DEBUG", "0") == "1"

PAUSE_EVERY_MS = int(os.getenv("PAUSE_EVERY_MS", "1200"))
BARGE_VAD_DB = float(os.getenv("BARGE_VAD_DB", "-30"))
BARGE_RELEASE_MS = int(os.getenv("BARGE_RELEASE_MS", "500"))
BARGE_SEND_SILENCE = os.getenv("BARGE_SEND_SILENCE", "1") == "1"
MAX_UTTER_MS = int(os.getenv("MAX_UTTER_MS", "5500"))
CAPTURE_TAG = os.getenv("CAPTURE_TAG", "LEAD")

app = FastAPI(title="SpainRoom Voice Realtime", docs_url="/docs")

def _log(*a):
    if DEBUG: print("[SRV]", *a)

def _infer_ws_url(request: Request) -> str:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    proto = request.headers.get("x-forwarded-proto", "https")
    scheme = "wss" if proto == "https" else "ws"
    ws_path = TWILIO_WS_PATH if TWILIO_WS_PATH else "/ws/twilio"
    return f"{scheme}://{host}{ws_path}"

class RateCV:
    def __init__(self, src_rate: int, dst_rate: int, sampwidth: int = 2, channels: int = 1):
        self.src_rate = src_rate; self.dst_rate = dst_rate
        self.sampwidth = sampwidth; self.channels = channels
        self.state = None
    def convert(self, pcm: bytes) -> bytes:
        if not pcm: return b""
        out, self.state = audioop.ratecv(pcm, self.sampwidth, self.channels,
                                         self.src_rate, self.dst_rate, self.state)
        return out

# ========== Rutas de diagnóstico rápidas ==========
@app.get("/")
async def root(request: Request):
    ws_url = _infer_ws_url(request)
    html = f"""
    <h1>SpainRoom Voice</h1>
    <ul>
      <li>/health</li>
      <li>/docs</li>
      <li>/voice/answer_sayfirst (GET/POST) — WS: <code>{ws_url}</code></li>
    </ul>
    """
    return HTMLResponse(html)

@app.get("/health")
async def health():
    return JSONResponse({"ok": True})

@app.get("/diag_keys")
async def diag_keys():
    def _mask(key: str) -> str:
        if not key: return ""
        return key[:4] + "*" * max(0, len(key)-8) + key[-4:] if len(key)>8 else "*" * len(key)
    from os import getenv
    return JSONResponse({
        "OPENAI_API_KEY": _mask(getenv("OPENAI_API_KEY","")),
    })

# ========== TwiML (GET/POST) ==========
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

# ========== WebSocket Twilio ⇄ OpenAI (μ-law 8k <-> PCM16 24 kHz) ==========
@app.websocket(TWILIO_WS_PATH if TWILIO_WS_PATH else "/ws/twilio")
async def twilio_stream(ws_twilio: WebSocket):
    await ws_twilio.accept(subprotocol="audio")
    _log("WS accepted (audio)")

    stream_sid: Optional[str] = None
    started = False
    ai_ready = asyncio.Event()
    start_evt = asyncio.Event()
    ws_ai: Optional[websockets.WebSocketClientProtocol] = None
    buffered_ulaw: List[bytes] = []

    barge_active: bool = False
    barge_last_voice: float = 0.0
    ai_spoken_ms: int = 0

    up_8k_to_24k = RateCV(8000, 24000, 2, 1)
    down_24k_to_8k = RateCV(24000, 8000, 2, 1)

    ulaw_out_queue: asyncio.Queue = asyncio.Queue(maxsize=4000)

    async def twilio_sender():
        nonlocal stream_sid, barge_active, ai_spoken_ms
        next_t = time.monotonic()
        sleep_s = max(0.0, PACE_MS / 1000.0)
        played_since_pause_ms = 0
        while True:
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
                if sleep_s > 0.0:
                    await asyncio.sleep(sleep_s)
                continue

            payload_b64 = await ulaw_out_queue.get()
            await ws_twilio.send_text(json.dumps({"event":"media","streamSid":stream_sid,"media":{"payload":payload_b64}}))
            ulaw_out_queue.task_done()

            ai_spoken_ms += CHUNK_MS
            played_since_pause_ms += CHUNK_MS

            if PAUSE_EVERY_MS > 0 and played_since_pause_ms >= PAUSE_EVERY_MS:
                if stream_sid:
                    silent = base64.b64encode(bytes([0xFF]) * ULAW_CHUNK_BYTES).decode('ascii')
                    await ws_twilio.send_text(json.dumps({"event":"media","streamSid":stream_sid,"media":{"payload":silent}}))
                played_since_pause_ms = 0
                next_t = time.monotonic()
                continue

            if MAX_UTTER_MS > 0 and ai_spoken_ms >= MAX_UTTER_MS:
                barge_active = True
                barge_last_voice = time.monotonic()

            qsz = ulaw_out_queue.qsize()
            if qsz >= HWM_FRAMES:
                to_send = min(qsz - HWM_FRAMES, BURST_MAX)
                for _ in range(to_send):
                    payload_b64 = await ulaw_out_queue.get()
                    await ws_twilio.send_text(json.dumps({"event":"media","streamSid":stream_sid,"media":{"payload":payload_b64}}))
                    ulaw_out_queue.task_done()
                next_t = time.monotonic()
                continue

            if sleep_s > 0.0:
                next_t += sleep_s
                delay = next_t - time.monotonic()
                if delay > 0: await asyncio.sleep(delay)
                else: next_t = time.monotonic()

    async def enqueue_ulaw_frames(ulaw_bytes: bytes):
        for i in range(0, len(ulaw_bytes), ULAW_CHUNK_BYTES):
            chunk = ulaw_bytes[i:i+ULAW_CHUNK_BYTES]
            if not chunk: continue
            await ulaw_out_queue.put(base64.b64encode(chunk).decode("ascii"))

    async def enqueue_silence(ms: int):
        nonlocal ai_spoken_ms
        ai_spoken_ms = 0
        frames = max(1, ms // CHUNK_MS)
        chunk = bytes([0xFF]) * ULAW_CHUNK_BYTES
        b64 = base64.b64encode(chunk).decode("ascii")
        for _ in range(frames):
            await ulaw_out_queue.put(b64)

    async def connect_ai_after_start():
        nonlocal ws_ai, ai_spoken_ms, barge_active
        await start_evt.wait()
        try:
            headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "OpenAI-Beta": "realtime=v1"}
            ai_url = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"
            ws_ai = await websockets.connect(ai_url, extra_headers=headers)

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

            while buffered_ulaw:
                ulaw = buffered_ulaw.pop(0)
                lin8 = audioop.ulaw2lin(ulaw, 2)
                pcm24 = up_8k_to_24k.convert(lin8)
                if pcm24:
                    await ws_ai.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(pcm24).decode("ascii"),
                    }))

            await asyncio.sleep(FOLLOWUP_GREETING_MS / 1000.0)
            with contextlib.suppress(Exception):
                await ws_ai.send(json.dumps({"type":"response.create","response":{"instructions":FOLLOWUP_GREETING_TEXT}}))

            try:
                txt_buf = ""
                async for raw in ws_ai:
                    evt = json.loads(raw); t = evt.get("type")
                    if t in ("response.created","response.started"):
                        ai_spoken_ms = 0; barge_active = False
                    if t in ("response.output_text.delta",):
                        # Acumular texto para capturar LEAD JSON
                        txt = evt.get("delta") or evt.get("text") or ""
                        if txt:
                            txt_buf += txt
                            start_tag = f"<<{CAPTURE_TAG}>>"
                            end_tag = "<<END>>"
                            if start_tag in txt_buf and end_tag in txt_buf:
                                s = txt_buf.split(start_tag,1)[1]
                                payload = s.split(end_tag,1)[0]
                                with contextlib.suppress(Exception):
                                    data = json.loads(payload)
                                    print("[LEAD]", data)
                                # limpiar para evitar repeticiones
                                txt_buf = ""
                    elif t in ("response.audio.delta","response.output_audio.delta"):
                        if barge_active: continue
                        b64 = evt.get("audio") or evt.get("delta")
                        if b64 and started and stream_sid:
                            pcm24 = base64.b64decode(b64)
                            lin8k = down_24k_to_8k.convert(pcm24)
                            if not lin8k: continue
                            ulaw8k = audioop.lin2ulaw(lin8k, 2)
                            await enqueue_ulaw_frames(ulaw8k)
                    if t in ("response.completed","response.stopped","response.canceled"):
                        ai_spoken_ms = 0
            except (ConnectionClosedOK, ConnectionClosedError, asyncio.CancelledError):
                pass
        finally:
            with contextlib.suppress(Exception):
                if ws_ai is not None: await ws_ai.close()

    async def twilio_to_ai():
        nonlocal stream_sid, started, barge_active, barge_last_voice
        try:
            while True:
                msg_text = await ws_twilio.receive_text()
                msg = json.loads(msg_text); ev = msg.get("event")

                if ev == "start":
                    stream_sid = msg["start"]["streamSid"]; started = True
                    start_evt.set()
                    await enqueue_silence(PREROLL_MS)

                elif ev == "media":
                    if not started: continue
                    ulaw = base64.b64decode(msg["media"]["payload"])

                    lin_probe = audioop.ulaw2lin(ulaw, 2)
                    rms = max(1, audioop.rms(lin_probe, 2))
                    db = 20.0 * math.log10(rms / 32767.0)
                    now = time.monotonic()
                    if db >= BARGE_VAD_DB:
                        barge_active = True; barge_last_voice = now
                        with contextlib.suppress(Exception):
                            if ws_ai is not None:
                                await ws_ai.send(json.dumps({"type":"response.cancel"}))
                    else:
                        if barge_active and (now - barge_last_voice) * 1000.0 >= BARGE_RELEASE_MS:
                            barge_active = False

                    if ws_ai is not None:
                        pcm24 = RateCV(8000,24000,2,1).convert(lin_probe)
                        if pcm24:
                            await ws_ai.send(json.dumps({
                                "type":"input_audio_buffer.append",
                                "audio": base64.b64encode(pcm24).decode("ascii"),
                            }))

                elif ev == "stop":
                    with contextlib.suppress(Exception):
                        if ws_ai is not None:
                            await ws_ai.send(json.dumps({"type":"input_audio_buffer.commit"}))
                    break

        except WebSocketDisconnect:
            pass
        except Exception:
            pass

    task_connect = asyncio.create_task(connect_ai_after_start())
    task_twilio = asyncio.create_task(twilio_to_ai())
    task_sender = asyncio.create_task(twilio_sender())
    await asyncio.wait({task_connect, task_twilio, task_sender}, return_when=asyncio.FIRST_COMPLETED)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("codigo_flask:app", host="0.0.0.0", port=8000)
