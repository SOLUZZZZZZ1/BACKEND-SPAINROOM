# codigo_flask.py
import os
import json
import base64
import asyncio
import audioop
import contextlib
import importlib
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response, PlainTextResponse
from starlette.middleware.wsgi import WSGIMiddleware

# --- Flask tipos para detectar Blueprint/App (opcional) ---
from flask import Flask, Blueprint

import websockets

# ========= Config =========
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
OPENAI_REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"

# Twilio <Stream> WS path
TWILIO_WS_PATH = "/stream/twilio"

# Cargar (opcional) un Blueprint o una app Flask:
# Formato env: FLASK_BP="paquete.modulo:objeto"
#   - Si 'objeto' es un Flask Blueprint -> se registra.
#   - Si 'objeto' es una Flask app -> se monta tal cual.
FLASK_BP = os.getenv("FLASK_BP", "").strip()        # p.ej. "blueprints.hello:bp" o "legacy.app:app"
FLASK_MOUNT = os.getenv("FLASK_MOUNT", "/legacy")   # dónde colgar la app/blueprint en FastAPI

# ========= App FastAPI =========
app = FastAPI(title="SpainRoom Voice Gateway")

# ========= Util: resample PCM16 (stdlib audioop) =========
def pcm16_resample(pcm_bytes: bytes, src_rate: int, dst_rate: int) -> bytes:
    if src_rate == dst_rate:
        return pcm_bytes
    converted, _ = audioop.ratecv(pcm_bytes, 2, 1, src_rate, dst_rate, None)
    return converted

# ========= Rutas HTTP básicas =========
@app.get("/voice/health")
def health():
    return PlainTextResponse("OK")

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
def
