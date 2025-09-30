# SpainRoom Backend — Franquicias (FastAPI)

APIs para gestionar zonas, franquiciados, asignaciones y leads, con cálculo automático de plazas por población.

## Requisitos
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Ejecutar
```bash
uvicorn app.main:app --reload
```
- API docs: http://127.0.0.1:8000/docs

## Config
- `DATABASE_URL` (opcional). Por defecto usa SQLite: `sqlite:///./spainroom.db`.

## CSV Import
Endpoint: `POST /zonas/import`
- Acepta archivo CSV (`multipart/form-data`, campo `file`) con columnas: `provincia,municipio,poblacion`.
- Calcula `franquiciados_permitidos` (Madrid/Barcelona → /20000, resto → /10000, mínimo 1).
- Upsert por (`provincia`,`municipio`).

## Buscador
`GET /zonas?provincia=...&municipio=...&estado=...&page=1&size=50`

## Añadir/Quitar franquiciados
- Crear franquiciado: `POST /franquiciados`
- Asignar a zona: `POST /asignaciones`
- Quitar asignación: `DELETE /asignaciones/{id}`
Al cambiar asignaciones, se recalculan `franquiciados_asignados` y `estado` en la zona.

## Leads (teléfono de atención)
- `POST /leads` crea un lead y lo **rutea automáticamente** al franquiciado de la zona (si hay varios, asigna al que tenga menos leads en esa zona; en empate, el de menor id).
- Estados: `Nuevo`, `Enviado`, `Contactado`, `Cerrado`.



## Migraciones con Alembic (Producción recomendado)

- Instala dependencias (incluye `alembic` en `requirements.txt`).
- Define la URL de BD (Render):
  - **Render interno (producción)**: `DATABASE_URL=postgresql://USER:PASSWORD@dpg-...-a.internal:5432/DB`
  - **Externo (local)**: `DATABASE_URL=postgresql://USER:PASSWORD@dpg-...oregon-postgres.render.com:5432/DB?sslmode=require`

### Ejecutar migraciones
```bash
alembic upgrade head
```

> Nota: En producción, **no** uses `create_all`. El código ya lo protege con `SPAINROOM_CREATE_ALL`.
Para desarrollo local, puedes activarlo así:
```bash
export SPAINROOM_CREATE_ALL=true   # Windows: set SPAINROOM_CREATE_ALL=true
```

