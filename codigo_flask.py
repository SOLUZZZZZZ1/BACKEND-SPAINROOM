# codigo_flask.py
import os, json, asyncio, contextlib
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
app = FastAPI(title="SpainRoom Voice Gateway (μ-law passthrough)")

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
    """Prueba del circuito telefónico (sin streaming)"""
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="es-ES">Prueba del circuito. SpainRoom operativo.</Say>
</Response>"""
    return Response(twiml, media_type="application/xml; charset=utf-8")

# ========= TwiML STREAMING (GET+POST) =========
@app.get("/voice/answer")
@app.post("/voice/answer")
def voice_answer():
    """Inicia Media Streams bidireccional (sin track, evita 31941)"""
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://backend-spainroom.onrender.com{TWILIO_WS_PATH}" />
  </Connect>
</Response>"""
    return Response(twiml, media_type="application/xml; charset=utf-8")

# ========= WebSocket: Twilio ⇄ OpenAI Realtime (μ-law 8 kHz passthrough) =========
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

    # Helper: extraer base64 de audio que devuelva el modelo
    def get_ulaw_b64(evt: dict) -> Optional[str]:
        # Realtime puede usar distintos envelopes
        return evt.get("audio") or evt.get("delta") or None

    try:
        async with websockets.connect(OPENAI_REALTIME_URL, extra_headers=headers) as ws_ai:
            # 1) Sesión del modelo: μ-law 8k en entrada y salida + voz femenina + guardarraíles
            await ws_ai.send(json.dumps({
                "type": "session.update",
                "session": {
                    "voice": OPENAI_VOICE,
                    "modalities": ["audio", "text"],
                    "turn_detection": {"type": "server_vad", "create_response": True},
                    "input_audio_format":  {"type": "g711_ulaw", "sample_rate_hz": 8000},
                    "output_audio_format": {"type": "g711_ulaw", "sample_rate_hz": 8000},
                    "instructions": (
                        "Te llamas Nora y trabajas en SpainRoom (alquiler de HABITACIONES). "
                        "Voz FEMENINA, cercana y ágil (frases cortas). "
                        "Si preguntan por temas ajenos (muebles/IKEA, etc.), explica que ayudas con HABITACIONES "
                        "y redirige: «¿Eres propietario o inquilino?» "
                        "Responde en el idioma del usuario (ES/EN) y permite interrupciones (barge-in)."
                    )
                }
            }))

            # 2) Hilo: Modelo → Twilio (reenviamos μ-law base64 tal cual, sin dormir)
            async def ai_to_twilio():
                try:
                    async for raw in ws_ai:
                        evt = json.loads(raw)
                        t = evt.get("type")
                        if t in ("response.audio.delta", "response.output_audio.delta"):
                            ulaw_b64 = get_ulaw_b64(evt)
                            if ulaw_b64 and stream_sid and started:
                                await ws_twilio.send_text(json.dumps({
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": ulaw_b64}
                                }))
                        elif t == "error":
                            print("OPENAI REALTIME ERROR:", evt)
                        # else:  # activa si necesitas traza
                        #     print("RT EVT:", t)
                except Exception as e:
                    print("ai_to_twilio error:", e)

            task = asyncio.create_task(ai_to_twilio())

            # 3) Twilio → Modelo (μ-law base64 passthrough)
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
                        # μ-law 8k en base64 -> buffer del modelo sin transformar
                        await ws_ai.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": msg["media"]["payload"]
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
