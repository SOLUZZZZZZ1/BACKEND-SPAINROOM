# SpainRoom — Voice Backend (ConversationRelay) — ES STABLE
# Start: uvicorn codigo_flask:app --host 0.0.0.0 --port $PORT --proxy-headers

import os, json, re, time, contextlib, hashlib
from typing import Dict, Any
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import Response, JSONResponse, HTMLResponse
from xml.sax.saxutils import quoteattr

app = FastAPI(title="SpainRoom Voice — ConversationRelay (ES)")

def _twiml(xml: str) -> Response:
    return Response(content=xml, media_type="application/xml")

def _env(k: str, default: str = "") -> str:
    return os.getenv(k, default)

def _normalize_ws_host(request: Request) -> str:
    return request.headers.get("host") or request.url.hostname or "localhost"

async def _post_json(url: str, payload: dict, timeout: float = 2.0) -> None:
    import urllib.request
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            _ = r.read()
    except Exception:
        pass

def _digits(t: str) -> str:
    return "".join(ch for ch in (t or "") if ch.isdigit())

@app.get("/")
async def root(request: Request):
    host = _normalize_ws_host(request)
    ws_url = f"wss://{host}/cr"
    html = (
        "<h2>SpainRoom Voice — ConversationRelay (ES)</h2>\n"
        "<p>Voice URL: <code>/voice/answer_cr</code></p>\n"
        f"<p>WebSocket CR: <code>{ws_url}</code></p>\n"
        "<p>Health: <code>/health</code> · Diag: <code>/diag_runtime</code></p>"
    )
    return HTMLResponse(html)

@app.get("/health")
async def health():
    return JSONResponse({"ok": True})

@app.get("/diag_runtime")
async def diag_runtime():
    keys = [
        "CR_TTS_PROVIDER",
        "CR_LANGUAGE",
        "CR_TRANSCRIPTION_LANGUAGE",
        "CR_VOICE",
        "CR_WELCOME",
        "SPEAK_SLEEP_MS",
        "ASSIGN_URL",
    ]
    return JSONResponse({k: _env(k) for k in keys})

# === TwiML handlers (GET/POST) ==============================================

@app.api_route("/voice/answer_cr", methods=["GET", "POST"])
async def answer_cr(request: Request):
    host = _normalize_ws_host(request)
    ws_url = f"wss://{host}/cr"
    lang = _env("CR_LANGUAGE", "es-ES")
    trans_lang = _env("CR_TRANSCRIPTION_LANGUAGE", lang)
    tts_provider = _env("CR_TTS_PROVIDER", "Google")
    tts_voice = _env("CR_VOICE", "")
    welcome = _env("CR_WELCOME", "")  # RECOMENDADO: "Bienvenido a SpainRoom."
    attrs = [
        f"url={quoteattr(ws_url)}",
        f"language={quoteattr(lang)}",
        f"transcriptionLanguage={quoteattr(trans_lang)}",
        f"ttsProvider={quoteattr(tts_provider)}",
        'interruptible="speech"',
        'reportInputDuringAgentSpeech="none"',
    ]
    if welcome.strip():
        attrs.append(f"welcomeGreeting={quoteattr(welcome.strip())}")
    if tts_voice:
        attrs.append(f"voice={quoteattr(tts_voice)}")
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        "  <Connect>\n"
        f"    <ConversationRelay {' '.join(attrs)} />\n"
        "  </Connect>\n"
        "</Response>"
    )
    return _twiml(twiml)

@app.api_route("/voice/fallback", methods=["GET", "POST"])
async def voice_fallback(request: Request):
    # Fallback idéntico: ConversationRelay (no <Say>), así Twilio guarda sin error y siempre usa Google.
    return await answer_cr(request)

# === Conocimiento ============================================================

KNOWLEDGE: Dict[str, Dict[str, Any]] = {
    "que_hace": {
        "patterns": ["que hac", "qué hac", "quienes sois", "qué es spainroom", "que es spainroom"],
        "answers": [
            "SpainRoom alquila habitaciones de medio y largo plazo. No somos hotel.",
            "Intermediamos, validamos documentación y firmamos digitalmente para su seguridad.",
        ],
    },
    "minimo_precios": {
        "patterns": ["minimo", "mínimo", "precio", "precios", "tarifa"],
        "answers": [
            "La estancia mínima es de un mes. El precio depende de la habitación y la zona.",
            "Le ayudamos a comparar opciones disponibles en su ciudad.",
        ],
    },
    "documentos": {
        "patterns": ["document", "dni", "pasaporte", "requisitos"],
        "answers": [
            "Para inquilinos: DNI o pasaporte y comprobante del teléfono declarado.",
            "La firma es electrónica y guardamos justificantes para su tranquilidad.",
        ],
    },
    "proceso": {
        "patterns": ["proceso", "como func", "cómo func", "pasos"],
        "answers": [
            "El proceso es simple: solicitud → verificación → contrato digital → entrada.",
            "Le guiamos en cada paso y resolvemos dudas en el momento.",
        ],
    },
    "pagos": {
        "patterns": ["pago", "stripe
