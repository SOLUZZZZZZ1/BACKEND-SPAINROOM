# codigo_flask.py
import os
import json
import base64
import asyncio
import audioop
import contextlib
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, PlainTextResponse
import websockets

# ========= Config =========
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
OPENAI_REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"
# Voces soportadas: alloy, ash, ballad, coral, echo, sage, shimmer, verse, marin, cedar
OPENAI_VOICE = os.getenv("OPENAI_VOICE", "shimmer")

TWILIO_WS_PATH = "/stream/twilio"

# ========= App =========
app = FastAPI(title="SpainRoom Voice Gateway (VOZ LITE CHUNKED)")

# ========= Utils =========
def resample_pcm16(pcm: bytes, src_hz: int, dst_hz: int) -> bytes:
    """Re-muestreo PCM16 mono con stdlib (audioop)."""
    if not pcm or src_hz == dst_hz:
        return pcm
    out, _ = audioop.ratecv(pcm, 2, 1, src_hz, dst_hz, None)
    return out

# ========= Health / Diag =========
@app.get("/voice/health")
def health():
    return {"ok": True, "service": "voice"}

@app.get("/diag/key")
def diag_key():
    return {"openai_key_configured": bool(OPENAI_API_KEY)}

# ========= TwiML PRUEBA (GET+POST) =========
@app.get("/voice/test_female")
@app.post("/voice/test_female")
def voice_test_female():
    """Prueba del circuito telefónico con TTS Twilio (sin streaming)."""
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="es-ES">Prueba de voz femenina en el circuito telefónico. SpainRoom operativo.</Say>
</Response>"""
    return Response(twiml, media_type="application/xml; charset=utf-8")

# ========= TwiML STREAMING (GET+POST) =========
@app.get("/voice/answer")
@app.post("/voice/answer")
def voice_answer():
    """TwiML de stream bidireccional (sin track, evita 31941)."""
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://backend-spainroom.onrender.com{TWILIO_WS_PATH}" />
  </Connect>
</Response>"""
    return Response(twiml, media_type="application/xml; charset=utf-8")

# ========= WebSocket: Twilio ⇄ OpenAI Realtime =========
@app.websocket(TWILIO_WS_PATH)
async def twilio_stream(ws_twilio: WebSocket):
    # Twilio envía Sec-WebSocket-Protocol: audio → acéptalo
    await ws_twilio.accept(subprotocol="audio")

    if not OPENAI_API_KEY:
        await ws_twilio.send_text(json.dumps({"event": "error", "message": "Falta OPENAI_API_KEY"}))
        await ws_twilio.close()
        return

    stream_sid: Optional[str] = None
    started = False

    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "OpenAI-Beta": "realtime=v1"}

    try:
        async with websockets.connect(OPENAI_REALTIME_URL, extra_headers=headers) as ws_ai:
            # 1) Sesión: PCM16 16k + voz femenina + VAD + guardarraíles SpainRoom
            await ws_ai.send(json.dumps({
                "type": "session.update",
                "session": {
                    "voice": OPENAI_VOICE,
                    "modalities": ["audio", "text"],  # requerido por Realtime
                    "turn_detection": {"type": "server_vad", "create_response": True},
                    "input_audio_format":  {"type": "pcm16", "sample_rate_hz": 16000},
                    "output_audio_format": {"type": "pcm16", "sample_rate_hz": 16000},
                    "instructions": (
                        "Te llamas Nora y trabajas en SpainRoom (alquiler de HABITACIONES). "
                        "Voz FEMENINA, cercana y ágil (ritmo natural, frases cortas). "
                        "Si el usuario pregunta por temas ajenos (muebles, tiendas, etc.), "
                        "indica cortésmente que ayudas con HABITACIONES y redirige: "
                        "«Puedo ayudarte con habitaciones. ¿Eres propietario o inquilino?» "
                        "Responde en el idioma del usuario (ES/EN) y cambia si el usuario cambia. "
                        "Permite interrupciones (barge-in) y mantén tono profesional."
                    )
                }
            }))

            # 2) Modelo → Twilio (PCM16 16k → μ-law 8k en chunks de 20 ms, sin freno)
            async def ai_to_twilio():
                try:
                    async for raw in ws_ai:
                        evt = json.loads(raw)
                        t = evt.get("type")

                        if t in ("response.output_audio.delta", "response.audio.delta"):
                            b64_pcm = evt.get("delta") or evt.get("audio")
                            if not b64_pcm:
                                continue
                            pcm16_16k = base64.b64decode(b64_pcm)
                            pcm16_8k  = resample_pcm16(pcm16_16k, 16000, 8000)
                            ulaw_8k   = audioop.lin2ulaw(pcm16_8k, 2)  # μ-law 8k

                            if stream_sid and started and ulaw_8k:
                                CHUNK = 160  # 20 ms @ 8kHz μ-law (1 byte/muestra)
                                for i in range(0, len(ulaw_8k), CHUNK):
                                    payload = base64.b64encode(ulaw_8k[i:i+CHUNK]).decode()
                                    await ws_twilio.send_text(json.dumps({
                                        "event": "media",
                                        "streamSid": stream_sid,
                                        "media": {"payload": payload}
                                    }))
                                    await asyncio.sleep(0)  # sin retraso artificial

                        elif t == "error":
                            print("OPENAI REALTIME ERROR:", evt)
                        # else:
                        #     print("RT EVT:", t)

                except Exception as e:
                    print("ai_to_twilio error:", e)

            task = asyncio.create_task(ai_to_twilio())

            # 3) Twilio → Modelo (μ-law 8k → PCM16 16k)
            try:
                while True:
                    msg_text = await ws_twilio.receive_text()
                    msg = json.loads(msg_text)
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
                        ulaw_b64 = msg["media"]["payload"]
                        ulaw_8k  = base64.b64decode(ulaw_b64)
                        pcm16_8k = audioop.ulaw2lin(ulaw_8k, 2)
                        pcm16_16k= resample_pcm16(pcm16_8k, 8000, 16000)
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
