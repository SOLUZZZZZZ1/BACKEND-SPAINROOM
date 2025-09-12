# codigo_flask.py — Frame-aligned 20 ms + resample con estado (anti-ruido/anti-lentitud)
import os
import json
import base64
import asyncio
import audioop
import contextlib
from typing import Optional, Tuple

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
import websockets
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

# ========= Config =========
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
OPENAI_REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"
# Voces soportadas: alloy, ash, ballad, coral, echo, sage, shimmer, verse, marin, cedar
OPENAI_VOICE = os.getenv("OPENAI_VOICE", "marin")  # femenina por defecto

TWILIO_WS_PATH = "/stream/twilio"

app = FastAPI(title="SpainRoom Voice Gateway (20ms aligned, clean)")

# ========= Health / Diag =========
@app.get("/voice/health")
def health():
    return {"ok": True, "service": "voice"}

@app.get("/diag/key")
def diag_key():
    return {"openai_key_configured": bool(OPENAI_API_KEY)}

# ========= TwiML (GET+POST) =========
@app.get("/voice/answer")
@app.post("/voice/answer")
def voice_answer():
    """Inicia Media Streams bidireccional."""
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://backend-spainroom.onrender.com{TWILIO_WS_PATH}" />
  </Connect>
</Response>"""
    return Response(twiml, media_type="application/xml; charset=utf-8")

# ========= Helpers de resample con estado =========
class RateCV:
    """Wrapper de audioop.ratecv con estado continuo."""
    def __init__(self, src_hz: int, dst_hz: int):
        self.src = src_hz
        self.dst = dst_hz
        self.state: Optional[Tuple] = None

    def __call__(self, pcm_bytes: bytes) -> bytes:
        if not pcm_bytes or self.src == self.dst:
            return pcm_bytes
        out, self.state = audioop.ratecv(pcm_bytes, 2, 1, self.src, self.dst, self.state)
        return out

# ========= WebSocket: Twilio ⇄ OpenAI Realtime =========
@app.websocket(TWILIO_WS_PATH)
async def twilio_stream(ws_twilio: WebSocket):
    # Twilio usa Sec-WebSocket-Protocol: "audio"
    await ws_twilio.accept(subprotocol="audio")

    if not OPENAI_API_KEY:
        await ws_twilio.send_text(json.dumps({"event":"error","message":"Falta OPENAI_API_KEY"}))
        await ws_twilio.close()
        return

    # Estados
    stream_sid: Optional[str] = None
    started = False

    # Buffers y resamplers con estado
    #   Modelo -> Twilio: 20 ms @16k PCM = 320 muestras = 640 bytes
    out_pcm16_16k_buf = bytearray()
    down_16k_to_8k = RateCV(16000, 8000)

    #   Twilio -> Modelo: 20 ms @8k PCM = 160 muestras = 320 bytes
    in_pcm16_8k_buf = bytearray()
    up_8k_to_16k = RateCV(8000, 16000)

    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "OpenAI-Beta": "realtime=v1"}

    try:
        async with websockets.connect(OPENAI_REALTIME_URL, extra_headers=headers) as ws_ai:
            # Sesión Realtime: PCM16 16k en entrada/salida (limpio) + voz
            await ws_ai.send(json.dumps({
                "type": "session.update",
                "session": {
                    "voice": OPENAI_VOICE,
                    "modalities": ["audio", "text"],  # requerido
                    "turn_detection": {"type": "server_vad", "create_response": True},
                    "input_audio_format":  {"type": "pcm16", "sample_rate_hz": 16000},
                    "output_audio_format": {"type": "pcm16", "sample_rate_hz": 16000},
                    "instructions": (
                        "Te llamas Nora y trabajas en SpainRoom (alquiler de HABITACIONES). "
                        "Voz FEMENINA, cercana y ágil (frases cortas). "
                        "Si preguntan por temas ajenos (muebles/limpieza/IKEA), explica que ayudas con HABITACIONES "
                        "y redirige: «¿Eres propietario o inquilino?» "
                        "Responde en el idioma del usuario (ES/EN) y permite interrupciones (barge-in)."
                    )
                }
            }))

            # ========== Modelo -> Twilio ==========
            async def ai_to_twilio():
                try:
                    async for raw in ws_ai:
                        evt = json.loads(raw)
                        et = evt.get("type")

                        if et in ("response.output_audio.delta", "response.audio.delta"):
                            # Llega PCM16 16k en base64
                            b64 = evt.get("delta") or evt.get("audio")
                            if not b64:
                                continue
                            out_pcm16_16k_buf.extend(base64.b64decode(b64))

                            # Procesar exactamente 20 ms por iteración (640 bytes)
                            FRAME_16K_BYTES = 640  # 20 ms @16k, 2 bytes/muestra
                            while len(out_pcm16_16k_buf) >= FRAME_16K_BYTES and stream_sid and started:
                                frame16 = bytes(out_pcm16_16k_buf[:FRAME_16K_BYTES])
                                del out_pcm16_16k_buf[:FRAME_16K_BYTES]

                                # 16k -> 8k con estado
                                frame8_pcm = down_16k_to_8k(frame16)  # 320 bytes PCM16 8k
                                # PCM16 -> μ-law (160 bytes)
                                frame8_ulaw = audioop.lin2ulaw(frame8_pcm, 2)

                                payload = base64.b64encode(frame8_ulaw).decode()
                                await ws_twilio.send_text(json.dumps({
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": payload}
                                }))
                                # Pacing exacto 20 ms por frame
                                await asyncio.sleep(0.02)

                        elif et == "error":
                            print("OPENAI REALTIME ERROR:", evt)

                except (ConnectionClosedOK, ConnectionClosedError, asyncio.CancelledError):
                    return
                except Exception as e:
                    print("ai_to_twilio error:", e)

            tx_task = asyncio.create_task(ai_to_twilio())

            # ========== Twilio -> Modelo ==========
            try:
                while True:
                    msg = json.loads(await ws_twilio.receive_text())
                    ev = msg.get("event")

                    if ev == "start":
                        stream_sid = msg["start"]["streamSid"]
                        started = True
                        # Saludo inicial
                        await ws_ai.send(json.dumps({
                            "type": "response.create",
                            "response": { "instructions": "Hola, soy Nora de SpainRoom. ¿En qué puedo ayudarte?" }
                        }))

                    elif ev == "media":
                        # μ-law 8k (b64) -> μ-law bytes
                        ulaw = base64.b64decode(msg["media"]["payload"])
                        # μ-law -> PCM16 8k
                        pcm8 = audioop.ulaw2lin(ulaw, 2)
                        in_pcm16_8k_buf.extend(pcm8)

                        # Procesar exactamente 20 ms @8k = 320 bytes PCM16
                        FRAME_8K_BYTES = 320
                        while len(in_pcm16_8k_buf) >= FRAME_8K_BYTES:
                            frame8 = bytes(in_pcm16_8k_buf[:FRAME_8K_BYTES])
                            del in_pcm16_8k_buf[:FRAME_8K_BYTES]

                            # 8k -> 16k con estado (640 bytes)
                            frame16 = up_8k_to_16k(frame8)
                            await ws_ai.send(json.dumps({
                                "type": "input_audio_buffer.append",
                                "audio": base64.b64encode(frame16).decode()
                            }))
                            # (Server VAD gestionará turnos; no dormimos aquí)

                    elif ev == "stop":
                        break

            except WebSocketDisconnect:
                pass
            finally:
                tx_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, ConnectionClosedOK, ConnectionClosedError):
                    await tx_task

    except (ConnectionClosedOK, ConnectionClosedError, asyncio.CancelledError):
        pass
    except Exception as e:
        print("Bridge error:", e)
        with contextlib.suppress(Exception):
            await ws_twilio.send_text(json.dumps({"event":"error","message":str(e)}))
    finally:
        with contextlib.suppress(Exception):
            await ws_twilio.close()

# Dev local opcional
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("codigo_flask:app", host="0.0.0.0", port=8000)
