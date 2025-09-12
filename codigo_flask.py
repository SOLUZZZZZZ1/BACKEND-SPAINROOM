# codigo_flask.py — Passthrough G.711 μ-law 8k (sin resample, sin pausas)
import os
import json
import base64
import asyncio
import contextlib
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

app = FastAPI(title="SpainRoom Voice Gateway (μ-law passthrough, sin voz lenta)")

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

# (Opcional) prueba Twilio TTS sin streaming
@app.get("/voice/test_female")
@app.post("/voice/test_female")
def voice_test_female():
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="es-ES">Prueba del circuito. SpainRoom operativo.</Say>
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
                    "modalities": ["audio", "text"],  # requerido por Realtime
                    "turn_detection": {"type": "server_vad", "create_response": True},
                    "input_audio_format":  {"type": "g711_ulaw", "sample_rate_hz": 8000},
                    "output_audio_format": {"type": "g711_ulaw", "sample_rate_hz": 8000},
                    "instructions": (
                        "Te llamas Nora y trabajas en SpainRoom (alquiler de HABITACIONES). "
                        "Voz FEMENINA, cercana y ágil (ritmo natural, frases cortas). "
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
                                # Opción A: enviar tal cual (normalmente suficiente)
                                # await ws_twilio.send_text(json.dumps({
                                #     "event": "media", "streamSid": stream_sid,
                                #     "media": {"payload": ulaw_b64}
                                # }))

                                # Opción B: trocear en frames de 20 ms (160 bytes) para ritmo perfecto
                                ulaw_bytes = base64.b64decode(ulaw_b64)
                                CHUNK = 160  # 20 ms @ 8 kHz μ-law
                                for i in range(0, len(ulaw_bytes), CHUNK):
                                    payload = base64.b64encode(ulaw_bytes[i:i+CHUNK]).decode()
                                    await ws_twilio.send_text(json.dumps({
                                        "event": "media",
                                        "streamSid": stream_sid,
                                        "media": {"payload": payload}
                                    }))
                                    await asyncio.sleep(0)  # sin pausa => sin voz lenta

                        elif t == "error":
                            print("OPENAI REALTIME ERROR:", evt)
                        # else:  # traza opcional
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
                            "response": { "in
::contentReference[oaicite:0]{index=0}
