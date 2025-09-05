# SpainRoom - Cedula Backend (Render Deploy)

Archivos incluidos:
- requirements.txt
- Procfile
- render.yaml

## Pasos de despliegue en Render

1. Sube la carpeta `cedula-backend` a un repositorio GitHub.
2. Añade dentro estos archivos (junto a `app_verify_cedula.py`).
3. Entra a https://render.com y crea un nuevo Web Service.
4. Selecciona el repo y la carpeta `cedula-backend` como root.
5. Configuración:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app_verify_cedula:app -k gthread --threads 4 --timeout 120 --bind 0.0.0.0:$PORT`
6. Render creará la URL pública, ej: `https://spainroom-cedula.onrender.com`
7. Añade un endpoint de health en `app_verify_cedula.py` si no lo tienes:
```python
@app.get("/health")
def health():
    return {"ok": True}, 200
```
8. En tu frontend (Vercel), añade variable de entorno:
   - Key: `VITE_API_BASE`
   - Value: `https://spainroom-cedula.onrender.com`

## Notas
- El archivo `render.yaml` permite crear el servicio "From YAML".
- Flask-Cors está habilitado para permitir peticiones desde el frontend.
- Gunicorn se encarga de correr en producción con múltiples threads.
