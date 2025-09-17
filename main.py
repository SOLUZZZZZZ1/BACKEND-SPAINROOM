import os
import json
import base64
import asyncio
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, PlainTextResponse
import websockets
import numpy as np

# ========= CONFIG =========
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
OPENAI_REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"

# Endpoint WS que Twilio llamará en el <Stream url="...">
TWILIO_WS_PATH = "/stream/twilio"

# ========= APP =========
app = FastAPI(title="SpainRoom Voice Gateway")

# ========= UTIL: µ-law <-> PCM16 y re-muestreo =========
MU = 255.0

def mulaw_decode(ulaw_bytes: bytes) -> np.ndarray:
    """Convierte bytes µ-law (8k) a PCM16 numpy array."""
    u = np.frombuffer(ulaw_bytes, dtype=np.uint8).astype(np.float32)
    u = 255.0 - u
    sign = np.where(u >= 128, -1.0, 1.0)
    exponent = (u % 128) / 16.0
    mantissa = (u % 16) / 16.0
    x = sign * ((1.0 / MU) * ((1.0 + MU) ** (exponent + mantissa) - 1.0))
    pcm16 = np.clip(x * 32767.0, -32768, 32767).astype(np.int16)
    return pcm16

def mulaw_encode(pcm16: np.ndarray) -> bytes:
    """Convierte PCM16 numpy array a bytes µ-law (8k)."""
    x = np.clip(pcm16.astype(np.float32) / 32768.0, -1.0, 1.0)
    sign = np.sign(x)
    y = np.log1p(MU * np.abs(x)) / np.log1p(MU)
    # Mapear a 8 bits µ-law:
    # Nota: Implementación simplificada y práctica para voz telefónica
    u = (1 - (sign < 0).astype(np.uint8)) * 0x7F  # bit de signo
    mag = (y * 127.0).astype(np.uint8)
    out = (u & 0x80) | (127 - mag)
    return out.tobytes()

def resample_linear(x: np.ndarray, src_hz: int, dst_hz: int) -> np.ndarray:
    if src_hz == dst_hz or x.size == 0:
        return x
    ratio = dst_hz / src_hz
    idx = np.arange(0, int(len(x) * ratio), 1.0) / ratio
    idx0 = np.floor(idx).astype(int)
    idx1 = np.minimum(idx0 + 1, len(x) - 1)
    frac = idx - idx0
    y = (1.0 - frac) * x[idx0] + frac * x[idx1]
    return y.astype(x.dtype)

# ========= RUTAS HTTP =========
@app.get("/voice/health")
def health():
    return PlainTextResponse("OK")

@app.post("/voice/answer")
def answer():
    """
    Twilio: A Call Comes In (POST) -> devuelve TwiML que abre el stream WS bidireccional.
    """
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://backend-spainroom.onrender.com{TWILIO_WS_PATH}" />
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml; charset=utf-8")

# ========= GATEWAY WS: Twilio <-> OpenAI Realtime =========
@app.websocket(TWILIO_WS_PATH)
async def twilio_stream(ws_twilio: WebSocket):
    """
    Puentea el audio de la llamada con OpenAI Realtime:
    - Recibe media µ-law 8k de Twilio, lo transforma a PCM16 16k y lo envía al modelo.
    - Recibe audio PCM16 16k del modelo, lo transforma a µ-law 8k y se lo envía a Twilio.
    - Idioma: el modelo detecta y responde en ES/EN automáticamente.
    """
    await ws_twilio.accept()

    if not OPENAI_API_KEY:
        # Si falta API key, avisamos y cerramos
        await ws_twilio.send_text(json.dumps({
            "event": "error",
            "message": "OPENAI_API_KEY no configurada en el servidor."
        }))
        await ws_twilio.close()
        return

    stream_sid: Optional[str] = None

    # Conexión WS con OpenAI Realtime
    openai_headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "OpenAI-Beta": "realtime=v1",
    }

    try:
        async with websockets.connect(OPENAI_REALTIME_URL, extra_headers=openai_headers) as ws_ai:
            # Configuramos la sesión (voz + VAD + instrucciones ES/EN)
            session_update = {
                "type": "session.update",
                "session": {
                    "voice": "verse",  # puedes cambiar: alloy/verse/etc.
                    "instructions": (
                        "Eres 'SpainRoom'. Habla con voz natural. "
                        "Detecta automáticamente si el llamante habla español o inglés y responde SIEMPRE en ese idioma. "
                        "Si el usuario cambia de idioma, cambia tú también. "
                        "Sé breve, amable, permite interrupciones (barge-in) y pide confirmación cuando tomes datos."
                    ),
                    "turn_detection": { "type": "server_vad", "create_response": True }
                }
            }
            await ws_ai.send(json.dumps(session_update))

            # Tarea que escucha al modelo y reenvía audio a Twilio
            async def forward_ai_to_twilio():
                try:
                    async for raw in ws_ai:
                        evt = json.loads(raw)
                        t = evt.get("type")

                        if t == "response.audio.delta":
                            # Audio PCM16 16k -> µ-law 8k -> base64 -> Twilio
                            chunk_pcm16 = base64.b64decode(evt["audio"])
                            pcm = np.frombuffer(chunk_pcm16, dtype=np.int16)
                            pcm_8k = resample_linear(pcm, 16000, 8000)
                            ulaw = mulaw_encode(pcm_8k)
                            payload = base64.b64encode(ulaw).decode()

                            if stream_sid:
                                await ws_twilio.send_text(json.dumps({
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": payload}
                                }))

                        # (Opcional) logs/diagnóstico:
                        # elif t in ("response.created","response.completed","input_audio_buffer.collected"):
                        #     print("AI evt:", t)
                except WebSocketDisconnect:
                    pass
                except Exception as e:
                    try:
                        await ws_twilio.send_text(json.dumps({"event":"error","message":f"AI bridge error: {e}"}))
                    except Exception:
                        pass

            ai_task = asyncio.create_task(forward_ai_to_twilio())

            # Bucle principal: recibir Twilio y empujar al modelo
            try:
                while True:
                    msg_text = await ws_twilio.receive_text()
                    msg = json.loads(msg_text)
                    ev = msg.get("event")

                    if ev == "start":
                        stream_sid = msg["start"]["streamSid"]

                    elif ev == "media":
                        # Twilio -> µ-law 8k (b64) -> PCM16 8k -> PCM16 16k -> b64 -> modelo
                        ulaw_b64 = msg["media"]["payload"]
                        ulaw = base64.b64decode(ulaw_b64)
                        pcm_8k = mulaw_decode(ulaw)
                        pcm_16k = resample_linear(pcm_8k, 8000, 16000)
                        await ws_ai.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(pcm_16k.tobytes()).decode()
                        }))

                    elif ev == "mark":
                        # Ignorado (marcas de sincronización de Twilio)
                        pass

                    elif ev == "stop":
                        break

            except WebSocketDisconnect:
                # Twilio colgó
                pass
            finally:
                # Cancelar la tarea lectora del modelo
                ai_task.cancel()
                with contextlib.suppress(Exception):
                    await ai_task

    except Exception as e:
        # Error al conectar con el Realtime o durante el puente
        try:
            await ws_twilio.send_text(json.dumps({"event": "error", "message": str(e)}))
        except Exception:
            pass
        finally:
            with contextlib.suppress(Exception):
                await ws_twilio.close()
