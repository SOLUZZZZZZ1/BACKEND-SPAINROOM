# codigo_flask.py
import os, json, asyncio, contextlib
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response, PlainTextResponse
import websockets

# ======= Config =======
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
OPENAI_REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"
TWILIO_WS_PATH = "/stream/twilio"

# ======= App =======
app = FastAPI(title="SpainRoom Voice Gateway")

# ======= Rutas HTTP =======
@app.get("/voice/health")
def health():
    return {"ok": True, "service": "voice"}

@app.get("/diag/key")
def diag_key():
    return {"openai_key_configured": bool(OPENAI_API_KEY)}

@app.post("/diag/stream-log")
async def stream_log(req: Request):
    """Recibe eventos del Stream (start/media/stop/error) para depurar."""
    try:
        if req.headers.get("content-type", "").startswith("application/json"):
            data = await req.json()
        else:
            form = await req.form()
            data = dict(form)
    except Exception:
        data = {"raw": (await req.body()).decode(errors="ignore")}
    print("TWILIO STREAM EVENT:", data)
    return PlainTextResponse("OK")

@app.post("/voice/say")
def voice_say():
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response><Say language="es-ES">Hola, prueba correcta.</Say></Response>"""
    return Response(twiml, media_type="application/xml; charset=utf-8")

@app.post("/voice/answer")
def voice_answer():
    """TwiML de stream bidireccional con callback (sin 'track')."""
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://backend-spainroom.onrender.com{TWILIO_WS_PATH}"
            statusCallback="https://backend-spainroom.onrender.com/diag/stream-log"
            statusCallbackEvent="start media stop" />
  </Connect>
</Response>"""
    return Response(twiml, media_type="application/xml; charset=utf-8")

# ======= WebSocket: Twilio ⇄ OpenAI Realtime (μ-law passthrough) =======
@app.websocket(TWILIO_WS_PATH)
async def twilio_stream(ws_twilio: WebSocket):
    # Acepta el subprotocolo que Twilio envía: "audio"
    await ws_twilio.accept(subprotocol="audio")

    if not OPENAI_API_KEY:
        await ws_twilio.send_text(json.dumps({"event": "error", "message": "Falta OPENAI_API_KEY"}))
        await ws_twilio.close()
        return

    stream_sid: Optional[str] = None
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "OpenAI-Beta": "realtime=v1"}

    try:
        async with websockets.connect(OPENAI_REALTIME_URL, extra_headers=headers) as ws_ai:
            # Configura sesión: μ-law 8k en entrada y salida, VAD y reglas de idioma
            await ws_ai.send(json.dumps({
                "type": "session.update",
                "session": {
                    "voice": "verse",
                    "modalities": ["audio"],
                    "turn_detection": {"type": "server_vad", "create_response": True},
                    "input_audio_format": {"type": "g711_ulaw", "sample_rate_hz": 8000},
                    "output_audio_format": {"type": "g711_ulaw", "sample_rate_hz": 8000},
                    "instructions": (
                        "Eres 'SpainRoom'. Detecta si el usuario habla español o inglés y responde en ese idioma. "
                        "Cambia si cambia. Sé breve, natural, permite interrupciones (barge-in) y confirma datos sensibles."
                    )
                }
            }))

            # Espera a que Twilio envíe 'start' para tener streamSid antes de hablar
            started = False

            # --- Modelo -> Twilio (audio de salida μ-law 8k ya en base64) ---
            async def ai_to_twilio():
                try:
                    async for raw in ws_ai:
                        evt = json.loads(raw)
                        t = evt.get("type")
                        if t == "response.audio.delta":
                            # El modelo ya devuelve audio μ-law 8k (base64). Reenvíalo tal cual.
                            if stream_sid and started:
                                await ws_twilio.send_text(json.dumps({
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": evt["audio"]}
                                }))
                        elif t == "error":
                            # Muestra errores del Realtime (modelo/no acceso/formato…)
                            print("OPENAI REALTIME ERROR:", evt)
                except Exception as e:
                    print("ai_to_twilio error:", e)

            task = asyncio.create_task(ai_to_twilio())

            # --- Twilio -> Modelo (audio de entrada μ-law 8k base64) ---
            try:
                while True:
                    text = await ws_twilio.receive_text()
                    msg = json.loads(text)
                    ev = msg.get("event")

                    if ev == "start":
                        stream_sid = msg["start"]["streamSid"]
                        started = True
                        # Lanza saludo inicial AHORA (ya tenemos streamSid)
                        await ws_ai.send(json.dumps({
                            "type": "response.create",
                            "response": {
                                "modalities": ["audio"],
                                "instructions": "Hola. Puedo atender en español o en inglés. ¿En qué puedo ayudarte?"
                            }
                        }))

                    elif ev == "media":
                        # Reenvía payload μ-law 8k directamente al buffer del modelo (base64)
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
