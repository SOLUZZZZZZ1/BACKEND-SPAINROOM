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

# Ruta WS que Twilio abrirá desde el TwiML <Stream>
TWILIO_WS_PATH = "/stream/twilio"

# ========= App =========
app = FastAPI(title="SpainRoom Voice Gateway")

# ========= Util: resample PCM16 con stdlib (audioop) =========
def pcm16_resample(pcm_bytes: bytes, src_rate: int, dst_rate: int) -> bytes:
    if src_rate == dst_rate:
        return pcm_bytes
    converted, _ = audioop.ratecv(pcm_bytes, 2, 1, src_rate, dst_rate, None)
    return converted

# ========= Rutas HTTP =========
@app.get("/voice/health")
def health():
    return PlainTextResponse("OK")

@app.get("/diag/key")
def diag_key():
    """
    Devuelve si la variable OPENAI_API_KEY está cargada (sin mostrar su valor).
    """
    return {"openai_key_configured": bool(OPENAI_API_KEY)}

@app.post("/voice/say")
def voice_say():
    """
    TwiML de prueba rápida (sin streaming) para verificar que Twilio entra a tu backend.
    """
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say language="es-ES">Hola, SpainRoom. Prueba de backend correcta.</Say>
</Response>"""
    return Response(content=twiml, media_type="application/xml; charset=utf-8")

@app.post("/voice/answer")
def voice_answer():
    """
    Twilio (A Call Comes In -> POST) llama aquí.
    Respondemos con TwiML que abre el stream WS bidireccional a nuestro gateway.
    """
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://backend-spainroom.onrender.com{TWILIO_WS_PATH}" />
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml; charset=utf-8")

# ========= Gateway WS: Twilio <-> OpenAI Realtime =========
@app.websocket(TWILIO_WS_PATH)
async def twilio_stream(ws_twilio: WebSocket):
    """
    - Recibe audio µ-law 8k de Twilio, lo pasa a PCM16 16k y lo envía al modelo.
    - Recibe audio PCM16 16k del modelo, lo convierte a µ-law 8k y lo envía a Twilio.
    - El modelo detecta ES/EN y responde en el mismo idioma (barge-in con VAD).
    """
    await ws_twilio.accept()

    if not OPENAI_API_KEY:
        await ws_twilio.send_text(json.dumps({
            "event": "error",
            "message": "OPENAI_API_KEY no configurada en el servidor."
        }))
        await ws_twilio.close()
        return

    stream_sid: Optional[str] = None
    openai_headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "OpenAI-Beta": "realtime=v1",
    }

    try:
        async with websockets.connect(OPENAI_REALTIME_URL, extra_headers=openai_headers) as ws_ai:
            # Configurar sesión Realtime (voz + VAD + idioma)
            await ws_ai.send(json.dumps({
                "type": "session.update",
                "session": {
                    "voice": "verse",
                    "turn_detection": { "type": "server_vad", "create_response": True },
                    "instructions": (
                        "Eres 'SpainRoom'. Habla con voz natural. "
                        "Detecta automáticamente si el llamante habla español o inglés "
                        "y responde SIEMPRE en ese idioma. Si cambia de idioma, cambia tú también. "
                        "Sé breve, amable, permite interrupciones (barge-in) y confirma datos sensibles."
                    )
                }
            }))

            # Tarea: del modelo -> Twilio
            async def forward_ai_to_twilio():
                try:
                    async for raw in ws_ai:
                        evt = json.loads(raw)
                        if evt.get("type") == "response.audio.delta":
                            # Modelo -> PCM16 16k
                            pcm16_16k = base64.b64decode(evt["audio"])
                            # 16k -> 8k
                            pcm16_8k = pcm16_resample(pcm16_16k, 16000, 8000)
                            # PCM16 -> µ-law 8k
                            ulaw = audioop.lin2ulaw(pcm16_8k, 2)
                            payload = base64.b64encode(ulaw).decode()

                            if stream_sid:
                                await ws_twilio.send_text(json.dumps({
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": payload}
                                }))
                except Exception:
                    # Evitar caída del puente si hay cortes de audio
                    pass

            ai_task = asyncio.create_task(forward_ai_to_twilio())

            # Bucle: Twilio -> modelo
            try:
                while True:
                    msg_text = await ws_twilio.receive_text()
                    msg = json.loads(msg_text)
                    ev = msg.get("event")

                    if ev == "start":
                        stream_sid = msg["start"]["streamSid"]

                    elif ev == "media":
                        # Twilio -> base64(µ-law 8k)
                        ulaw_b64 = msg["media"]["payload"]
                        ulaw = base64.b64decode(ulaw_b64)
                        # µ-law 8k -> PCM16 8k
                        pcm16_8k = audioop.ulaw2lin(ulaw, 2)
                        # 8k -> 16k
                        pcm16_16k = pcm16_resample(pcm16_8k, 8000, 16000)
                        # Enviar chunk al buffer de entrada del modelo
                        await ws_ai.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(pcm16_16k).decode()
                        }))

                    elif ev == "stop":
                        break

            except WebSocketDisconnect:
                pass
            finally:
                ai_task.cancel()
                with contextlib.suppress(Exception):
                    await ai_task

    except Exception as e:
        with contextlib.suppress(Exception):
            await ws_twilio.send_text(json.dumps({"event": "error", "message": str(e)}))
        with contextlib.suppress(Exception):
            await ws_twilio.close()
