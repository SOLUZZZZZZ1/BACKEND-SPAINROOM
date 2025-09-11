import os
import json
import asyncio
import contextlib
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response, PlainTextResponse
import websockets

# ==== Config ====
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
OPENAI_REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"
TWILIO_WS_PATH = "/stream/twilio"

app = FastAPI(title="SpainRoom Voice Gateway")

# ==== HTTP ====
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
    # IMPORTANTE: sin track="both_tracks" (lanza 31941 con <Connect>)
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://backend-spainroom.onrender.com{TWILIO_WS_PATH}"
            statusCallback="https://backend-spainroom.onrender.com/diag/stream-log"
            statusCallbackEvent="start stop" />
  </Connect>
</Response>"""
    return Response(twiml, media_type="application/xml; charset=utf-8")

# ==== WS: Twilio ⇄ OpenAI Realtime (μ-law 8k passthrough) ====
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
            # 1) Sesión: μ-law 8k, VAD y MODALIDADES ['audio','text']
            await ws_ai.send(json.dumps({
                "type": "session.update",
                "session": {
                    "voice": "verse",
                    "modalities": ["audio", "text"],
                    "turn_detection": {"type": "server_vad", "create_response": True},
                    "input_audio_format": {"type": "g711_ulaw", "sample_rate_hz": 8000},
                    "output_audio_format": {"type": "g711_ulaw", "sample_rate_hz": 8000},
                    "instructions": (
                        "Eres 'SpainRoom'. Detecta si el usuario habla español o inglés y responde en ese idioma. "
                        "Cambia si cambia. Sé breve, natural, permite interrupciones (barge-in) y confirma datos sensibles."
                    )
                }
            }))

            # 2) Modelo -> Twilio (reenviamos μ-law base64 tal cual)
            async def ai_to_twilio():
                try:
                    async for raw in ws_ai:
                        evt = json.loads(raw)
                        t = evt.get("type")
                        # Traza
                        if t not in ("response.output_audio.delta", "response.audio.delta"):
                            print("RT EVT:", t)

                        if t in ("response.output_audio.delta", "response.audio.delta"):
                            # Algunas versiones envían 'delta', otras 'audio'
                            b64 = evt.get("delta") or evt.get("audio")
                            if b64 and stream_sid and started:
                                await ws_twilio.send_text(json.dumps({
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": b64}
                                }))
                        elif t == "error":
                            print("OPENAI REALTIME ERROR:", evt)
                except Exception as e:
                    print("ai_to_twilio error:", e)

            task = asyncio.create_task(ai_to_twilio())

            # 3) Twilio -> Modelo
            try:
                while True:
                    text = await ws_twilio.receive_text()
                    msg = json.loads(text)
                    ev = msg.get("event")

                    if ev == "start":
                        stream_sid = msg["start"]["streamSid"]
                        started = True
                        # Saludo inicial (usar modalidades de sesión)
                        await ws_ai.send(json.dumps({
                            "type": "response.create",
                            "response": {
                                "instructions": "Hola. Puedo atender en español o en inglés. ¿En qué puedo ayudarte?"
                            }
                        }))

                    elif ev == "media":
                        # μ-law 8k en base64 -> buffer del modelo tal cual
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("codigo_flask:app", host="0.0.0.0", port=8000)
