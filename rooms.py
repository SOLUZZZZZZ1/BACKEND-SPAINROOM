# backend/rooms.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from datetime import datetime
import os, json

router = APIRouter()
DATA_DIR = os.getenv("DATA_DIR", "data")
ROOMS_DIR = os.path.join(DATA_DIR, "rooms")
os.makedirs(ROOMS_DIR, exist_ok=True)

class RoomSheet(BaseModel):
  room_id: str | None = None
  title: str
  address: str | None = None
  city: str
  size_m2: float | None = None
  price_eur: float | None = None
  owner_email: str | None = None
  features: list[str] = Field(default_factory=list)

@router.post("/rooms/sheet")
def save_room_sheet(sheet: RoomSheet):
  rid = sheet.room_id or f"ROOM-{int(datetime.utcnow().timestamp())}"
  path = os.path.join(ROOMS_DIR, f"{rid}.json")
  data = sheet.model_dump()
  data["room_id"] = rid
  with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
  return {"message": "Ficha de habitación guardada", "room_id": rid}
