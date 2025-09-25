
# SpainRoom — Remesas (RIA) — API backend

## Archivos
- `models_remesas.py` — tabla `remesas`
- `routes_remesas.py` — endpoints:
  - `GET  /api/remesas/quote`            → cotización demo
  - `POST /api/remesas/start`            → inicia remesa, devuelve URL widget (RIA)
  - `POST /api/remesas/webhook`          → callback de RIA (HMAC opcional)
  - `GET  /api/remesas/mias`             → lista del usuario (X-User-Id)

## Registro en tu API
En `codigo_api.py` (o tu app API), registra el blueprint (ya está tolerante):
```python
_try_register(app, "routes_remesas", "bp_remesas", None)
```

## Variables de entorno
- `RIA_WIDGET_BASE`     = URL base del widget Hosted (sandbox/prod)
- `RIA_PARTNER_ID`      = identificador de partner (SpainRoom)
- `RIA_PARTNER_SECRET`  = secreto para firmar `start` (HMAC) — opcional en demo
- `RIA_WEBHOOK_SECRET`  = secreto para validar `webhook` (HMAC) — opcional en demo

## Seguridad y datos
- SpainRoom guarda **mínimos** (principio de minimización). RIA es **KYC owner**.
- Idempotencia: el webhook crea un registro si no existiera (`user_id=0`) y actualiza su estado.
- Sustituye el mecanismo de `_get_user_id()` por JWT si ya emites tokens desde `/api/auth`.
