
# codigo_flask.py — v4.4: pacing fijo 20 ms + preroll 500 ms + Say-first + conexión a OpenAI tras 'start'
import os
import json
import base64
import asyncio
import contextlib
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
PUBLIC_WS_URL = os.getenv("PUBLIC_WS_URL")  # e.g., wss://backend-spainroom.onrender.com/ws/twilio
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", "Eres Nora de SpainRoom. Responde de forma clara y breve.")
PREROLL_MS = int(os.getenv("PREROLL_MS", "500"))             # μ-law silencio inicial tras start
SLEEP_PER_CHUNK = 0.02  # pacing fijo: 20 ms por chunk de 160B μ-law
SAYFIRST_TEXT = os.getenv("SAYFIRST_TEXT", "Hola, soy Nora de SpainRoom.")
FOLLOWUP_GREETING_MS = int(os.getenv("FOLLOWUP_GREETING_MS", "700"))
FOLLOWUP_GREETING_TEXT = os.getenv("FOLLOWUP_GREETING_TEXT", "¿En qué puedo ayudarte?")
DEBUG = os.getenv("DEBUG", "1") == "1"

# ========= App =========
app = FastAPI(title="SpainRoom Voice Realtime Bridge v4.4")

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

async def _send_ulaw_silence(ws_twilio: WebSocket, stream_sid: str, ms: int):
    """Envía 'ms' ms de silencio μ-law (byte 0xFF) en frames de 20 ms (160 bytes)."""
    if not stream_sid or ms <= 0:
        return
    frames = max(1, ms // 20)
    chunk = bytes([0xFF]) * 160
    payload = base64.b64encode(chunk).decode("ascii")
    for _ in range(frames):
        await ws_twilio.send_text(json.dumps({
            "event": "media",
            "streamSid": stream_sid,
            "media": {"payload": payload},
        }))
        await asyncio.sleep(SLEEP_PER_CHUNK)

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
    """Modo directo: conecta el stream sin <Say> previo."""
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
    """Modo 'Say-first': Twilio dice un saludo y luego abre el stream (reduce chasquidos/ruido)."""
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

# Twilio fallback: siempre contesta con audio claro
@app.get("/voice/fallback")
@app.post("/voice/fallback")
def voice_fallback():
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="es-ES">En este momento no puedo atenderle. Por favor, inténtelo de nuevo en unos minutos.</Say>
  <Hangup/>
</Response>"""
    return Response(twiml, media_type="application/xml; charset=utf-8")

# Prueba Twilio TTS sin streaming
@app.get("/voice/test_female")
@app.post("/voice/test_female")
def voice_test_female():
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="es-ES">Prueba del circuito. SpainRoom operativo.</Say>
</Response>"""
    return Response(twiml, media_type="application/xml; charset=utf-8")

# ========= WebSocket: Twilio ⇄ OpenAI Realtime (μ-law 8k passthrough) =========
@app.websocket(TWILIO_WS_PATH)
async def twilio_stream(ws_twilio: WebSocket):
    await ws_twilio.accept(subprotocol="audio")
    print("[RT] WS accepted (audio)", flush=True)

    if not OPENAI_API_KEY:
        await ws_twilio.send_text(json.dumps({"event": "error", "message": "Falta OPENAI_API_KEY"}))
        await ws_twilio.close()
        return

    stream_sid: Optional[str] = None
    started = False
    ai_ready = asyncio.Event()
    start_evt = asyncio.Event()
    ws_ai: Optional[websockets.WebSocketClientProtocol] = None
    buffered_media: List[str] = []  # base64 μ-law frames de Twilio recibidos antes de que AI esté listo

    async def connect_ai_after_start():
        nonlocal ws_ai
        await start_evt.wait()
        try:
            headers = {
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1",
            }
            ai_url = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"
            print("[RT] Connecting to OpenAI Realtime…", flush=True)
            ws_ai = await websockets.connect(ai_url, extra_headers=headers)
            print("[RT] OpenAI Realtime connected", flush=True)

            # μ-law 8k E2E + VAD servidor
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
            print("[RT] Session configured; flushing buffered media:", len(buffered_media), flush=True)

            # Volcar audio que llegó antes
            while buffered_media:
                payload = buffered_media.pop(0)
                await ws_ai.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": payload,
                }))

            # Pequeño saludo de seguimiento para que el usuario sepa que ya puede hablar
            await asyncio.sleep(FOLLOWUP_GREETING_MS / 1000.0)
            try:
                await ws_ai.send(json.dumps({
                    "type": "response.create",
                    "response": {"instructions": FOLLOWUP_GREETING_TEXT}
                }))
            except Exception as e:
                print("[RT] Optional greet failed:", e, flush=True)

            # Bucle de lectura desde AI → Twilio
            try:
                async for raw in ws_ai:
                    evt = json.loads(raw)
                    t = evt.get("type")
                    if t in ("response.audio.delta", "response.output_audio.delta"):
                        ulaw_b64 = evt.get("audio") or evt.get("delta")
                        if ulaw_b64 and stream_sid and started:
                            data = base64.b64decode(ulaw_b64)
                            # Pacing estricto: 160B → dormir 20 ms por chunk
                            for i in range(0, len(data), 160):
                                chunk = data[i:i+160]
                                if not chunk:
                                    continue
                                payload = base64.b64encode(chunk).decode("ascii")
                                await ws_twilio.send_text(json.dumps({
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": payload},
                                }))
                                await asyncio.sleep(SLEEP_PER_CHUNK)
            except (ConnectionClosedOK, ConnectionClosedError, asyncio.CancelledError):
                print("[RT] AI socket closed", flush=True)
            except Exception as e:
                print("[RT] ai_to_twilio error:", e, flush=True)
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
                    print("[RT] Twilio 'start' received; streamSid:", stream_sid, flush=True)
                    start_evt.set()

                    # Silencio inicial para evitar chasquidos/módem
                    await _send_ulaw_silence(ws_twilio, stream_sid, PREROLL_MS)

                elif ev == "media":
                    if not started:
                        continue
                    payload = msg["media"]["payload"]
                    if ai_ready.is_set() and ws_ai is not None:
                        await ws_ai.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": payload,
                        }))
                    else:
                        buffered_media.append(payload)

                elif ev == "stop":
                    print("[RT] Twilio 'stop' received; closing", flush=True)
                    with contextlib.suppress(Exception):
                        if ws_ai is not None:
                            await ws_ai.send(json.dumps({"type": "input_audio_buffer.commit"}))
                    break

        except WebSocketDisconnect:
            print("[RT] Twilio WS disconnect", flush=True)
        except Exception as e:
            print("[RT] twilio_to_ai error:", e, flush=True)

    # Lanzar tareas en paralelo
    task_connect = asyncio.create_task(connect_ai_after_start())
    task_twilio = asyncio.create_task(twilio_to_ai())
    done, pending = await asyncio.wait({task_connect, task_twilio}, return_when=asyncio.FIRST_COMPLETED)

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
