
# codigo_flask.py — v6 ULaw E2E + clocked sender (solución “voz lenta”)
# - Todo el camino en G.711 μ-law 8 kHz (sin transcodificar)
# - Emisor con reloj preciso (20 ms) y cola de salida
# - Say-first + preroll para eliminar chasquidos
import os
import json
import base64
import asyncio
import contextlib
import time
from typing import Optional, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response
import websockets
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

# ========= Config =========
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
OPENAI_VOICE = os.getenv("OPENAI_VOICE", "sage")
TWILIO_WS_PATH = os.getenv("TWILIO_WS_PATH", "/ws/twilio")
PUBLIC_WS_URL = os.getenv("PUBLIC_WS_URL")
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", "Eres Nora de SpainRoom. Responde de forma clara y breve.")

# Audio pacing
CHUNK_MS = int(os.getenv("CHUNK_MS", "20"))                   # 20 ms por frame
ULAW_CHUNK_BYTES = int(os.getenv("ULAW_CHUNK_BYTES", "160"))  # 20 ms @ 8 kHz μ-law
PACE_MS = int(os.getenv("PACE_MS", str(CHUNK_MS)))            # por defecto, tiempo real
PREROLL_MS = int(os.getenv("PREROLL_MS", "320"))              # μ-law silencio inicial tras start

SAYFIRST_TEXT = os.getenv("SAYFIRST_TEXT", "Hola, soy Nora de SpainRoom.")
FOLLOWUP_GREETING_MS = int(os.getenv("FOLLOWUP_GREETING_MS", "600"))
FOLLOWUP_GREETING_TEXT = os.getenv("FOLLOWUP_GREETING_TEXT", "¿En qué puedo ayudarte?")
DEBUG = os.getenv("DEBUG", "0") == "1"

# ========= App =========
app = FastAPI(title="SpainRoom Voice Realtime Bridge v6 ULaw E2E")

# ========= Util =========
def _log(*args):
    if DEBUG:
        print("[RT]", *args, flush=True)

def _mask_key(key: str) -> str:
    if not key:
        return "(vacío)"
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]

def _infer_ws_url(request: Request) -> str:
    if PUBLIC_WS_URL:
        return PUBLIC_WS_URL
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    proto = request.headers.get("x-forwarded-proto", "https")
    scheme = "wss" if proto == "https" else "ws"
    return f"{scheme}://{host}{TWILIO_WS_PATH}"

# ========= Health =========
@app.get("/health")
def health():
    return {"ok": True}

@app.get("/diag_key")
def diag_key():
    return {"OPENAI_API_KEY": _mask_key(OPENAI_API_KEY)}

# ========= TwiML =========
@app.get("/voice/answer")
@app.post("/voice/answer")
def voice_answer(request: Request):
    ws_url = _infer_ws_url(request)
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}"/>
  </Connect>
</Response>"""
    return Response(twiml, media_type="application/xml; charset=utf-8")

@app.get("/voice/answer_sayfirst")
@app.post("/voice/answer_sayfirst")
def voice_answer_sayfirst(request: Request):
    ws_url = _infer_ws_url(request)
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="es-ES">{SAYFIRST_TEXT}</Say>
  <Pause length="1"/>
  <Connect>
    <Stream url="{ws_url}"/>
  </Connect>
</Response>"""
    return Response(twiml, media_type="application/xml; charset=utf-8")

@app.get("/voice/fallback")
@app.post("/voice/fallback")
def voice_fallback():
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="es-ES">En este momento no puedo atenderle. Por favor, inténtelo de nuevo en unos minutos.</Say>
  <Hangup/>
</Response>"""
    return Response(twiml, media_type="application/xml; charset=utf-8")

@app.get("/voice/test_female")
@app.post("/voice/test_female")
def voice_test_female():
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="es-ES">Prueba del circuito. SpainRoom operativo.</Say>
</Response>"""
    return Response(twiml, media_type="application/xml; charset=utf-8")

# ========= WebSocket ULaw E2E =========
@app.websocket(TWILIO_WS_PATH)
async def twilio_stream(ws_twilio: WebSocket):
    await ws_twilio.accept(subprotocol="audio")
    _log("WS accepted (audio)")

    if not OPENAI_API_KEY:
        await ws_twilio.send_text(json.dumps({"event": "error", "message": "Falta OPENAI_API_KEY"}))
        await ws_twilio.close()
        return

    stream_sid: Optional[str] = None
    started = False
    ai_ready = asyncio.Event()
    start_evt = asyncio.Event()
    ws_ai: Optional[websockets.WebSocketClientProtocol] = None

    # Cola de salida y emisor con reloj
    ulaw_out_queue: asyncio.Queue = asyncio.Queue(maxsize=4000)

    async def twilio_sender():
        """Emisor con reloj: 1 frame cada PACE_MS; drenaje estable y sin ralentizar tono."""
        next_t = time.monotonic()
        sleep_s = max(0.0, PACE_MS / 1000.0)
        while True:
            payload_b64 = await ulaw_out_queue.get()
            await ws_twilio.send_text(json.dumps({
                "event": "media",
                "streamSid": stream_sid,
                "media": {"payload": payload_b64},
            }))
            ulaw_out_queue.task_done()
            if sleep_s > 0.0:
                next_t += sleep_s
                delay = next_t - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                else:
                    next_t = time.monotonic()
            else:
                await asyncio.sleep(0)

    async def enqueue_ulaw_frames(ulaw_bytes: bytes):
        for i in range(0, len(ulaw_bytes), ULAW_CHUNK_BYTES):
            chunk = ulaw_bytes[i:i+ULAW_CHUNK_BYTES]
            if chunk:
                await ulaw_out_queue.put(base64.b64encode(chunk).decode("ascii"))

    async def enqueue_silence(ms: int):
        frames = max(1, ms // CHUNK_MS)
        chunk = bytes([0xFF]) * ULAW_CHUNK_BYTES
        b64 = base64.b64encode(chunk).decode("ascii")
        for _ in range(frames):
            await ulaw_out_queue.put(b64)

    async def connect_ai_after_start():
        nonlocal ws_ai
        await start_evt.wait()
        try:
            headers = {
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1",
            }
            ai_url = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"
            _log("Connecting to OpenAI Realtime…")
            ws_ai = await websockets.connect(ai_url, extra_headers=headers)
            _log("OpenAI Realtime connected")

            # Sesión 100% μ-law 8 kHz
            await ws_ai.send(json.dumps({
                "type": "session.update",
                "session": {
                    "voice": OPENAI_VOICE,
                    "instructions": SYSTEM_PROMPT,
                    "input_audio_format": {"type": "g711_ulaw", "sample_rate_hz": 8000, "channels": 1},
                    "output_audio_format": {"type": "g711_ulaw", "sample_rate_hz": 8000, "channels": 1},
                    "turn_detection": {"type": "server_vad"},
                },
            }))
            ai_ready.set()
            _log("Session configured ULaw E2E")

            # Saludo de seguimiento (después de breve pausa)
            await asyncio.sleep(FOLLOWUP_GREETING_MS / 1000.0)
            try:
                await ws_ai.send(json.dumps({
                    "type": "response.create",
                    "response": {"instructions": FOLLOWUP_GREETING_TEXT}
                }))
            except Exception as e:
                _log("Optional greet failed:", e)

            # Bucle AI → Twilio (ULaw directo)
            try:
                async for raw in ws_ai:
                    evt = json.loads(raw)
                    t = evt.get("type")
                    if t in ("response.audio.delta", "response.output_audio.delta"):
                        b64 = evt.get("audio") or evt.get("delta")
                        if b64 and stream_sid and started:
                            ulaw_bytes = base64.b64decode(b64)
                            await enqueue_ulaw_frames(ulaw_bytes)
            except (ConnectionClosedOK, ConnectionClosedError, asyncio.CancelledError):
                _log("AI socket closed")
            except Exception as e:
                _log("ai_to_twilio error:", e)
        finally:
            with contextlib.suppress(Exception):
                if ws_ai is not None:
                    await ws_ai.close()

    async def twilio_to_ai():
        nonlocal stream_sid, started
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
                    if not started:
                        continue
                    b64 = msg["media"]["payload"]
                    if ai_ready.is_set() and ws_ai is not None:
                        await ws_ai.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": b64,  # μ-law directo
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

    # Lanzar tareas en paralelo (incluye emisor con reloj)
    task_connect = asyncio.create_task(connect_ai_after_start())
    task_twilio = asyncio.create_task(twilio_to_ai())
    task_sender = asyncio.create_task(twilio_sender())
    done, pending = await asyncio.wait({task_connect, task_twilio, task_sender}, return_when=asyncio.FIRST_COMPLETED)

    for t in pending:
        t.cancel()
        with contextlib.suppress(Exception):
            await t

    with contextlib.suppress(Exception):
        if ws_ai is not None:
            await ws_ai.close()
    with contextlib.suppress(Exception):
        await ws_twilio.close()

# Dev local
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("codigo_flask:app", host="0.0.0.0", port=8000)
