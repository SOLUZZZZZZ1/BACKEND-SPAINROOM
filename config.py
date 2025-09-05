import os

class Config:
    # Lee la URL de la base de datos de las variables de entorno
    raw_url = os.getenv("DATABASE_URL", "").strip()
    if raw_url.startswith("postgres://"):
        raw_url = "postgresql://" + raw_url[len("postgres://"):]
    # Si no hay DATABASE_URL definida, usar SQLite local
    SQLALCHEMY_DATABASE_URI = raw_url or "sqlite:///app.db"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }
