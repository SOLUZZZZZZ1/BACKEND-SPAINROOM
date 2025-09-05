# wsgi.py — punto de entrada WSGI para producción
from app import app as application  # Gunicorn busca 'application'