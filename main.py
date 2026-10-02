"""

UNIK SYSTEM - API Render (FastAPI + MongoDB Atlas)



Variables d'environnement (Render > Environment) :

    MONGODB_URI   obligatoire  (mongodb+srv://...)

    API_KEY       obligatoire  (même valeur que UNIK_API_KEY côté application)

    MONGODB_DB    facultatif   (défaut : app-unik-system)



requirements.txt :

    fastapi

    uvicorn[standard]

    pymongo[srv]

    pydantic

    orjson            (recommandé : sérialisation JSON beaucoup plus rapide)



Commande de démarrage Render :

    uvicorn main:app --host 0.0.0.0 --port $PORT

"""

import hmac

import logging

import os

from contextlib import asynccontextmanager

from datetime import date, datetime

from typing import Any, Optional



from bson import ObjectId

from fastapi import Depends, FastAPI, Header, HTTPException, Request

from fastapi.middleware.gzip import GZipMiddleware

from fastapi.responses import JSONResponse

from pydantic import BaseModel, Field, ValidationError

from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument

from pymongo.errors import DuplicateKeyError, PyMongoError



try:  # JSON rapide si orjson est installé

    import orjson  # noqa: F401

    from fastapi.responses import ORJSONResponse as DefaultResponse

except ImportError:

    DefaultResponse = JSONResponse



log = logging.getLogger("unik")

logging.basicConfig(level=logging.INFO)



MONGODB_URI = os.getenv("MONGODB_URI", "")

MONGODB_DB = os.getenv("MONGODB_DB", "app-unik-system")

API_KEY = os.getenv("API_KEY", "")



ALLOWED_COLLECTIONS = {"settings", "products", "movements", "invoices",

                       "items", "clients", "payments", "counters"}

# Collections disposant d'un champ numérique "id" géré par next_id

ID_COLLECTIONS = {"products", "movements", "invoices", "clients", "payments"}



# Opérateurs MongoDB autorisés (aucun $where / $expr / $function ...)

FILTER_OPS = {"$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin", "$regex", "$options",

              "$exists", "$and", "$or", "$nor", "$not"}

UPDATE_OPS = {"$set", "$setOnInsert", "$inc", "$unset", "$min", "$max"}

MAX_ROWS = 50_000



if not MONGODB_URI:

    raise RuntimeError("MONGODB_URI n'est pas défini.")

if not API_KEY:

    log.warning("API_KEY non défini : toutes les routes /api/db/\* seront refusées (503).")



client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000,

                     retryWrites=True, appname="unik-system-api",

                     maxPoolSize=30, minPoolSize=2, compressors="zlib")

db = client[MONGODB_DB]



INDEXES = [

    ("products", "id", True, False),

    ("products", "ref", True, True),

    ("invoices", "id", True, False),

    ("invoices", "number", True, False),

    ("invoices", "doctype", False, False),

    ("items", "invoice_id", False, False),

    ("clients", "id", True, False),

    ("payments", "invoice_id", False, False),

    ("movements", "product_id", False, False),

    ("settings", "key", True, False),

]





@asynccontextmanager

async def lifespan(_: FastAPI):

    for coll, field, unique, sparse in INDEXES:

        try:

            db[coll].create_index([(field, ASCENDING)], unique=unique, sparse=sparse)

        except Exception as exc:  # index existant différent, données dupliquées...

            log.warning("Index ignoré %s.%s : %s", coll, field, exc)

    yield

    client.close()





app = FastAPI(title="UNIK SYSTEM API", version="3.0.0", lifespan=lifespan,

              default_response_class=DefaultResponse)

app.add_middleware(GZipMiddleware, minimum_size=1024)





@app.exception_handler(PyMongoError)

async def mongo_error_handler(_: Request, exc: PyMongoError):

    log.error("Erreur MongoDB : %s", exc)

    return JSONResponse(status_code=503, content={"detail": "Base de données indisponible"})





# ------------------------------------------------------------ sécurité

def auth(x_api_key: Optional[str] = Header(default=None)):

    if not API_KEY:

        raise HTTPException(503, "API_KEY non configurée sur le serveur")

    if not x_api_key or not hmac.compare_digest(x_api_key.encode(), API_KEY.encode()):

        raise HTTPException(401, "API key invalide")





def coll_of(name: str):

    if name not in ALLOWED_COLLECTIONS:

        raise HTTPException(400, "Collection non autorisée")

    return db[name]





def check_ops(node: Any, allowed: set, depth: int = 0):

    """Refuse les opérateurs non listés (protection contre l'injection NoSQL)."""

    if depth > 8:

        raise HTTPException(400, "Requête trop profonde")

    if isinstance(node, dict):

        for k, v in node.items():

            if isinstance(k, str) and k.startswith("$") and k not in allowed:

                raise HTTPException(400, f"Opérateur interdit : {k}")

            if k == "$regex" and isinstance(v, str) and len(v) > 200:

                raise HTTPException(400, "Expression régulière trop longue")

            check_ops(v, allowed, depth + 1)

    elif isinstance(node, list):

        for v in node:

            check_ops(v, allowed, depth + 1)





def check_doc(node: Any, depth: int = 0):

    if depth > 8:

        raise HTTPException(400, "Document trop profond")

    if isinstance(node, dict):

        for k, v in node.items():

            if isinstance(k, str) and k.startswith("$"):

                raise HTTPException(400, "Clé de document interdite")

            check_doc(v, depth + 1)

    elif isinstance(node, list):

        for v in node:

            check_doc(v, depth + 1)





def jsonable(value: Any):

    if isinstance(value, dict):

        return {k: jsonable(v) for k, v in value.items() if k != "_id"}

    if isinstance(value, list):

        return [jsonable(v) for v in value]

    if isinstance(value, ObjectId):

        return str(value)

    if isinstance(value, (datetime, date)):

        return value.isoformat()

    return value





def clean_projection(p):

    if not p:

        return None

    if not isinstance(p, dict) or not all(isinstance(v, (int, bool)) for v in p.values()):

        raise HTTPException(400, "Projection invalide")

    return p





def to_sort(sort):

    return [(f, DESCENDING if int(d) < 0 else ASCENDING) for f, d in (sort or [])]





# ------------------------------------------------------------ modèles

class Base(BaseModel):

    collection: str





class FindBody(Base):

    filter: dict = Field(default_factory=dict)

    projection: Optional[dict] = None

    sort: Optional[list] = None

    limit: int = Field(default=0, ge=0, le=MAX_ROWS)

    skip: int = Field(default=0, ge=0)





class InsertOneBody(Base):

    document: dict





class InsertManyBody(Base):

    documents: list[dict] = Field(default_factory=list)





class UpdateBody(Base):

    filter: dict = Field(default_factory=dict)

    update: dict = Field(default_factory=dict)

    upsert: bool = False





class FilterBody(Base):

    filter: dict = Field(default_factory=dict)





class NextIdBody(BaseModel):

    name: str

    count: int = Field(default=1, ge=1, le=500)





class BatchBody(BaseModel):

    ops: list[dict] = Field(default_factory=list, max_length=200)





# ------------------------------------------------------------ routes publiques

@app.get("/")

def root():

    return {"status": "online", "application": "UNIK SYSTEM", "version": app.version}





@app.get("/health")

def health():

    try:

        client.admin.command("ping")

        return {"status": "ok", "mongodb": "connected"}

    except PyMongoError as exc:

        log.error("Ping MongoDB échoué : %s", exc)

        return {"status": "error", "mongodb": "disconnected"}





# ------------------------------------------------------------ routes base de données

@app.post("/api/db/find", dependencies=[Depends(auth)])

def api_find(body: FindBody):

    check_ops(body.filter, FILTER_OPS)

    cur = coll_of(body.collection).find(body.filter, clean_projection(body.projection))

    if body.sort:

        cur = cur.sort(to_sort(body.sort))

    if body.skip:

        cur = cur.skip(body.skip)

    cur = cur.limit(body.limit or MAX_ROWS)

    return {"rows": [jsonable(r) for r in cur]}





@app.post("/api/db/find_one", dependencies=[Depends(auth)])

def api_find_one(body: FindBody):

    check_ops(body.filter, FILTER_OPS)

    row = coll_of(body.collection).find_one(body.filter, clean_projection(body.projection),

                                            sort=to_sort(body.sort) or None)

    return {"row": jsonable(row) if row else None}





@app.post("/api/db/insert_one", dependencies=[Depends(auth)])

def api_insert_one(body: InsertOneBody):

    check_doc(body.document)

    try:

        res = coll_of(body.collection).insert_one(body.document)

    except DuplicateKeyError as exc:

        raise HTTPException(409, f"Duplicate key: {exc}")

    return {"inserted_id": str(res.inserted_id)}





@app.post("/api/db/insert_many", dependencies=[Depends(auth)])

def api_insert_many(body: InsertManyBody):

    if not body.documents:

        return {"inserted_ids": []}

    for d in body.documents:

        check_doc(d)

    try:

        res = coll_of(body.collection).insert_many(body.documents)

    except DuplicateKeyError as exc:

        raise HTTPException(409, f"Duplicate key: {exc}")

    return {"inserted_ids": [str(x) for x in res.inserted_ids]}





@app.post("/api/db/update_one", dependencies=[Depends(auth)])

def api_update_one(body: UpdateBody):

    check_ops(body.filter, FILTER_OPS)

    check_ops(body.update, UPDATE_OPS)

    if not body.update:

        raise HTTPException(400, "Mise à jour vide")

    try:

        res = coll_of(body.collection).update_one(body.filter, body.update, upsert=body.upsert)

    except DuplicateKeyError as exc:

        raise HTTPException(409, f"Duplicate key: {exc}")

    return {"matched_count": res.matched_count, "modified_count": res.modified_count,

            "upserted_id": str(res.upserted_id) if res.upserted_id else None}





@app.post("/api/db/delete_one", dependencies=[Depends(auth)])

def api_delete_one(body: FilterBody):

    check_ops(body.filter, FILTER_OPS)

    return {"deleted_count": coll_of(body.collection).delete_one(body.filter).deleted_count}





@app.post("/api/db/delete_many", dependencies=[Depends(auth)])

def api_delete_many(body: FilterBody):

    check_ops(body.filter, FILTER_OPS)

    return {"deleted_count": coll_of(body.collection).delete_many(body.filter).deleted_count}





@app.post("/api/db/count_documents", dependencies=[Depends(auth)])

def api_count_documents(body: FilterBody):

    check_ops(body.filter, FILTER_OPS)

    return {"count": coll_of(body.collection).count_documents(body.filter)}





@app.post("/api/db/find_one_and_update", dependencies=[Depends(auth)])

def api_find_one_and_update(body: UpdateBody):

    check_ops(body.filter, FILTER_OPS)

    check_ops(body.update, UPDATE_OPS)

    try:

        row = coll_of(body.collection).find_one_and_update(

            body.filter, body.update, return_document=ReturnDocument.AFTER, upsert=body.upsert)

    except DuplicateKeyError as exc:

        raise HTTPException(409, f"Duplicate key: {exc}")

    return {"row": jsonable(row) if row else None}





@app.post("/api/db/next_id", dependencies=[Depends(auth)])

def api_next_id(body: NextIdBody):

    """Compteur atomique. Réserve `count` ids d'un coup : renvoie le dernier (seq) et le premier (first)."""

    name = body.name

    if name not in ID_COLLECTIONS:

        raise HTTPException(400, "Compteur non autorisé")

    for _ in range(5):

        row = db.counters.find_one_and_update({"_id": name}, {"$inc": {"seq": body.count}},

                                              return_document=ReturnDocument.AFTER)

        if row:

            seq = int(row["seq"])

            return {"seq": seq, "first": seq - body.count + 1}

        last = db[name].find_one({"id": {"$type": "number"}}, {"id": 1}, sort=[("id", DESCENDING)])

        try:

            db.counters.insert_one({"_id": name, "seq": int(last["id"]) if last else 0})

        except DuplicateKeyError:

            pass  # un autre appel vient de l'initialiser : on réessaie

    raise HTTPException(500, "Impossible de générer l'identifiant")





# Plusieurs opérations en UNE seule requête HTTP (gain majeur de vitesse : moins d'aller-retours).

# Exécution séquentielle, non transactionnelle : en cas d'erreur, les opérations déjà faites restent appliquées.

BATCH_OPS = {

    "find": (FindBody, api_find),

    "find_one": (FindBody, api_find_one),

    "insert_one": (InsertOneBody, api_insert_one),

    "insert_many": (InsertManyBody, api_insert_many),

    "update_one": (UpdateBody, api_update_one),

    "delete_one": (FilterBody, api_delete_one),

    "delete_many": (FilterBody, api_delete_many),

    "count_documents": (FilterBody, api_count_documents),

    "find_one_and_update": (UpdateBody, api_find_one_and_update),

    "next_id": (NextIdBody, api_next_id),

}





@app.post("/api/db/batch", dependencies=[Depends(auth)])

def api_batch(body: BatchBody):

    results = []

    for n, op in enumerate(body.ops):

        spec = BATCH_OPS.get(op.get("op"))

        if not spec:

            raise HTTPException(400, f"Opération inconnue (#{n}) : {op.get('op')}")

        model, fn = spec

        try:

            payload = model(**{k: v for k, v in op.items() if k != "op"})

        except ValidationError as exc:

            raise HTTPException(422, f"Opération #{n} invalide : {exc.errors()[0]['msg']}")

        results.append(fn(payload))

    return {"results": results}
