from datetime import date, timedelta
from app import app, db, Room

def ensure_room(**kwargs):
    # Evita duplicados por título
    title = kwargs.get("title")
    exists = Room.query.filter_by(title=title).first()
    if exists:
        return exists
    r = Room(**kwargs)
    db.session.add(r)
    return r

if __name__ == "__main__":
    with app.app_context():
        # Crea tablas si no existen
        db.create_all()

        today = date.today()

        rooms = [
            dict(
                title="Habitación 1 – Centro, luminosa",
                price_eur=420,
                city="Madrid",
                images="casa-diseno.jpg,room1.jpg",
                size_m2=12,
                features="Armario, Escritorio, WiFi, Llave propia",
                availableFrom=today + timedelta(days=7),
                cedula_status="verified",
                cedula_ref="MAD-001",
                cedula_verification="auto",
                cedula_doc_url="https://example.com/cedula/mad-001.pdf",
            ),
            dict(
                title="Habitación 2 – Barrio Salamanca",
                price_eur=480,
                city="Madrid",
                images="casa-diseno.jpg,room2.jpg",
                size_m2=14,
                features="Balcón, Smart TV, Llave propia",
                availableFrom=today + timedelta(days=15),
                cedula_status="pending",
                cedula_ref="MAD-002",
            ),
            dict(
                title="Habitación 3 – Sol, reformada",
                price_eur=450,
                city="Madrid",
                images="casa-diseno.jpg,room3.jpg",
                size_m2=11,
                features="Cama 135x200, Escritorio, WiFi",
                availableFrom=today + timedelta(days=3),
                cedula_status="verified",
                cedula_ref="MAD-003",
                cedula_verification="manual",
                cedula_doc_url="https://example.com/cedula/mad-003.pdf",
            ),
        ]

        for data in rooms:
            ensure_room(**data)

        db.session.commit()
        print("✅ Seed completado: habitaciones creadas/aseguradas.")
