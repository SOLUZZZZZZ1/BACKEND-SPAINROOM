# codigo_flask.py — Minimal μ-law 8k passthrough (no resample, no pauses)
import os
import json
import asyncio
import contextlib
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
import websockets
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
OPENAI_REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"
OPENAI_VOICE = os.getenv("OPENAI_VOICE", "sage")  # voz femenina ágil
TWILIO_WS_PATH = "/stream/twilio"

app = FastAPI(title="SpainRoom Voice Gateway (μ-law passthrough fast)")

@app.get("/voice/health")
def health():
    return {"ok": True, "service": "voice"}

@app.get("/diag/key")
def diag_key():
    return {"openai_key_configured": bool(OPENAI_API_KEY)}

@app.get("/voice/test_female")
@app.post("/voice/test_female")
def voice_test_female():
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="es-ES">Prueba del circuito. SpainRoom operativo.</Say>
</Response>"""
    return Response(twiml, media_type="application/xml; charset=utf-8")

@app.get("/voice/answer")
@app.post("/voice/answer")
def voice_answer():
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://backend-spainroom.onrender.com{TWILIO_WS_PATH}" />
  </Connect>
</Response>"""
    return Response(twiml, media_type="application/xml; charset=utf-8")

@app.websocket(TWILIO_WS_PATH)
async def twilio_stream(ws_twilio: WebSocket):
    await ws_twilio.accept(subprotocol="audio")

    if not OPENAI_API_KEY:
        await ws_twilio.send_text(json.dumps({"event":"error","message":"Falta OPENAI_API_KEY"}))
        await ws_twilio.close()
        return

    stream_sid: Optional[str] = None
    started = False
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "OpenAI-Beta": "realtime=v1"}

    try:
        async with websockets.connect(OPENAI_REALTIME_URL, extra_headers=headers) as ws_ai:
            # μ-law 8k end-to-end
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
                        "Si preguntan por temas ajenos (muebles/limpieza/IKEA), explica que ayudas con HABITACIONES "
                        "y redirige: «¿Eres propietario o inquilino?» "
                        "Responde en el idioma del usuario (ES/EN) y permite interrupciones (barge-in)."
                    )
                }
            }))

            async def ai_to_twilio():
                try:
                    async for raw in ws_ai:
                        evt = json.loads(raw)
                        t = evt.get("type")
                        if t in ("response.audio.delta", "response.output_audio.delta"):
                            ulaw_b64 = evt.get("audio") or evt.get("delta")
                            if ulaw_b64 and stream_sid and started:
                                # reenviar tal cual (sin troceo, sin pausas)
                                await ws_twilio.send_text(json.dumps({
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": ulaw_b64}
                                }))
                        elif t == "error":
                            print("OPENAI REALTIME ERROR:", evt)
                except (ConnectionClosedOK, ConnectionClosedError, asyncio.CancelledError):
                    return
                except Exception as e:
                    print("ai_to_twilio error:", e)

            task = asyncio.create_task(ai_to_twilio())

            try:
                while True:
                    msg_text = await ws_twilio.receive_text()
                    msg = json.loads(msg_text)
                    ev = msg.get("event")

                    if ev == "start":
                        stream_sid = msg["start"]["streamSid"]
                        started = True
                        await ws_ai.send(json.dumps({
                            "type": "response.create",
                            "response": { "instructions": "Hola, soy Nora de SpainRoom. ¿En qué puedo ayudarte?" }
                        }))

                    elif ev == "media":
                        # μ-law 8k base64 -> buffer del modelo (sin tocar)
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
                with contextlib.suppress(asyncio.CancelledError, ConnectionClosedOK, ConnectionClosedError):
                    await task

    except (ConnectionClosedOK, ConnectionClosedError, asyncio.CancelledError):
        pass
    except Exception as e:
        print("Bridge error:", e)
        with contextlib.suppress(Exception):
            await ws_twilio.send_text(json.dumps({"event":"error","message":str(e)}))
    finally:
        with contextlib.suppress(Exception):
            await ws_twilio.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("codigo_flask:app", host="0.0.0.0", port=8000)
