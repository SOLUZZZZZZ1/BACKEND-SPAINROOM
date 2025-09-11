# codigo_flask.py
import os, json, base64, asyncio, audioop, contextlib
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

# ======= Util =======
def resample_pcm16(pcm: bytes, src_hz: int, dst_hz: int) -> bytes:
    if src_hz == dst_hz or not pcm:
        return pcm
    out, _ = audioop.ratecv(pcm, 2, 1, src_hz, dst_hz, None)
    return out

# ======= Rutas HTTP =======
@app.get("/voice/health")
def health():
    return {"ok": True, "service": "voice"}

@app.get("/diag/key")
def diag_key():
    return {"openai_key_configured": bool(OPENAI_API_KEY)}

@app.post("/diag/stream-log")
async def stream_log(req: Request):
    """Recibe eventos del Stream (start/stop/error)."""
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
    """TwiML de stream bidireccional (¡sin track!) con callback para logs."""
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://backend-spainroom.onrender.com{TWILIO_WS_PATH}"
            statusCallback="https://backend-spainroom.onrender.com/diag/stream-log" />
  </Connect>
</Response>"""
    return Response(twiml, media_type="application/xml; charset=utf-8")

# ======= WebSocket: Twilio ⇄ OpenAI Realtime =======
@app.websocket(TWILIO_WS_PATH)
async def twilio_stream(ws_twilio: WebSocket):
    await ws_twilio.accept()

    if not OPENAI_API_KEY:
        await ws_twilio.send_text(json.dumps({"event": "error", "message": "Falta OPENAI_API_KEY"}))
        await ws_twilio.close()
        return

    stream_sid: Optional[str] = None
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "OpenAI-Beta": "realtime=v1"}

    try:
        async with websockets.connect(OPENAI_REALTIME_URL, extra_headers=headers) as ws_ai:
            # Sesión: audio 16k + VAD + idioma auto
            await ws_ai.send(json.dumps({
                "type": "session.update",
                "session": {
                    "voice": "verse",
                    "modalities": ["audio"],
                    "turn_detection": {"type": "server_vad", "create_response": True},
                    "input_audio_format": {"type": "pcm16", "sample_rate_hz": 16000},
                    "output_audio_format": {"type": "pcm16", "sample_rate_hz": 16000},
                    "instructions": (
                        "Eres 'SpainRoom'. Detecta si el usuario habla español o inglés y responde en ese idioma. "
                        "Cambia si cambia. Sé breve, natural, permite interrupciones (barge-in) y confirma datos sensibles."
                    )
                }
            }))

            # Saludo inicial (garantiza voz de salida)
            await ws_ai.send(json.dumps({
                "type": "response.create",
                "response": {
                    "modalities": ["audio"],
                    "instructions": "Hola. Puedo atender en español o en inglés. ¿En qué puedo ayudarte?"
                }
            }))

            # --- Modelo -> Twilio (audio de salida) ---
            async def ai_to_twilio():
                try:
                    async for raw in ws_ai:
                        evt = json.loads(raw)
                        if evt.get("type") == "response.audio.delta":
                            pcm16_16k = base64.b64decode(evt["audio"])
                            pcm16_8k = resample_pcm16(pcm16_16k, 16000, 8000)
                            ulaw = audioop.lin2ulaw(pcm16_8k, 2)
                            payload = base64.b64encode(ulaw).decode()
                            if stream_sid:
                                await ws_twilio.send_text(json.dumps({
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": payload}
                                }))
                except Exception:
                    pass

            task = asyncio.create_task(ai_to_twilio())

            # --- Twilio -> Modelo (audio de entrada) ---
            try:
                while True:
                    text = await ws_twilio.receive_text()
                    msg = json.loads(text)
                    ev = msg.get("event")

                    if ev == "start":
                        stream_sid = msg["start"]["streamSid"]

                    elif ev == "media":
                        ulaw_b64 = msg["media"]["payload"]
                        ulaw = base64.b64decode(ulaw_b64)
                        pcm16_8k = audioop.ulaw2lin(ulaw, 2)
                        pcm16_16k = resample_pcm16(pcm16_8k, 8000, 16000)
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
        with contextlib.suppress(Exception):
            await ws_twilio.send_text(json.dumps({"event": "error", "message": str(e)}))
        with contextlib.suppress(Exception):
            await ws_twilio.close()
