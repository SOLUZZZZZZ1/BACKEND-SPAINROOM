# backend/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os

load_dotenv()

app = FastAPI(title="SpainRoom Backend API")

# CORS (ajusta si usas otros orígenes)
frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://127.0.0.1:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[frontend_origin, "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health():
    return {"status": "ok"}

# === Routers existentes opcionales ===
# (Descomenta si tienes estos módulos)
try:
    from .payments import router as payments_router
    app.include_router(payments_router, prefix="/api", tags=["payments"])
except Exception as e:
    print("[INFO] payments router no cargado:", e)

try:
    from .otp import router as otp_router
    app.include_router(otp_router, prefix="/api", tags=["otp"])
except Exception as e:
    print("[INFO] otp router no cargado:", e)

# === Routers de subidas (activados) ===
from .tenants import router as tenants_router
from .owners import router as owners_router
from .franchisees import router as franchisees_router

app.include_router(tenants_router, prefix="/api", tags=["tenants"])
app.include_router(owners_router, prefix="/api", tags=["owners"])
app.include_router(franchisees_router, prefix="/api", tags=["franchisees"])
