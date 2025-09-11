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
OPENAI_VOICE = os.getenv("OPENAI_VOICE", "shimmer")  # voces soportadas: alloy, ash, ballad, coral, echo, sage, shimmer, verse, marin, cedar

TWILIO_WS_PATH = "/stream/twilio"

# ========= App =========
app = FastAPI(title="SpainRoom Voice Gateway (VOZ LITE CHUNKED)")

# ========= Util: PCM16 resample =========
def resample_pcm16(pcm: bytes, src_hz: int, dst_hz: int) -> bytes:
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

@app.post("/voice/answer")
def voice_answer():
    # Sin track="both_tracks" (con <Connect> da 31941). Simplísimo.
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
    # Twilio usa Sec-WebSocket-Protocol: audio -> acéptalo
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
            # 1)
