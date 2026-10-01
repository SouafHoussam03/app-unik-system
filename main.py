import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

app = FastAPI(title="UNIK SYSTEM API", version="1.0.0")

MONGODB_URI = os.getenv("MONGODB_URI", "")
MONGODB_DB = os.getenv("MONGODB_DB", "app-unik-system")
API_KEY = os.getenv("API_KEY", "")

ALLOWED_COLLECTIONS = {
    "settings", "products", "movements", "invoices",
    "items", "clients", "payments", "counters"
}

client = MongoClient(
    MONGODB_URI,
    serverSelectionTimeoutMS=10000,
    connectTimeoutMS=10000,
)
db = client[MONGODB_DB]

# Indexes nécessaires à l'application.
for collection, field, unique, sparse in [
    ("products", "id", True, False),
    ("products", "ref", True, True),
    ("invoices", "id", True, False),
    ("invoices", "number", True, False),
    ("items", "invoice_id", False, False),
    ("clients", "id", True, False),
    ("payments", "invoice_id", False, False),
    ("movements", "product_id", False, False),
    ("settings", "key", True, False),
]:
    try:
        db[collection].create_index([(field, ASCENDING)], unique=unique, sparse=sparse)
    except Exception as exc:
        print(f"Index ignoré {collection}.{field}: {exc}")


def auth(x_api_key: str | None):
    # En production, garder une API_KEY dans Render.
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="API key invalide")


def check_collection(name: str):
    if name not in ALLOWED_COLLECTIONS:
        raise HTTPException(status_code=400, detail="Collection non autorisée")
    return db[name]


def jsonable(value: Any):
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    # Les documents de l'application utilisent des ids numériques/string.
    # On retire _id pour garder l'API simple et compatible avec le client desktop.
    try:
        from bson import ObjectId
        if isinstance(value, ObjectId):
            return str(value)
    except Exception:
        pass
    return value


@app.get("/")
def root():
    return {"status": "online", "application": "UNIK SYSTEM", "database": MONGODB_DB}


@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok", "mongodb": "connected"}
    except PyMongoError as exc:
        return {"status": "error", "mongodb": "disconnected", "error": str(exc)}


@app.post("/api/db/find")
def api_find(body: dict, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    coll = check_collection(body["collection"])
    projection = body.get("projection")
    if projection and projection.get("_id", 1) == 0:
        pass
    rows = list(coll.find(body.get("filter", {}), projection))
    return {"rows": [jsonable({k: v for k, v in r.items() if k != "_id"}) for r in rows]}


@app.post("/api/db/find_one")
def api_find_one(body: dict, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    coll = check_collection(body["collection"])
    row = coll.find_one(body.get("filter", {}), body.get("projection"))
    if row and "_id" in row:
        row.pop("_id", None)
    return {"row": jsonable(row)}


@app.post("/api/db/insert_one")
def api_insert_one(body: dict, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    coll = check_collection(body["collection"])
    try:
        result = coll.insert_one(body["document"])
        return {"inserted_id": str(result.inserted_id)}
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail=f"Duplicate key: {exc}")


@app.post("/api/db/insert_many")
def api_insert_many(body: dict, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    coll = check_collection(body["collection"])
    try:
        result = coll.insert_many(body.get("documents", []))
        return {"inserted_ids": [str(x) for x in result.inserted_ids]}
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail=f"Duplicate key: {exc}")


@app.post("/api/db/update_one")
def api_update_one(body: dict, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    coll = check_collection(body["collection"])
    try:
        result = coll.update_one(
            body.get("filter", {}),
            body.get("update", {}),
            upsert=bool(body.get("upsert", False)),
        )
        return {
            "matched_count": result.matched_count,
            "modified_count": result.modified_count,
            "upserted_id": str(result.upserted_id) if result.upserted_id else None,
        }
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail=f"Duplicate key: {exc}")


@app.post("/api/db/delete_one")
def api_delete_one(body: dict, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    result = check_collection(body["collection"]).delete_one(body.get("filter", {}))
    return {"deleted_count": result.deleted_count}


@app.post("/api/db/delete_many")
def api_delete_many(body: dict, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    result = check_collection(body["collection"]).delete_many(body.get("filter", {}))
    return {"deleted_count": result.deleted_count}


@app.post("/api/db/count_documents")
def api_count_documents(body: dict, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    count = check_collection(body["collection"]).count_documents(body.get("filter", {}))
    return {"count": count}


@app.post("/api/db/find_one_and_update")
def api_find_one_and_update(body: dict, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    try:
        row = check_collection(body["collection"]).find_one_and_update(
            body.get("filter", {}),
            body.get("update", {}),
            return_document=ReturnDocument.AFTER,
            upsert=bool(body.get("upsert", False)),
        )
        if row and "_id" in row:
            row.pop("_id", None)
        return {"row": jsonable(row)}
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail=f"Duplicate key: {exc}")
