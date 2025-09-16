# backend/owners.py
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from datetime import datetime
import os, shutil, re

router = APIRouter()
BASE_DIR = os.getenv("UPLOAD_DIR", "uploads")
OWNERS_DIR = os.path.join(BASE_DIR, "owners")
os.makedirs(OWNERS_DIR, exist_ok=True)

def normalize_email(email: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", email or "")

def normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")

def validate_iban(iban: str) -> bool:
    s = re.sub(r"\s+", "", iban or "").upper()
    return 15 <= len(s) <= 34 and s.isalnum()

@router.post("/owners/upload-documents")
async def upload_owner_documents(
    full_name: str = Form(...),
    phone: str = Form(...),
    iban: str = Form(...),
    owner_email: str | None = Form(None),
    dni_file: UploadFile | None = File(None),
    contract_file: UploadFile | None = File(None),
):
    try:
        if not validate_iban(iban):
            raise ValueError("IBAN no válido")
        stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        key = normalize_email(owner_email) or normalize_phone(phone)
        if not key:
            raise ValueError("Falta identificador: proporcione teléfono o email.")
        user_dir = os.path.join(OWNERS_DIR, key)
        os.makedirs(user_dir, exist_ok=True)

        meta_path = os.path.join(user_dir, f"{stamp}-owner.txt")
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(f"name={full_name}\nphone={phone}\nemail={owner_email or ''}\niban={iban}\n")

        def save_optional(upfile: UploadFile, name: str):
            dest = os.path.join(user_dir, f"{stamp}-{name}-{upfile.filename}")
            with open(dest, "wb") as out:
                shutil.copyfileobj(upfile.file, out)

        if dni_file: save_optional(dni_file, "dni")
        if contract_file: save_optional(contract_file, "contract")

        return {"message": "Datos del propietario recibidos", "owner_key": key}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
