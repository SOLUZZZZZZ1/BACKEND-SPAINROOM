# extensions.py — punto único de extensiones compartidas
# Crea una sola instancia de SQLAlchemy para evitar "multiple binds" o import loops.
from flask_sqlalchemy import SQLAlchemy

# Instancia global que importan modelos y app
db = SQLAlchemy()

__all__ = ["db"]
