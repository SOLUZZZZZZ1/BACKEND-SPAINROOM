
# codigo_flask_bargein24k.py — SpainRoom Voice (Twilio 8 kHz ↔ OpenAI 24 kHz) con barge‑in
# - Mantiene tono correcto (24 kHz)
# - Permite interrumpir (barge-in con VAD simple)
# - Inserta micro‑pausas para sonar más humana
# - Limita frases largas y cancela respuesta si el cliente habla
#
# Requisitos: fastapi, uvicorn[standard], websockets
#   pip install fastapi "uvicorn[standard]" websockets
#
# ENV principales:
#   OPENAI_API_KEY, OPENAI_REALTIME_MODEL=gpt-4o-realtime-preview, OPENAI_VOICE=sage
#   TWILIO_WS_PATH=/ws/twilio, PUBLIC_WS_URL=wss://TU_DOMINIO/ws/twilio
#   CHUNK_MS=20, PACE_MS=20, HWM_FRAMES=50
#   PAUSE_EVERY_MS=1200
#   BARGE_VAD_DB=-28, BARGE_RELEASE_MS=450, BARGE_SEND_SILENCE=1, MAX_UTTER_MS=6000
#   SYSTEM_PROMPT="Eres Nora de SpainRoom..."
#   STRIPE_SECRET_KEY=sk_live_xxx   (solo para verificación en /diag_keys; no se usa aquí)
#
import os, json, base64, asyncio, contextlib, time, math
from typing import Optional, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response, JSONResponse
import websockets
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError
import audioop

# ========= Config =========
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
OPENAI_VOICE = os.getenv("OPENAI_VOICE", "sage")
TWILIO_WS_PATH = os.getenv("TWILIO_WS_PATH", "/ws/twilio")
PUBLIC_WS_URL = os.getenv("PUBLIC_WS_URL")
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT",
    "Eres Nora de SpainRoom. Habla ~15% más despacio, frases cortas y deja pausas naturales. "
    "Si el cliente empieza a hablar, para de inmediato y deja que termine. Responde en 1–2 frases.")

# Pacing / audio
CHUNK_MS = int(os.getenv("CHUNK_MS", "20"))                   # 20 ms por frame
ULAW_CHUNK_BYTES = int(os.getenv("ULAW_CHUNK_BYTES", "160"))  # 20 ms @ 8 kHz μ-law
PACE_MS = int(os.getenv("PACE_MS", str(CHUNK_MS)))            # por defecto, tiempo real
HWM_FRAMES = int(os.getenv("HWM_FRAMES", "50"))               # backlog alto (≈1s si 20 ms/frame)
BURST_MAX = int(os.getenv("BURST_MAX", "8"))                  # frames extra por tick para recuperar
PREROLL_MS = int(os.getenv("PREROLL_MS", "400"))
SAYFIRST_TEXT = os.getenv("SAYFIRST_TEXT", "Hola, soy Nora de SpainRoom.")
FOLLOWUP_GREETING_MS = int(os.getenv("FOLLOWUP_GREETING_MS", "600"))
FOLLOWUP_GREETING_TEXT = os.getenv("FOLLOWUP_GREETING_TEXT", "¿En qué puedo ayudarte?")
DEBUG = os.getenv("DEBUG", "0") == "1"

# Micro-pausas y barge-in
PAUSE_EVERY_MS = int(os.getenv("PAUSE_EVERY_MS", "1200"))     # inserta 1 frame de silencio cada X ms
BARGE_VAD_DB = float(os.getenv("BARGE_VAD_DB", "-28"))        # umbral dBFS para detectar voz del cliente
BARGE_RELEASE_MS = int(os.getenv("BARGE_RELEASE_MS", "450"))  # silencio para soltar barge-in
BARGE_SEND_SILENCE = os.getenv("BARGE_SEND_SILENCE", "1") == "1"
MAX_UTTER_MS = int(os.getenv("MAX_UTTER_MS", "6000"))         # cortar si la IA habla demasiado

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")        # no se usa aquí; solo diagnóstico

# ========= App =========
app = FastAPI(title="SpainRoom Voice Realtime")

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

# ========= Endpoints TwiML =========
@app.get("/voice/answer_sayfirst")
async def voice_answer(request: Request):
    ws_url = _infer_ws_url(request)
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="es-ES">{SAYFIRST_TEXT}</Say>
  <Connect>
    <Stream url="{ws_url}" />
  </Connect>
</Response>"""
    return Response(twiml, media_type="text/xml; charset=utf-8")

@app.get("/voice/fallback")
async def voice_fallback():
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="es-ES">Lo siento, ahora mismo no estoy disponible. Inténtalo de nuevo en un momento.</Say>
</Response>"""
    return Response(twiml, media_type="text/xml; charset=utf-8")

@app.get("/health")
async def health():
    return JSONResponse({"ok": True})

@app.get("/diag_keys")
async def diag_keys():
    return JSONResponse({
        "OPENAI_API_KEY": _mask(OPENAI_API_KEY),
        "STRIPE_SECRET_KEY": _mask(STRIPE_SECRET_KEY)
    })

# ========= WebSocket: Twilio ⇄ OpenAI (μ-law 8k <-> PCM16 24k) =========
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

    # Estado barge-in / control de turnos
    barge_active: bool = False
    barge_last_voice: float = 0.0
    ai_spoken_ms: int = 0

    # Resamplers
    up_8k_to_24k = RateCV(8000, 24000, 2, 1)
    down_24k_to_8k = RateCV(24000, 8000, 2, 1)

    # Cola de salida y emisor con reloj + ráfagas anti-backlog
    ulaw_out_queue: asyncio.Queue = asyncio.Queue(maxsize=4000)

    async def twilio_sender():
        """Emisor: 1 frame cada PACE_MS; ráfagas si hay backlog alto; micro‑pausas opcionales."""
        nonlocal stream_sid, barge_active, ai_spoken_ms
        next_t = time.monotonic()
        sleep_s = max(0.0, PACE_MS / 1000.0)
        played_since_pause_ms = 0
        while True:
            # Si el cliente está hablando, no reproducir la IA; mantener reloj con silencio
            if barge_active:
                # Vaciar cola para no acumular audio “viejo”
                try:
                    while True:
                        _ = ulaw_out_queue.get_nowait()
                        ulaw_out_queue.task_done()
                except asyncio.QueueEmpty:
                    pass
                if BARGE_SEND_SILENCE and stream_sid:
                    silent = base64.b64encode(bytes([0xFF]) * ULAW_CHUNK_BYTES).decode('ascii')
                    await ws_twilio.send_text(json.dumps({
                        "event": "media", "streamSid": stream_sid, "media": {"payload": silent}
                    }))
                if sleep_s > 0.0:
                    await asyncio.sleep(sleep_s)
                continue

            payload_b64 = await ulaw_out_queue.get()
            await ws_twilio.send_text(json.dumps({
                "event": "media", "streamSid": stream_sid, "media": {"payload": payload_b64},
            }))
            ulaw_out_queue.task_done()

            # Controles de naturalidad y corte
            ai_spoken_ms += CHUNK_MS
            played_since_pause_ms += CHUNK_MS
            if MAX_UTTER_MS > 0 and ai_spoken_ms >= MAX_UTTER_MS:
                # Cortar frases demasiado largas ⇒ activar barge-in suave
                barge_active = True
                barge_last_voice = time.monotonic()

            # Micro‑pausa: 1 frame de silencio cada X ms
            if PAUSE_EVERY_MS > 0 and played_since_pause_ms >= PAUSE_EVERY_MS:
                if stream_sid:
                    silent = base64.b64encode(bytes([0xFF]) * ULAW_CHUNK_BYTES).decode('ascii')
                    await ws_twilio.send_text(json.dumps({
                        "event": "media", "streamSid": stream_sid, "media": {"payload": silent}
                    }))
                played_since_pause_ms = 0
                next_t = time.monotonic()
                continue

            # Ráfagas anti-backlog
            qsz = ulaw_out_queue.qsize()
            if qsz >= HWM_FRAMES:
                to_send = min(qsz - HWM_FRAMES, BURST_MAX)
                for _ in range(to_send):
                    payload_b64 = await ulaw_out_queue.get()
                    await ws_twilio.send_text(json.dumps({
                        "event": "media", "streamSid": stream_sid, "media": {"payload": payload_b64},
                    }))
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
            _log("Connecting to OpenAI Realtime…")
            ws_ai = await websockets.connect(ai_url, extra_headers=headers)
            _log("OpenAI Realtime connected")

            # Sesión: PCM16 24k E2E con el modelo + turn detection server_vad
            await ws_ai.send(json.dumps({
                "type": "session.update",
                "session": {
                    "voice": OPENAI_VOICE,
                    "instructions": SYSTEM_PROMPT,
                    "input_audio_format": {"type": "pcm16", "sample_rate_hz": 24000, "channels": 1},
                    "output_audio_format": {"type": "pcm16", "sample_rate_hz": 24000, "channels": 1},
                    "turn_detection": {"type": "server_vad"},
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

            # Saludo posterior
            await asyncio.sleep(FOLLOWUP_GREETING_MS / 1000.0)
            try:
                await ws_ai.send(json.dumps({
                    "type": "response.create",
                    "response": {"instructions": FOLLOWUP_GREETING_TEXT}
                }))
            except Exception as e:
                _log("Optional greet failed:", e)

            # Bucle AI → Twilio con transcoding a μ-law 8k y encolado
            try:
                async for raw in ws_ai:
                    evt = json.loads(raw)
                    t = evt.get("type")

                    # Reinicio de contadores al comenzar respuesta
                    if t in ("response.created", "response.started"):
                        ai_spoken_ms = 0
                        barge_active = False

                    # Audio saliente de la IA
                    if t in ("response.audio.delta", "response.output_audio.delta"):
                        if barge_active:
                            # Mientras el cliente habla, ignoramos frames de IA
                            continue
                        b64 = evt.get("audio") or evt.get("delta")
                        if b64 and stream_sid and started:
                            pcm24 = base64.b64decode(b64)
                            lin8k = down_24k_to_8k.convert(pcm24)
                            if not lin8k: continue
                            ulaw8k = audioop.lin2ulaw(lin8k, 2)
                            await enqueue_ulaw_frames(ulaw8k)

                    # Fin de respuesta => reset contadores
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
        nonlocal stream_sid, started, barge_active, barge_last_voice
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

                    # VAD simple para detectar voz del cliente (umbral en dBFS)
                    lin_probe = audioop.ulaw2lin(ulaw, 2)
                    rms = max(1, audioop.rms(lin_probe, 2))
                    db = 20.0 * math.log10(rms / 32767.0)
                    now = time.monotonic()
                    if db >= BARGE_VAD_DB:
                        # Cliente hablando ⇒ activar barge-in y cancelar respuesta en curso
                        barge_active = True
                        barge_last_voice = now
                        if ws_ai is not None:
                            with contextlib.suppress(Exception):
                                await ws_ai.send(json.dumps({"type": "response.cancel"}))

                    else:
                        # Cliente en silencio: si ha pasado suficiente, liberar barge-in
                        if barge_active and (now - barge_last_voice) * 1000.0 >= BARGE_RELEASE_MS:
                            barge_active = False

                    # Reenvío de audio del cliente a la IA (24 kHz)
                    if ws_ai is not None:
                        lin8 = lin_probe
                        pcm24 = up_8k_to_24k.convert(lin8)
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

    async def _flush_ulaw_queue(q: asyncio.Queue):
        try:
            while True:
                _ = q.get_nowait()
                q.task_done()
        except asyncio.QueueEmpty:
            pass

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

    with contextlib.suppress(Exception):
        if ws_ai is not None:
            await ws_ai.close()
    with contextlib.suppress(Exception):
        await ws_twilio.close()

# Dev local
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("codigo_flask_bargein24k:app", host="0.0.0.0", port=8000)
