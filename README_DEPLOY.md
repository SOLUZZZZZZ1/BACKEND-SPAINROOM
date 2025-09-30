# SpainRoom — Correcciones y Deploy (Render + Alembic)

## Archivos incluidos
- `requirements-full.txt` → dependencias completas (añade: alembic, pandas, openpyxl, twilio, stripe).
- `models_owner.py` → FIX import (`from extensions import db`).
- `models_franchise_slots.py` → modelo simple usado por `routes_admin_franchise.py`.
- `codigo_api.py` → protegido: `db.create_all()` solo si `SPAINROOM_CREATE_ALL=true`.
- `alembic/` + `alembic.ini` → migración inicial `0001_init_spainroom.py` con **todas** las tablas:
  - rooms, reservas, uploads, contracts + contract_items
  - leads, contact_messages, kyc_sessions, remesas, owner_checks
  - **franquicia avanzada**: `franquicia_slots`, `franquicia_ocupacion`
  - **franquicia simple**: `franchise_slots` (compat con `routes_admin_franchise.py`)

## Pasos (Render)
1. **Render → PostgreSQL**: usa **Internal Database URL** en el servicio FastAPI:
   - `DATABASE_URL=postgresql://USER:PASSWORD@dpg-xxx-a.internal:5432/spainroom`
2. **Variables entorno** (mínimas útiles):
   - `ADMIN_API_KEY=ramon`
   - `BACKEND_FEATURE_FRANQ_PLAZAS=on` (si usas `/franquicia/*`)
   - `FRONTEND_BASE_URL=https://spainroom.vercel.app` (ajusta)
   - `JWT_SECRET` (elige uno), `SECRET_KEY` (elige uno)
3. **Instalar deps**: usa `requirements-full.txt`.
4. **Migración** (recomendado):
   - En **Shell** del servicio o **Post-deploy hook**:
     ```bash
     alembic upgrade head
     ```
5. **Arranque**:
   - No uses `create_all` en producción (queda inactivo salvo `SPAINROOM_CREATE_ALL=true`).
   - Salud: `/health` — Swagger no se incluye por Flask puro.

## Útiles
- **Admin franquicia (simple)**:
  - `POST /api/admin/franquicia/ingest` — sube CSV `provincia,municipio,poblacion`
  - `GET  /api/admin/franquicia/slots?provincia=&municipio=&status=`
  - `POST /api/admin/franquicia/slots/ocupar` / `.../liberar`
  - `GET  /api/admin/franquicia/summary`
  - `GET  /api/admin/franquicia/export.xlsx`
  - Cabecera: `X-Admin-Key: $ADMIN_API_KEY`
- **Franq avanzada** (`/franquicia/*`) si activas `BACKEND_FEATURE_FRANQ_PLAZAS=on`.

## Desarrollo local
```bash
export DATABASE_URL=postgresql://USER:PASS@host:5432/spainroom?sslmode=require
pip install -r requirements-full.txt
alembic upgrade head
export SPAINROOM_CREATE_ALL=true    # solo si quieres create_all()
python codigo_api.py
```
