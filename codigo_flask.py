# codigo_flask.py
import os, json, base64, asyncio, audioop, contextlib
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response, PlainTextResponse
import websockets

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
OPENAI_REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"
TWILIO_WS_PATH = "/stream/twilio"

app = FastAPI(title="SpainRoom Voice Gateway")

def resample_pcm16(pcm: bytes, src: int, dst: int) -> bytes:
    if src == dst or not pcm: return pcm
    out, _ = audioop.ratecv(pcm, 2, 1, src, dst, None)
    return out

@app.get("/voice/health")
def health():
    return {"ok": True, "service": "voice"}

@app.get("/diag/key")
def diag_key():
    return {"openai_key_configured": bool(OPENAI_API_KEY)}

@app.post("/diag/stream-log")
async def stream_log(req: Request):
    try:
        data = await req.json()
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
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://backend-spainroom.onrender.com{TWILIO_WS_PATH}"
            track="both_tracks"
            statusCallback="https://backend-spainroom.onrender.com/diag/stream-log"
            statusCallbackEvent="start mark media stop" />
  </Connect>
</Response>"""
    return Response(twiml, media_type="application/xml; charset=utf-8")

@app.websocket(TWILIO_WS_PATH)
async def twilio_stream(ws_twilio: WebSocket):
    await ws_twilio.accept()
    if not OPENAI_API_KEY:
        await ws_twilio.send_text(json.dumps({"event":"error","message":"Falta OPENAI_API_KEY"}))
        await ws_twilio.close()
        return

    stream_sid: Optional[str] = None
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "OpenAI-Beta": "realtime=v1"}

    try:
        async with websockets.connect(OPENAI_REALTIME_URL, extra_headers=headers) as ws_ai:
            # Configuración de sesión (audio 16k + VAD + idioma auto)
            await ws_ai.send(json.dumps({
                "type": "session.update",
                "session": {
                    "voice": "verse",
                    "modalities": ["audio"],
                    "turn_detection": {"type": "server_vad", "create_response": True},
                    "input_audio_format": {"type": "pcm16", "sample_rate_hz": 16000},
                    "output_audio_format": {"type": "pcm16", "sample_rate_hz": 16000},
                    "instructions": (
                        "Eres 'SpainRoom'. Detecta si el usuario habla español o inglés y responde "
                        "siempre en ese idioma. Cambia si cambia. Sé breve, natural, con barge-in, "
                        "y confirma nombre y teléfono antes de guardarlos."
                    )
                }
            }))
            # Saludo inicial (garantiza voz desde el segundo 1)
            await ws_ai.send(json.dumps({
                "type": "response.create",
                "response": {
                    "modalities": ["audio"],
                    "instructions": "Hola. Puedo atender en español o en inglés. ¿En qué puedo ayudarte?"
                }
            }))

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

            try:
                while True:
                    text = await ws_twilio.receive_text()
                    msg = json.loads(text)
                    ev = msg.get("event")
                    if ev == "start":
                        stream_sid = msg["start"]["streamSid"]
                    elif ev == "media":
                        ulaw_b64 = msg["media"]["payload"]
