# backend/otp.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
import os, json, re, random

router = APIRouter()
DATA_DIR = os.getenv("DATA_DIR", "data")
OTP_PATH = os.path.join(DATA_DIR, "otp.json")
os.makedirs(DATA_DIR, exist_ok=True)
if not os.path.exists(OTP_PATH):
  with open(OTP_PATH, "w", encoding="utf-8") as f:
    json.dump({}, f)

def normalize_phone(phone: str) -> str:
  return re.sub(r"\D", "", phone or "")

class OTPRequest(BaseModel):
  phone: str

class OTPVerify(BaseModel):
  phone: str
  code: str

@router.post("/otp/request")
def otp_request(req: OTPRequest):
  phone = normalize_phone(req.phone)
  if not phone:
    raise HTTPException(400, "Teléfono no válido")
  code = f"{random.randint(0, 999999):06d}"  # 6 dígitos
  with open(OTP_PATH, "r", encoding="utf-8") as f:
    db = json.load(f)
  db[phone] = {"code": code, "exp": (datetime.utcnow() + timedelta(minutes=10)).isoformat()}
  with open(OTP_PATH, "w", encoding="utf-8") as f:
    json.dump(db, f)
  # Aquí enviarías el SMS con Twilio u otro proveedor.
  return {"message": "OTP generado (demo)", "code_demo": code}

@router.post("/otp/verify")
def otp_verify(req: OTPVerify):
  phone = normalize_phone(req.phone)
  with open(OTP_PATH, "r", encoding="utf-8") as f:
    db = json.load(f)
  item = db.get(phone)
  if not item:
    raise HTTPException(400, "No hay OTP para este teléfono")
  if req.code != item.get("code"):
    raise HTTPException(400, "Código incorrecto")
  # (Opcional: validar expiración)
  return {"ok": True}
