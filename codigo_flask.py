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
OPENAI_VOICE = os.getenv("OPENAI_VOICE","sage")
PUBLIC_WS_URL = os.getenv("PUBLIC_WS_URL","")
TWILIO_WS_PATH = os.getenv("TWILIO_WS_PATH","/ws/twilio")

SAYFIRST_TEXT = os.getenv("SAYFIRST_TEXT","Bienvenido a SpainRoom. Alquilamos habitaciones a medio y largo plazo (mínimo un mes). Para atenderle: ¿Es usted propietario o inquilino?")
FOLLOWUP_GREETING_TEXT = os.getenv("FOLLOWUP_GREETING_TEXT","Para atenderle: ¿Es usted propietario o inquilino?")
FOLLOWUP_GREETING_MS = int(os.getenv("FOLLOWUP_GREETING_MS","300") or "300")
CAPTURE_TAG = os.getenv("CAPTURE_TAG","LEAD")
LEAD_WEBHOOK_URL = os.getenv("LEAD_WEBHOOK_URL","")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL","")

# Audio / timing
CHUNK_MS = int(os.getenv("CHUNK_MS","20") or "20")
PACE_MS = int(os.getenv("PACE_MS","20") or "20")
ULAW_CHUNK_BYTES = int(os.getenv("ULAW_CHUNK_BYTES","160") or "160")
HWM_FRAMES = int(os.getenv("HWM_FRAMES","60") or "60")
BURST_M_
