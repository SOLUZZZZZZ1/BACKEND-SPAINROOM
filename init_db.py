"""
Crea el esquema de la base de datos según los modelos.
- Si usas Postgres: define DATABASE_URL antes de ejecutar.
- Si usas SQLite local, creará app.db en la carpeta backend.
"""
from app import app, db

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        print("✅ Tablas creadas/actualizadas según los modelos.")
