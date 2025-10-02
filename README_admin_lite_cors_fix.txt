SpainRoom — Admin Lite + CORS (Arreglo rápido)

1) Usa este backend modificado (CORS ampliado):
   - archivo: codigo_api_fixed.py
   - cambia _allowed_origin para aceptar http://127.0.0.1:8089 y http://localhost:8089

2) Ejecuta backend en local (Flask):
   set FLASK_APP=codigo_api_fixed.py
   set FLASK_ENV=development
   python codigo_api_fixed.py
   # o: python -m flask run --host 127.0.0.1 --port 5000

3) Sirve el Admin Lite FIX desde puerto 8089 para evitar file://
   cd C:\spainroom\backend-api
   python -m http.server 8089
   Abre: http://127.0.0.1:8089/admin-lite-fixed.html

4) En la página, completa:
   - API Base: http://127.0.0.1:5000   (o tu URL en Render)
   - Admin Key: (tu clave)
   Luego: "Subir en partes". Verás barra, contador y tabla (OK/Fallo + Reintentar).

Si tu backend está en Render, despliega el archivo codigo_api_fixed.py y repite el test.
