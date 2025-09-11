# codigo_flask.py
import os, json, asyncio, contextlib
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response, PlainTextResponse
import websockets

# ======= Config =======
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
OPENAI_REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"
TWILIO_WS_PATH = "/stream/twilio"

# ======= App =======
app = FastAPI(title="SpainRoom Voice Gateway")

# ======= Rutas HTTP =======
@app.get("/voice/health")
def health():
    return {"ok": True, "service": "voice"}

@app.get("/diag/key")
def diag_key():
    return {"openai_key_configured": bool(OPENAI_API_KEY)}

@app.post("/diag/stream-log")
async def stream_log(req: Request):
    """Recibe eventos del Stream (start/stop/error) para depurar."""
    try:
        if req.headers.get("content-type", "").startswith("application/json"):
            data = await req.json()
        else:
            form = await req.form()
