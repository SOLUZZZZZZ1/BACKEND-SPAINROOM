# backend/tenants.py
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from datetime import datetime
import os, shutil

router = APIRouter()
BASE_DIR = os.getenv("UPLOAD_DIR", "uploads")
TENANTS_DIR = os.path.join(BASE_DIR, "tenants")
os.makedirs(TENANTS_DIR, exist_ok=True)

@router.post("/tenants/upload-documents")
async def upload_tenant_documents(
    tenant_email: str = Form(...),
    phone_number: str = Form(...),
    id_file: UploadFile = File(...),
    bill_file: UploadFile = File(...),
):
    try:
        stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        user_dir = os.path.join(TENANTS_DIR, tenant_email.replace("@", "_"))
        os.makedirs(user_dir, exist_ok=True)

        def save_file(upfile: UploadFile, name: str):
            dest = os.path.join(user_dir, f"{stamp}-{name}-{upfile.filename}")
            with open(dest, "wb") as out:
                shutil.copyfileobj(upfile.file, out)

        save_file(id_file, "id")
        save_file(bill_file, "bill")
        return {"message": "Documentos del inquilino recibidos", "email": tenant_email}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
