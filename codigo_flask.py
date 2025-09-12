
# codigo_flask.py — Passthrough G.711 μ-law 8k con troceo 20 ms (parche pacing)
# FastAPI + WebSocket bridge Twilio <-> OpenAI Realtime
import os
import json
import base64
import asyncio
import contextlib
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response
import websockets
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

# ========= Config =========
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
OPENAI_VOICE = os.getenv("OPENAI_VOICE", "sage")  # 'sage' / 'marin' / etc.
TWILIO_WS_PATH = os.getenv("TWILIO_WS_PATH", "/ws/twilio")
PUBLIC_WS_URL = os.getenv("PUBLIC_WS_URL")  # si lo defines, se usa tal cual (wss://.../ws/twilio)
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", "Eres Nora de SpainRoom. Responde de forma clara y breve.")

# ========= App =========
app = FastAPI(title="SpainRoom Voice Realtime Bridge")

# ========= Util =========
def _mask_key(key: str) -> str:
    if not key:
        return "(vacío)"
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]

def _infer_ws_url(request: Request) -> str:
    if PUBLIC_WS_URL:
        return PUBLIC_WS_URL
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    proto = request.headers.get("x-forwarded-proto", "https")
    scheme = "wss" if proto == "https" else "ws"
    return f"{scheme}://{host}{TWILIO_WS_PATH}"

# ========= Health =========
@app.get("/health")
def health():
    return {"ok": True}

@app.get("/diag_key")
def diag_key():
    return {"OPENAI_API_KEY": _mask_key(OPENAI_API_KEY)}

# ========= TwiML =========
@app.get("/voice/answer")
@app.post("/voice/answer")
def voice_answer(request: Request):
    ws_url = _infer_ws_url(request)
    # Importante: NO usar track="both_tracks" para evitar ruidos/latencia innecesaria
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}"/>
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

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "OpenAI-Beta": "realtime=v1",
    }

    ai_url = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"

    # Abrimos socket con OpenAI
    async with websockets.connect(ai_url, extra_headers=headers) as ws_ai:
        # Configurar sesión en μ-law 8k E2E + VAD servidor
        await ws_ai.send(json.dumps({
            "type": "session.update",
            "session": {
                "voice": OPENAI_VOICE,
                "instructions": SYSTEM_PROMPT,
                "input_audio_format": {"type": "g711_ulaw", "sample_rate_hz": 8000, "channels": 1},
                "output_audio_format": {"type": "g711_ulaw", "sample_rate_hz": 8000, "channels": 1},
                "turn_detection": {"type": "server_vad"},
            },
        }))

        async def ai_to_twilio():
            """Recibe audio del modelo y lo reenvía a Twilio en frames de 20 ms (160 bytes μ-law)."""
            try:
                async for raw in ws_ai:
                    evt = json.loads(raw)
                    t = evt.get("type")

                    if t in ("response.audio.delta", "response.output_audio.delta"):
                        ulaw_b64 = evt.get("audio") or evt.get("delta")
                        if ulaw_b64 and stream_sid and started:
                            data = base64.b64decode(ulaw_b64)
                            # Parche pacing: troceo a 160 bytes (20 ms @ 8 kHz μ-law)
                            for i in range(0, len(data), 160):
                                chunk = data[i:i+160]
                                if not chunk:
                                    continue
                                payload = base64.b64encode(chunk).decode("ascii")
                                await ws_twilio.send_text(json.dumps({
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": payload},
                                }))
                                # Ceder el control al loop sin imponer pausas artificiales
                                await asyncio.sleep(0)

                    elif t == "response.completed":
                        # Señal para marcar fin de respuesta (opcional)
                        pass

                    # (opcional) manejar otros eventos si se desea depurar
                    # else:
                    #     print("AI EVT:", evt)
            except (ConnectionClosedOK, ConnectionClosedError, asyncio.CancelledError):
                pass
            except Exception as e:
                print("ai_to_twilio error:", e)

        async def twilio_to_ai():
            """Recibe eventos de Twilio y empuja audio μ-law al buffer del modelo."""
            nonlocal stream_sid, started
            try:
                while True:
                    msg_text = await ws_twilio.receive_text()
                    msg = json.loads(msg_text)
                    ev = msg.get("event")

                    if ev == "start":
                        stream_sid = msg["start"]["streamSid"]
                        started = True
                        # Breve saludo inicial
                        await ws_ai.send(json.dumps({
                            "type": "response.create",
                            "response": {"instructions": "Hola, soy Nora de SpainRoom. ¿En qué puedo ayudarte?"}
                        }))

                    elif ev == "media":
                        if not started:
                            continue
                        payload = msg["media"]["payload"]
                        # Twilio media.payload ya es μ-law base64
                        await ws_ai.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": payload,
                        }))

                    elif ev == "stop":
                        # Cierra turno y cuelga
                        with contextlib.suppress(Exception):
                            await ws_ai.send(json.dumps({"type": "input_audio_buffer.commit"}))
                        break

                    # (opcional) marks/dtmf etc.
            except WebSocketDisconnect:
                pass
            except Exception as e:
                print("twilio_to_ai error:", e)

        # Ejecutar ambas direcciones en paralelo
        task_ai = asyncio.create_task(ai_to_twilio())
        task_twilio = asyncio.create_task(twilio_to_ai())
        done, pending = await asyncio.wait({task_ai, task_twilio}, return_when=asyncio.FIRST_COMPLETED)

        for t in pending:
            t.cancel()
            with contextlib.suppress(Exception):
                await t

        with contextlib.suppress(Exception):
            await ws_ai.close()

    # Cerrar socket Twilio si sigue abierto
    with contextlib.suppress(Exception):
        await ws_twilio.close()

# Dev local opcional
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("codigo_flask:app", host="0.0.0.0", port=8000)
