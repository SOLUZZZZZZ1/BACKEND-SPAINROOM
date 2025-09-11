# codigo_flask.py
import os
import json
import base64
import asyncio
import audioop
import contextlib
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response, PlainTextResponse
import websockets

# ========= Config =========
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
OPENAI_REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"

# Twilio <Connect><Stream> apuntará aquí
TWILIO_WS_PATH = "/stream/twilio"

# ========= App =========
app = FastAPI(title="SpainRoom Voice Gateway")

# ========= Utiles de audio =========
def resample_pcm16(pcm: bytes, src_hz: int, dst_hz: int) -> bytes:
    """Re-muestreo PCM16 mono con stdlib (audioop)."""
    if not pcm or src_hz == dst_hz:
        return pcm
    out, _ = audioop.ratecv(pcm, 2, 1, src_hz, dst_hz, None)
    return out

# ========= Rutas HTTP =========
@app.get("/voice/health")
def health():
    return {"ok": True, "service": "voice"}

@app.get("/diag/key")
def diag_key():
    return {"openai_key_configured": bool(OPENAI_API_KEY)}

@app.post("/diag/stream-log")
async def stream_log(req: Request):
    body = (await req.body()).decode(errors="ignore")
    print("TWILIO STREAM EVENT:", body)
    return PlainTextResponse("OK")

@app.post("/voice/say")
def voice_say():
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response><Say language="es-ES">Hola, prueba correcta.</Say></Response>"""
    return Response(twiml, media_type="application/xml; charset=utf-8")

@app.post("/voice/answer")
def voice_answer():
    """
    Twilio (A Call Comes In -> POST) devuelve TwiML de stream bidireccional.
    Sin 'track' (evita 31941). Con callback para ver start/media/stop en logs.
    """
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://backend-spainroom.onrender.com{TWILIO_WS_PATH}"
            statusCallback="https://backend-spainroom.onrender.com/diag/stream-log"
            statusCallbackEvent="start media stop" />
  </Connect>
</Response>"""
    return Response(twiml, media_type="application/xml; charset=utf-8")

# ========= WebSocket: Twilio ⇄ OpenAI Realtime (PCM16 16k <-> μ-law 8k) =========
@app.websocket(TWILIO_WS_PATH)
async def twilio_stream(ws_twilio: WebSocket):
    # Twilio usa Sec-WebSocket-Protocol: audio
    await ws_twilio.accept(subprotocol="audio")

    if not OPENAI_API_KEY:
        await ws_twilio.send_text(json.dumps({"event": "error", "message": "Falta OPENAI_API_KEY"}))
        await ws_twilio.close()
        return

    stream_sid: Optional[str] = None
    started = False

    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}",
               "OpenAI-Beta": "realtime=v1"}

    try:
        async with websockets.connect(OPENAI_REALTIME_URL, extra_headers=headers) as ws_ai:
            # 1) Sesión: el modelo trabaja en PCM16 a 16 kHz (limpio) y VAD servidor
            await ws_ai.send(json.dumps({
                "type": "session.update",
                "session": {
                    "voice": "verse",
                    "modalities": ["audio", "text"],  # requerido por Realtime
                    "turn_detection": {"type": "server_vad", "create_response": True},
                    "input_audio_format": {"type": "pcm16", "sample_rate_hz": 16000},
                    "output_audio_format": {"type": "pcm16", "sample_rate_hz": 16000},
                    "instructions": (
                        "Eres 'SpainRoom'. Detecta si el usuario habla español o inglés y responde en ese idioma. "
                        "Cambia si cambia. Sé breve, natural, permite interrupciones (barge-in) y confirma datos sensibles."
                    )
                }
            }))

            # 2) Modelo -> Twilio (PCM16 16k -> μ-law 8k)
            async def ai_to_twilio():
                try:
                    async for raw in ws_ai:
                        evt = json.loads(raw)
                        t = evt.get("type")
                        if t in ("response.output_audio.delta", "response.audio.delta"):
                            # Algunas versiones usan 'delta', otras 'audio'
                            b64_pcm = evt.get("delta") or evt.get("audio")
                            if not b64_pcm:
                                continue
                            pcm16_16k = base64.b64decode(b64_pcm)
                            pcm16_8k = resample_pcm16(pcm16_16k, 16000, 8000)
                            ulaw_8k = audioop.lin2ulaw(pcm16_8k, 2)  # PCM16 -> μ-law
                            if stream_sid and started:
                                await ws_twilio.send_text(json.dumps({
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": base64.b64encode(ulaw_8k).decode()}
                                }))
                        elif t == "error":
                            print("OPENAI REALTIME ERROR:", evt)
                        else:
                            # Traza ligera
                            if t not in ("response.created", "response.completed",
                                         "input_audio_buffer.collected"):
                                print("RT EVT:", t)
                except Exception as e:
                    print("ai_to_twilio error:", e)

            task = asyncio.create_task(ai_to_twilio())

            # 3) Twilio -> Modelo (μ-law 8k -> PCM16 16k)
            try:
                while True:
                    text = await ws_twilio.receive_text()
                    msg = json.loads(text)
                    ev = msg.get("event")

                    if ev == "start":
                        stream_sid = msg["start"]["streamSid"]
                        started = True
                        # Saludo inicial (hereda modalities de sesión)
                        await ws_ai.send(json.dumps({
                            "type": "response.create",
                            "response": {
                                "instructions": "Hola. Puedo atender en español o en inglés. ¿En qué puedo ayudarte?"
                            }
                        }))

                    elif ev == "media":
                        ulaw_b64 = msg["media"]["payload"]
                        ulaw_8k = base64.b64decode(ulaw_b64)
                        pcm16_8k = audioop.ulaw2lin(ulaw_8k, 2)            # μ-law -> PCM16 8k
                        pcm16_16k = resample_pcm16(pcm16_8k, 8000, 16000)   # a 16k para el modelo
                        await ws_ai.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(pcm16_16k).decode()
                        }))

                    elif ev == "stop":
                        break

            except WebSocketDisconnect:
                pass
            finally:
                task.cancel()
                with contextlib.suppress(Exception):
                    await task

    except Exception as e:
        print("Bridge error:", e)
        with contextlib.suppress(Exception):
            await ws_twilio.send_text(json.dumps({"event": "error", "message": str(e)}))
        with contextlib.suppress(Exception):
            await ws_twilio.close()

# Dev local opcional
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("codigo_flask:app", host="0.0.0.0", port=8000)
