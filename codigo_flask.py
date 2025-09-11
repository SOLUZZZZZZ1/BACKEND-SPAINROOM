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

# ========= HTTP =========
@app.get("/voice/health")
def health():
    return {"ok": True, "service": "voice"}

@app.get("/diag/key")
def diag_key():
    return {"openai_key_configured": bool(OPENAI_API_KEY)}

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

# ========= WS: Twilio ⇄ OpenAI Realtime =========
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
            # 1) Sesión del modelo: PCM16 16k limpio + voz femenina + VAD servidor
            await ws_ai.send(json.dumps({
                "type": "session.update",
                "session": {
                    "voice": OPENAI_VOICE,
                    "modalities": ["audio", "text"],  # requerido por Realtime
                    "turn_detection": {"type": "server_vad", "create_response": True},
                    "input_audio_format":  {"type": "pcm16", "sample_rate_hz": 16000},
                    "output_audio_format": {"type": "pcm16", "sample_rate_hz": 16000},
                    "instructions": (
                        "Te llamas Nora de SpainRoom (alquiler de habitaciones). Voz femenina, cercana y ágil. "
                        "Nada de viajes. Mantén ritmo natural, frases cortas."
                    )
                }
            }))

            # 2) Hilo: Modelo → Twilio (PCM16 16k → μ-law 8k en chunks de 20 ms)
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
                                    payload = base64.b
