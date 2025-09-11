# codigo_flask.py
import os
import json
import asyncio
import contextlib
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response, PlainTextResponse
import websockets

# ========= Config =========
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
OPENAI_REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"

# Twilio <Connect><Stream> (bidireccional) apuntará aquí:
TWILIO_WS_PATH = "/stream/twilio"

# ========= App =========
app = FastAPI(title="SpainRoom Voice Gateway")

# ========= Rutas HTTP =========
@app.get("/voice/health")
def health():
    return {"ok": True, "service": "voice"}

@app.get("/diag/key")
def diag_key():
    return {"openai_key_configured": bool(OPENAI_API_KEY)}

@app.post("/diag/stream-log")
async def stream_log(req: Request):
    # Log simple y robusto (no parseamos sofisticado para evitar fallos)
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
    Twilio (A Call Comes In -> POST) recibe este TwiML.
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

# ========= WebSocket: Twilio ⇄ OpenAI Realtime (μ-law 8k passthrough) =========
@app.websocket(TWILIO_WS_PATH)
async def twilio_stream(ws_twilio: WebSocket):
    # Twilio envía Sec-WebSocket-Protocol: audio -> acéptalo
    await ws_twilio.accept(subprotocol="audio")

    if not OPENAI_API_KEY:
        await ws_twilio.send_text(json.dumps({"event": "error", "message": "Falta OPENAI_API_KEY"}))
        await ws_twilio.close()
        return

    stream_sid: Optional[str] = None
    started = False

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "OpenAI-Beta": "realtime=v1",
    }

    # Helper para extraer base64 de eventos de audio del modelo (según versión)
    def _extract_ulaw_b64(evt: dict) -> Optional[str]:
        # Intentos habituales
        for k in ("audio", "delta", "chunk", "data"):
            v = evt.get(k)
            if isinstance(v, str) and v:
                return v
        # Algunos eventos vienen anidados (por si acaso)
        out = evt.get("output_audio") or {}
        if isinstance(out, dict):
            for k in ("audio", "delta"):
                v = out.get(k)
                if isinstance(v, str) and v:
                    return v
        return None

    try:
        async with websockets.connect(OPENAI_REALTIME_URL, extra_headers=headers) as ws_ai:
            # 1) Configurar sesión: μ-law 8k, VAD y MODALIDADES REQUERIDAS ['audio','text']
            await ws_ai.send(json.dumps({
                "type": "session.update",
                "session": {
                    "voice": "verse",
                    "modalities": ["audio", "text"],  # <- importante (evita 'Invalid modalities: ["audio"]')
                    "turn_detection": {"type": "server_vad", "create_response": True},
                    "input_audio_format": {"type": "g711_ulaw", "sample_rate_hz": 8000},
                    "output_audio_format": {"type": "g711_ulaw", "sample_rate_hz": 8000},
                    "instructions": (
                        "Eres 'SpainRoom'. Detecta si el usuario habla español o inglés y responde en ese idioma. "
                        "Cambia si cambia. Sé breve, natural, permite interrupciones (barge-in) y confirma datos sensibles."
                    )
                }
            }))

            # 2) Tarea: del modelo -> Twilio (reenviamos μ-law base64 tal cual)
            async def ai_to_twilio():
                try:
                    async for raw in ws_ai:
                        evt = json.loads(raw)
                        t = evt.get("type")
                        if t in (
                            "response.audio.delta",
                            "response.output_audio.delta",
                            "response.output_audio.buffer.delta",
                        ):
                            b64 = _extract_ulaw_b64(evt)
                            if b64 and stream_sid and started:
                                await ws_twilio.send_text(json.dumps({
