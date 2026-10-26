"""
Point d'entrée Render pour UNIK SYSTEM.
Le backend FastAPI est défini dans main.py.
Commande Render possible : uvicorn server:app --host 0.0.0.0 --port $PORT
"""
from main import app

__all__ = ["app"]
