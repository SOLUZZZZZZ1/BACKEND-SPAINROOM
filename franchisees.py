# backend/franchisees.py
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from datetime import datetime
import os, shutil

router = APIRouter()
BASE_DIR = os.getenv("UPLOAD_DIR", "uploads")
FR_DIR = os.path.join(BASE_DIR, "franchisees")
os.makedirs(FR_DIR, exist_ok=True)

@router.post("/franchisees/upload-documents")
async def upload_franchisee_documents(
    franchisee_email: str = Form(...),
    room_id: str = Form(...),
    sheet_file: UploadFile | None = File(None),
    photos: list[UploadFile] | None = File(None),
):
    try:
        stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        fr_dir = os.path.join(FR_DIR, franchisee_email.replace("@","_"), room_id)
        os.makedirs(fr_dir, exist_ok=True)

        def save_file(upfile: UploadFile, name_prefix: str):
            dest = os.path.join(fr_dir, f"{stamp}-{name_prefix}-{upfile.filename}")
            with open(dest, "wb") as out:
                shutil.copyfileobj(upfile.file, out)

        if sheet_file: save_file(sheet_file, "sheet")
        if photos:
            for i, p in enumerate(photos):
                if p: save_file(p, f"photo{i+1}")

        return {"message": "Material del franquiciado recibido", "email": franchisee_email, "room_id": room_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
