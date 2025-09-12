# codigo_flask.py  —  PASSTHROUGH G.711 μ-law 8k (sin resample, sin pausas)
import os, json, asyncio, contextlib
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, PlainTextResponse
import websockets
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

# ========= Config =========
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
OPENAI_REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"
# Voces soportadas: alloy, ash, ballad, coral, echo, sage, shimmer, verse, marin, cedar
OPENAI_VOICE = os.getenv("OPENAI_VOICE", "marin")  # femenina por defecto

TWILIO_WS_PATH = "/stream/twilio"

app = FastAPI(title="SpainRoom Voice Gateway (μ-law passthrough, no-slow)")

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
    """Inicia Media Streams bidireccional (sin track => evita 31941)."""
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://backend-spainroom.onrender.com{TWILIO_WS_PATH}" />
  </Connect>
</Response>"""
    return Response(twiml, media_type="application/xml; charset=utf-8")

# ========= WebSocket: Twilio ⇄ OpenAI Realtime (μ-law 8k passthrough) =========
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
            # Sesión: μ-law 8k extremo a extremo + voz femenina + guardarraíles SpainRoom
            await ws_ai.send(json.dumps({
                "type": "session.update",
                "session": {
                    "voice": OPENAI_VOICE,
                    "modalities": ["audio", "text"],                       # requerido
                    "turn_detection": {"type": "server_vad", "create_response": True},
                    "input_audio_format":  {"type": "g711_ulaw", "sample_rate_hz": 8000},
                    "output_audio_format": {"type": "g711_ulaw", "sample_rate_hz": 8000},
                    "instructions": (
                        "Te llamas Nora y trabajas en SpainRoom (alquiler de HABITACIONES). "
                        "Voz FEMENINA, cercana y ÁGIL (ritmo natural, frases cortas). "
                        "Si preguntan por temas ajenos (muebles/limpieza/IKEA), explica que ayudas con HABITACIONES "
                        "y redirige: «¿Eres propietario o inquilino?» "
                        "Responde en el idioma del usuario (ES/EN) y permite interrupciones (barge-in)."
                    )
                }
            }))

            # ---- Modelo -> Twilio (reenviar μ-law base64 tal cual; sin pausas) ----
            async def ai_to_twilio():
                try:
                    async for raw in ws_ai:
                        evt = json.loads(raw)
                        t = evt.get("type")

                        if t in ("response.audio.delta", "response.output_audio.delta"):
                            ulaw_b64 = evt.get("audio") or evt.get("delta")
                            if ulaw_b64 and stream_sid and started:
                                await ws_twilio.send_text(json.dumps({
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": ulaw_b64}
                                }))
                        elif t == "error":
                            print("OPENAI REALTIME ERROR:", evt)
                        # else:  # traza ligera opcional
                        #     print("RT EVT:", t)

                except (ConnectionClosedOK, ConnectionClosedError, asyncio.CancelledError):
                    return
                except Exception as e:
                    print("ai_to_twilio error:", e)

            task = asyncio.create_task(ai_to_twilio())

            # ---- Twilio -> Modelo (passthrough μ-law base64) ----
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
                        # μ-law 8k en base64 → buffer del modelo (SIN tocar)
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
            await ws_twilio.send_text(json.dumps({"event": "error", "message": str(e)}))
    finally:
        with contextlib.suppress(Exception):
            await ws_twilio.close()

# Dev local opcional
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("codigo_flask:app", host="0.0.0.0", port=8000)
