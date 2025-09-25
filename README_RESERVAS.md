# SpainRoom — Reservas (mínimo viable)

## Archivos
- models_reservas.py
- routes_reservas.py

## Registro en app.py
En tu `create_app()` añade:
```python
from routes_reservas import bp_reservas
app.register_blueprint(bp_reservas)
```

Asegúrate de ejecutar `db.create_all()` (tu app ya lo hace).

## Pruebas rápidas (curl)
Disponibilidad:
```
curl -s "http://localhost:5000/api/reservas/availability?room_id=1&start=2025-10-01&end=2025-10-05"
```

Crear (pending):
```
curl -s -X POST http://localhost:5000/api/reservas -H "Content-Type: application/json" -d '{
  "room_id": 1, "nombre": "Juan Pérez", "email": "juan@example.com",
  "start_date": "2025-10-01", "end_date": "2025-10-05"
}'
```

Listar pending:
```
curl -s "http://localhost:5000/api/reservas?status=pending"
```

Aprobar:
```
curl -s -X PATCH http://localhost:5000/api/reservas/1 -H "Content-Type: application/json" -d '{"status":"approved"}'
```

Cancelar:
```
curl -s -X PATCH http://localhost:5000/api/reservas/1 -H "Content-Type: application/json" -d '{"status":"cancelled"}'
```
