
# codigo_flask.py — SpainRoom Voice (Twilio 8 kHz ↔ OpenAI 24 kHz) con de-click
# (ver descripción dentro)
import os, json, base64, asyncio, contextlib, time, math, urllib.request
from typing import Optional, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response, JSONResponse, HTMLResponse
import websockets
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError
try:
    import audioop
except ModuleNotFoundError:
    import audioop_lts as audioop

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
OPENAI_VOICE = os.getenv("OPENAI_VOICE", "sage")
TWILIO_WS_PATH = os.getenv("TWILIO_WS_PATH", "/ws/twilio")
PUBLIC_WS_URL = os.getenv("PUBLIC_WS_URL")
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT",
    "Eres Nora de SpainRoom (es-ES, usted). Captura rol, población, zona, nombre y teléfono. "
    "Emite <<LEAD>>{\"role\":\"propietario|inquilino\",\"poblacion\":\"POBLACION\",\"zona\":\"ZONA\","
    "\"nombre\":\"NOMBRE COMPLETO\",\"telefono\":\"TELEFONO\"}<<END>> al completar y confirma en una frase.")

CHUNK_MS = int(os.getenv("CHUNK_MS", "20"))
ULAW_CHUNK_BYTES = int(os.getenv("ULAW_CHUNK_BYTES", "160"))
PACE_MS = int(os.getenv("PACE_MS", str(CHUNK_MS)))
HWM_FRAMES = int(os.getenv("HWM_FRAMES", "50"))
BURST_MAX = int(os.getenv("BURST_MAX", "6"))
PREROLL_MS = int(os.getenv("PREROLL_MS", "1000"))
SAYFIRST_TEXT = os.getenv("SAYFIRST_TEXT", "Bienvenido a SpainRoom.")
FOLLOWUP_GREETING_MS = int(os.getenv("FOLLOWUP_GREETING_MS", "300"))
FOLLOWUP_GREETING_TEXT = os.getenv("FOLLOWUP_GREETING_TEXT", "Para atenderle: ¿Es usted propietario o inquilino?")
DEBUG = os.getenv("DEBUG", "0") == "1"

PAUSE_EVERY_MS = int(os.getenv("PAUSE_EVERY_MS", "0"))
MAX_UTTER_MS = int(os.getenv("MAX_UTTER_MS", "3400"))
BARGE_VAD_DB = float(os.getenv("BARGE_VAD_DB", "-28"))
BARGE_RELEASE_MS = int(os.getenv("BARGE_RELEASE_MS", "950"))
BARGE_SEND_SILENCE = os.getenv("BARGE_SEND_SILENCE", "1") == "1"
START_SPEAK_DELAY_MS = int(os.getenv("START_SPEAK_DELAY_MS", "350"))
MIN_BARGE_SPEECH_MS = int(os.getenv("MIN_BARGE_SPEECH_MS", "220"))

DECLICK_ON = os.getenv("DECLICK_ON", "1") == "1"
DECLICK_FRAMES = int(os.getenv("DECLICK_FRAMES", "2"))
DECLICK_FACTORS = [0.6, 0.3]

CAPTURE_TAG = os.getenv("CAPTURE_TAG", "LEAD")
LEAD_WEBHOOK_URL = os.getenv("LEAD_WEBHOOK_URL", "")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

app = FastAPI(title="SpainRoom Voice Realtime", docs_url="/docs", redoc_url=None)

def _log(*a):
    if DEBUG: print("[SRV]", *a)

def _mask(key: str) -> str:
    if not key: return ""
    if len(key) <= 8: return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]

def _infer_ws_url(request: Request) -> str:
    if PUBLIC_WS_URL: return PUBLIC_WS_URL
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    proto = request.headers.get("x-forwarded-proto", "https")
    scheme = "wss" if proto == "https" else "ws"
    return f"{scheme}://{host}{TWILIO_WS_PATH}"

class RateCV:
    def __init__(self, src_rate: int, dst_rate: int, sampwidth: int = 2, channels: int = 1):
        self.src_rate = src_rate; self.dst_rate = dst_rate
        self.sampwidth = sampwidth; self.channels = channels
        self.state = None
    def convert(self, pcm: bytes) -> bytes:
        if not pcm: return b""
        out, self.state = audioop.ratecv(pcm, self.sampwidth, self.channels, self.src_rate, self.dst_rate, self.state)
        return out

async def _post_json(url: str, payload: dict):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    return await asyncio.to_thread(lambda: urllib.request.urlopen(req, timeout=6).read())

async def deliver_lead(lead: dict):
    if not lead: return
    if LEAD_WEBHOOK_URL:
        with contextlib.suppress(Exception):
            await _post_json(LEAD_WEBHOOK_URL, lead); _log("Lead → webhook")
    if SLACK_WEBHOOK_URL:
        with contextlib.suppress(Exception):
            text = f":house: *LEAD* — {lead.get('role','?')} | {lead.get('poblacion',lead.get('zona','?'))} | {lead.get('zona','?')} | {lead.get('nombre','?')} | {lead.get('telefono','?')}"
            await _post_json(SLACK_WEBHOOK_URL, {"text": text}); _log("Lead → Slack")

@app.get("/")
async def root(request: Request):
    ws_url = _infer_ws_url(request)
    return HTMLResponse(f"<h3>SpainRoom Voice</h3><p>/health · /docs · /voice/answer_sayfirst (GET/POST)</p><code>{ws_url}</code>")

@app.get("/health")
async def health():
    return JSONResponse({"ok": True})

@app.get("/diag_keys")
async def diag_keys():
    return JSONResponse({
        "OPENAI_API_KEY": _mask(OPENAI_API_KEY),
        "LEAD_WEBHOOK_URL": bool(LEAD_WEBHOOK_URL),
        "SLACK_WEBHOOK_URL": bool(SLACK_WEBHOOK_URL),
    })

@app.api_route("/voice/answer_sayfirst", methods=["GET","POST"])
async def voice_answer(request: Request):
    call_sid = ""; from_phone = ""
    with contextlib.suppress(Exception):
        if request.method == "POST":
            form = dict(await request.form()); call_sid = form.get("CallSid","") or form.get("callsid",""); from_phone = form.get("From","") or form.get("from","")
        else:
            qp = dict(request.query_params); call_sid = qp.get("CallSid","") or qp.get("callsid",""); from_phone = qp.get("From","") or qp.get("from","")
    ws_url = _infer_ws_url(request)
    params_xml = ""
    if call_sid or from_phone:
        params_xml = f'\n      <Parameter name="callSid" value="{call_sid}"/>\n      <Parameter name="from" value="{from_phone}"/>'
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="es-ES">{SAYFIRST_TEXT}</Say>
  <Connect>
    <Stream url="{ws_url}">{params_xml}
    </Stream>
  </Connect>
</Response>"""
    return Response(twiml, media_type="text/xml; charset=utf-8")

@app.api_route("/voice/fallback", methods=["GET","POST"])
async def voice_fallback():
    return Response('<?xml version="1.0"?><Response><Say language="es-ES">Lo siento, intente de nuevo.</Say></Response>',
                    media_type="text/xml; charset=utf-8")

@app.websocket(TWILIO_WS_PATH)
async def twilio_stream(ws_twilio: WebSocket):
    await ws_twilio.accept(subprotocol="audio"); _log("WS accepted (audio)")
    if not OPENAI_API_KEY:
        await ws_twilio.send_text(json.dumps({"event":"error","message":"Falta OPENAI_API_KEY"})); await ws_twilio.close(); return

    stream_sid: Optional[str] = None; started = False
    start_evt = asyncio.Event(); ws_ai: Optional[websockets.WebSocketClientProtocol] = None
    buffered_ulaw: List[bytes] = []
    call_sid = ""; from_phone = ""

    barge_active = False; barge_last_voice = 0.0; ai_spoken_ms = 0; speak_gate_until = 0.0; vad_hot_ms = 0
    up_8k_to_24k = RateCV(8000, 24000, 2, 1); down_24k_to_8k = RateCV(24000, 8000, 2, 1)

    ulaw_out_queue: asyncio.Queue = asyncio.Queue(maxsize=4000)
    ulaw_carry = bytearray()
    last_ulaw_sent: Optional[bytes] = None  # de-click

    def _declick_frames_from_last() -> List[str]:
        out: List[str] = []
        if not last_ulaw_sent or not DECLICK_ON: return out
        try:
            lin = audioop.ulaw2lin(last_ulaw_sent, 2)
            nframes = 2 if DECLICK_FRAMES >= 2 else 1
            for f in DECLICK_FACTORS[:nframes]:
                lin_f = audioop.mul(lin, 2, float(f))
                u = audioop.lin2ulaw(lin_f, 2)
                out.append(base64.b64encode(u).decode('ascii'))
        except Exception as e:
            _log("de-click error", e)
        return out

    async def twilio_sender():
        nonlocal stream_sid, barge_active, ai_spoken_ms, speak_gate_until, last_ulaw_sent
        next_t = time.monotonic(); sleep_s = max(0.0, PACE_MS / 1000.0); played_since_pause_ms = 0
        while True:
            now = time.monotonic()
            if speak_gate_until and now < speak_gate_until:
                if BARGE_SEND_SILENCE and stream_sid:
                    silent = base64.b64encode(bytes([0xFF]) * ULAW_CHUNK_BYTES).decode('ascii')
                    await ws_twilio.send_text(json.dumps({"event":"media","streamSid":stream_sid,"media":{"payload":silent}}))
                if sleep_s > 0.0: await asyncio.sleep(sleep_s); continue

            if barge_active:
                for b64 in _declick_frames_from_last():
                    await ws_twilio.send_text(json.dumps({"event":"media","streamSid":stream_sid,"media":{"payload":b64}}))
                if BARGE_SEND_SILENCE and stream_sid:
                    silent = base64.b64encode(bytes([0xFF]) * ULAW_CHUNK_BYTES).decode('ascii')
                    await ws_twilio.send_text(json.dumps({"event":"media","streamSid":stream_sid,"media":{"payload":silent}}))
                try:
                    while True: ulaw_out_queue.get_nowait(); ulaw_out_queue.task_done()
                except asyncio.QueueEmpty: pass
                if sleep_s > 0.0: await asyncio.sleep(sleep_s)
                continue

            payload_b64 = await ulaw_out_queue.get()
            try:
                raw = base64.b64decode(payload_b64)
                if raw != bytes([0xFF]) * ULAW_CHUNK_BYTES: last_ulaw_sent = raw
            except Exception: pass

            await ws_twilio.send_text(json.dumps({"event":"media","streamSid":stream_sid,"media":{"payload":payload_b64}}))
            ulaw_out_queue.task_done()

            ai_spoken_ms += CHUNK_MS; played_since_pause_ms += CHUNK_MS

            if PAUSE_EVERY_MS > 0 and played_since_pause_ms >= PAUSE_EVERY_MS:
                if stream_sid:
                    silent = base64.b64encode(bytes([0xFF]) * ULAW_CHUNK_BYTES).decode('ascii')
                    await ws_twilio.send_text(json.dumps({"event":"media","streamSid":stream_sid,"media":{"payload":silent}}))
                played_since_pause_ms = 0; next_t = time.monotonic(); continue

            if MAX_UTTER_MS > 0 and ai_spoken_ms >= MAX_UTTER_MS:
                barge_active = True; barge_last_voice = time.monotonic()

            qsz = ulaw_out_queue.qsize()
            if qsz >= HWM_FRAMES:
                to_send = min(qsz - HWM_FRAMES, BURST_MAX)
                for _ in range(to_send):
                    payload_b64 = await ulaw_out_queue.get()
                    await ws_twilio.send_text(json.dumps({"event":"media","streamSid":stream_sid,"media":{"payload":payload_b64}}))
                    ulaw_out_queue.task_done()
                next_t = time.monotonic(); continue

            if sleep_s > 0.0:
                next_t += sleep_s; delay = next_t - time.monotonic()
                if delay > 0: await asyncio.sleep(delay)
                else: next_t = time.monotonic()

    async def enqueue_ulaw_frames(ulaw_bytes: bytes):
        nonlocal ulaw_carry
        ulaw_carry.extend(ulaw_bytes)
        while len(ulaw_carry) >= ULAW_CHUNK_BYTES:
            frame = bytes(ulaw_carry[:ULAW_CHUNK_BYTES])
            await ulaw_out_queue.put(base64.b64encode(frame).decode('ascii'))
            del ulaw_carry[:ULAW_CHUNK_BYTES]

    async def enqueue_silence(ms: int):
        nonlocal ai_spoken_ms
        ai_spoken_ms = 0
        frames = max(1, ms // CHUNK_MS)
        b64 = base64.b64encode(bytes([0xFF]) * ULAW_CHUNK_BYTES).decode("ascii")
        for _ in range(frames): await ulaw_out_queue.put(b64)

    async def connect_ai_after_start():
        nonlocal ws_ai, ai_spoken_ms, barge_active, speak_gate_until
        await start_evt.wait()
        try:
            headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "OpenAI-Beta": "realtime=v1"}
            ai_url = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"
            _log("Connecting to OpenAI Realtime…")
            ws_ai = await websockets.connect(ai_url, extra_headers=headers)
            _log("OpenAI Realtime connected")

            await ws_ai.send(json.dumps({
                "type": "session.update",
                "session": {
                    "voice": OPENAI_VOICE,
                    "instructions": SYSTEM_PROMPT,
                    "input_audio_format": {"type": "pcm16", "sample_rate_hz": 24000, "channels": 1},
                    "output_audio_format": {"type": "pcm16", "sample_rate_hz": 24000, "channels": 1},
                    "turn_detection": {"type": "server_vad"},
                    "modalities": ["audio","text"],
                },
            }))
            ai_spoken_ms = 0; barge_active = False

            await asyncio.sleep(FOLLOWUP_GREETING_MS / 1000.0)
            with contextlib.suppress(Exception):
                await ws_ai.send(json.dumps({"type":"response.create","response":{"instructions":FOLLOWUP_GREETING_TEXT,"voice":OPENAI_VOICE}}))

            try:
                txt_buf = ""
                async for raw in ws_ai:
                    evt = json.loads(raw); t = evt.get("type")

                    if t in ("response.created","response.started"):
                        ai_spoken_ms = 0; barge_active = False
                        speak_gate_until = time.monotonic() + (START_SPEAK_DELAY_MS / 1000.0)

                    if t in ("response.output_text.delta",):
                        delta = evt.get("delta") or evt.get("text") or ""
                        if delta:
                            txt_buf += delta
                            st, en = f"<<{CAPTURE_TAG}>>", "<<END>>"
                            if st in txt_buf and en in txt_buf:
                                s = txt_buf.rfind(st); e = txt_buf.find(en, s)
                                if e != -1:
                                    payload = txt_buf[s+len(st):e].strip()
                                    lead = {}
                                    with contextlib.suppress(Exception): lead = json.loads(payload)
                                    lead.update({"timestamp": int(time.time()*1000), "source": "twilio-voice"})
                                    await deliver_lead(lead)

                    if t in ("response.audio.delta","response.output_audio.delta"):
                        if barge_active: continue
                        b64 = evt.get("audio") or evt.get("delta")
                        if b64 and started and stream_sid:
                            pcm24 = base64.b64decode(b64)
                            lin8 = down_24k_to_8k.convert(pcm24)
                            if not lin8: continue
                            ulaw8 = audioop.lin2ulaw(lin8, 2)
                            await enqueue_ulaw_frames(ulaw8)

            except (ConnectionClosedOK, ConnectionClosedError, asyncio.CancelledError):
                _log("AI socket closed.")
            except Exception as e:
                _log("ai_to_twilio error:", e)
        finally:
            with contextlib.suppress(Exception):
                if ws_ai is not None: await ws_ai.close()

    async def twilio_to_ai():
        nonlocal stream_sid, started, barge_active, barge_last_voice, vad_hot_ms, call_sid, from_phone
        try:
            while True:
                msg_text = await ws_twilio.receive_text()
                msg = json.loads(msg_text); ev = msg.get("event")

                if ev == "start":
                    stream_sid = msg["start"]["streamSid"]; started = True
                    with contextlib.suppress(Exception):
                        params = {p.get("name"): p.get("value") for p in msg["start"].get("customParameters", [])}
                        call_sid = params.get("callSid",""); from_phone = params.get("from","")
                    _log("Twilio start — streamSid:", stream_sid, "from:", from_phone)
                    start_evt.set()
                    await enqueue_silence(PREROLL_MS)

                elif ev == "media":
                    if not started: continue
                    ulaw = base64.b64decode(msg["media"]["payload"])
                    lin = audioop.ulaw2lin(ulaw, 2)
                    rms = max(1, audioop.rms(lin, 2)); db = 20.0 * math.log10(rms / 32767.0); now = time.monotonic()
                    if db >= BARGE_VAD_DB:
                        vad_hot_ms = min(vad_hot_ms + CHUNK_MS, 2000)
                        if vad_hot_ms >= MIN_BARGE_SPEECH_MS:
                            barge_active = True; barge_last_voice = now; vad_hot_ms = 0
                            with contextlib.suppress(Exception):
                                # cancelar respuesta en curso
                                await ws_ai.send(json.dumps({"type":"response.cancel"}))
                    else:
                        vad_hot_ms = 0
                        if barge_active and (now - barge_last_voice) * 1000.0 >= BARGE_RELEASE_MS:
                            barge_active = False

                    if ws_ai is not None:
                        pcm24 = up_8k_to_24k.convert(lin)
                        if pcm24:
                            await ws_ai.send(json.dumps({
                                "type":"input_audio_buffer.append",
                                "audio": base64.b64encode(pcm24).decode("ascii"),
                            }))
                    else:
                        buffered_ulaw.append(ulaw)

                elif ev == "stop":
                    _log("Twilio stop")
                    with contextlib.suppress(Exception):
                        if ws_ai is not None:
                            await ws_ai.send(json.dumps({"type":"input_audio_buffer.commit"}))
                    break

        except WebSocketDisconnect:
            _log("Twilio WS disconnect")
        except Exception as e:
            _log("twilio_to_ai error:", e)

    # Lanzar tareas
    task_connect = asyncio.create_task(connect_ai_after_start())
    task_twilio  = asyncio.create_task(twilio_to_ai())
    task_sender  = asyncio.create_task(twilio_sender())
    done, pending = await asyncio.wait({task_connect, task_twilio, task_sender}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
        with contextlib.suppress(Exception):
            await t

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("codigo_flask:app", host="0.0.0.0", port=8000)
