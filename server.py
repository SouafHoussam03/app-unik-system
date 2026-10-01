"""
server.py - API UNIK SYSTEM (FastAPI + MongoDB) à déployer sur Render.
Variables d'environnement : MONGODB_URI, MONGODB_DB (défaut app-unik-system), API_KEY (recommandé).
Démarrage : uvicorn server:app --host 0.0.0.0 --port $PORT
"""
import os
from typing import Any, Optional

from bson import ObjectId
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pymongo import MongoClient, ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
MONGODB_DB = os.getenv("MONGODB_DB", "app-unik-system")
API_KEY = os.getenv("API_KEY", "")
ALLOWED = {"settings", "products", "movements", "invoices", "items", "clients", "payments", "counters"}

client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=8000)
db = client[MONGODB_DB]

for coll, key, opts in [("products", "id", dict(unique=True)), ("products", "ref", dict(unique=True, sparse=True)),
                        ("invoices", "id", dict(unique=True)), ("invoices", "number", dict(unique=True)),
                        ("items", "invoice_id", {}), ("clients", "id", dict(unique=True)),
                        ("payments", "invoice_id", {}), ("movements", "product_id", {}),
                        ("settings", "key", dict(unique=True))]:
    try:
        db[coll].create_index([(key, ASCENDING)], **opts)
    except Exception as ex:
        print("Index ignoré :", coll, key, ex)

app = FastAPI(title="UNIK SYSTEM API")


class Req(BaseModel):
    coll: str
    op: str
    filter: Optional[dict] = None
    projection: Optional[dict] = None
    sort: Optional[list] = None
    limit: int = 0
    doc: Optional[dict] = None
    docs: Optional[list] = None
    update: Optional[dict] = None
    upsert: bool = False
    after: bool = False


def clean(d):
    if isinstance(d, dict) and isinstance(d.get("_id"), ObjectId):
        d["_id"] = str(d["_id"])
    return d


@app.get("/")
def root():
    return {"status": "online", "application": "UNIK SYSTEM", "database": MONGODB_DB}


@app.post("/api/db")
def run(r: Req, x_api_key: str = Header(default="")):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(401, "Clé API invalide")
    if r.coll not in ALLOWED:
        raise HTTPException(400, "Collection non autorisée")
    c, f = db[r.coll], r.filter or {}
    try:
        if r.op == "find":
            cur = c.find(f, r.projection)
            if r.sort:
                cur = cur.sort([(k, d) for k, d in r.sort])
            if r.limit:
                cur = cur.limit(r.limit)
            res = [clean(x) for x in cur]
        elif r.op == "find_one":
            cur = c.find(f, r.projection)
            if r.sort:
                cur = cur.sort([(k, d) for k, d in r.sort])
            res = clean(next(iter(cur.limit(1)), None))
        elif r.op == "insert_one":
            c.insert_one(r.doc or {})
            res = True
        elif r.op == "insert_many":
            if r.docs:
                c.insert_many(r.docs)
            res = True
        elif r.op == "update_one":
            res = c.update_one(f, r.update or {}, upsert=r.upsert).modified_count
        elif r.op == "find_one_and_update":
            res = clean(c.find_one_and_update(
                f, r.update or {}, upsert=r.upsert,
                return_document=ReturnDocument.AFTER if r.after else ReturnDocument.BEFORE))
        elif r.op == "delete_one":
            res = c.delete_one(f).deleted_count
        elif r.op == "delete_many":
            res = c.delete_many(f).deleted_count
        elif r.op == "count_documents":
            res = c.count_documents(f)
        else:
            raise HTTPException(400, "Opération inconnue")
    except DuplicateKeyError as e:
        return JSONResponse({"error": "duplicate", "detail": str(e)}, status_code=409)
    return {"result": res}
