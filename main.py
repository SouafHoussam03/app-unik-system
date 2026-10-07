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

    PyJWT

    orjson            (recommandé : sérialisation JSON beaucoup plus rapide)



Commande de démarrage Render :

    uvicorn main:app --host 0.0.0.0 --port $PORT

"""

import hmac
import hashlib
import secrets
import time

import logging

import os

from contextlib import asynccontextmanager

from datetime import date, datetime

from typing import Any, Optional

import jwt

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
JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_TTL_HOURS = int(os.getenv("JWT_TTL_HOURS", "8"))
INITIAL_ADMIN_USERNAME = os.getenv("INITIAL_ADMIN_USERNAME", "").strip()
INITIAL_ADMIN_PASSWORD = os.getenv("INITIAL_ADMIN_PASSWORD", "")

if not JWT_SECRET:
    log.warning("JWT_SECRET non défini : les connexions utilisateur seront refusées.")



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

    log.warning("API_KEY non défini : toutes les routes /api/db/* seront refusées (503).")



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
    ("users", "username", True, False),

]





def seed_initial_admin():
    """Crée le premier administrateur uniquement si la collection users est vide."""
    if db.users.count_documents({}) > 0:
        return
    if not INITIAL_ADMIN_USERNAME or not INITIAL_ADMIN_PASSWORD:
        log.warning("Aucun utilisateur trouvé : définissez INITIAL_ADMIN_USERNAME et INITIAL_ADMIN_PASSWORD sur Render.")
        return

    username = INITIAL_ADMIN_USERNAME.lower()
    user = {
        "id": secrets.token_hex(12),
        "username": username,
        "role": "ADMIN",
        "permissions": sorted(PERMISSIONS),
        "is_active": True,
        "password_hash": _hash_password(INITIAL_ADMIN_PASSWORD),
        "created_at": datetime.utcnow().isoformat(),
        "last_login": "",
        "failed_attempts": 0,
        "locked_until": 0,
        "token_version": 0,
    }
    try:
        db.users.insert_one(user)
        log.info("Premier administrateur créé : %s", username)
    except DuplicateKeyError:
        pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    for coll, field, unique, sparse in INDEXES:
        try:
            db[coll].create_index([(field, ASCENDING)], unique=unique, sparse=sparse)
        except Exception as exc:
            log.warning("Index ignoré %s.%s : %s", coll, field, exc)
    seed_initial_admin()
    yield
    client.close()


app = FastAPI(title="UNIK SYSTEM API", version="4.0.0-secure", lifespan=lifespan,

              default_response_class=DefaultResponse)

app.add_middleware(GZipMiddleware, minimum_size=1024)





@app.exception_handler(PyMongoError)

async def mongo_error_handler(_: Request, exc: PyMongoError):

    log.error("Erreur MongoDB : %s", exc)

    return JSONResponse(status_code=503, content={"detail": "Base de données indisponible"})





# ------------------------------------------------------------ sécurité
PERMISSIONS = {
    "dashboard", "devis", "factures", "bc", "bl",
    "stock", "clients", "settings", "users",
}
ROLES = {"ADMIN", "MEMBER"}


def api_key_auth(x_api_key: Optional[str] = Header(default=None)):
    if not API_KEY:
        raise HTTPException(503, "API_KEY non configurée sur le serveur")
    if not x_api_key or not hmac.compare_digest(x_api_key.encode(), API_KEY.encode()):
        raise HTTPException(401, "API key invalide")
    return True


def _hash_password(password: str) -> str:
    """Hash mot de passe avec scrypt + sel aléatoire."""
    if not isinstance(password, str) or len(password) < 8:
        raise HTTPException(422, "Le mot de passe doit contenir au moins 8 caractères")
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$14$8$1${salt.hex()}${dk.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        alg, n_exp, r, p, salt_hex, hash_hex = encoded.split("$")
        if alg != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**int(n_exp),
                            r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def make_token(user: dict) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "permissions": user.get("permissions", []),
        "iat": now,
        "exp": now + JWT_TTL_HOURS * 3600,
        "type": "access",
        "ver": int(user.get("token_version", 0)),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def current_user(
    x_api_key: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
):
    api_key_auth(x_api_key)
    if not JWT_SECRET:
        raise HTTPException(503, "JWT_SECRET non configuré sur le serveur")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Connexion utilisateur requise")

    token = authorization[7:].strip()
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expirée. Veuillez vous reconnecter.")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Session invalide")

    user = db.users.find_one({"id": payload.get("sub")}, {"_id": 0})
    if not user:
        raise HTTPException(401, "Utilisateur introuvable")
    if not user.get("is_active", True):
        raise HTTPException(403, "Compte bloqué")
    if int(payload.get("ver", -1)) != int(user.get("token_version", 0)):
        raise HTTPException(401, "Session révoquée. Veuillez vous reconnecter.")
    return user


def require_admin(user: dict):
    if user.get("role") != "ADMIN":
        raise HTTPException(403, "Accès réservé à l'administrateur")
    return user


def normalize_permissions(role: str, permissions: Optional[list[str]]) -> list[str]:
    role = role.upper()
    if role not in ROLES:
        raise HTTPException(422, "Rôle invalide")
    if role == "ADMIN":
        return sorted(PERMISSIONS)
    p = [str(x) for x in (permissions or []) if str(x) in PERMISSIONS and str(x) != "users"]
    if "dashboard" not in p:
        p.insert(0, "dashboard")
    return sorted(set(p))


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






# ------------------------------------------------------------ modèles d'authentification
class LoginBody(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=1, max_length=200)


class UserCreateBody(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=200)
    role: str = "MEMBER"
    permissions: list[str] = Field(default_factory=list)
    is_active: bool = True


class UserUpdateBody(BaseModel):
    user_id: str = Field(min_length=1, max_length=100)
    role: Optional[str] = None
    permissions: Optional[list[str]] = None
    is_active: Optional[bool] = None


class UserPasswordBody(BaseModel):
    user_id: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=200)


def public_user(user: dict) -> dict:
    return {
        "id": str(user["id"]),
        "username": user["username"],
        "role": user.get("role", "MEMBER"),
        "permissions": user.get("permissions", []),
        "is_active": user.get("is_active", True),
        "created_at": user.get("created_at", ""),
        "last_login": user.get("last_login", ""),
    }


# ------------------------------------------------------------ authentification
@app.post("/api/auth/login")
def api_login(body: LoginBody, _: bool = Depends(api_key_auth)):
    username = body.username.strip().lower()
    user = db.users.find_one({"username": username})

    if not user:
        raise HTTPException(401, "Identifiant ou mot de passe incorrect")

    now = int(time.time())
    locked_until = int(user.get("locked_until", 0) or 0)
    if locked_until > now:
        minutes = max(1, (locked_until - now + 59) // 60)
        raise HTTPException(423, f"Compte temporairement bloqué. Réessayez dans {minutes} min.")

    if not _verify_password(body.password, user.get("password_hash", "")):
        failed = int(user.get("failed_attempts", 0) or 0) + 1
        update = {"$set": {"failed_attempts": failed}}
        if failed >= 5:
            update["$set"]["locked_until"] = now + 15 * 60
            update["$set"]["failed_attempts"] = 0
        db.users.update_one({"id": user["id"]}, update)
        raise HTTPException(401, "Identifiant ou mot de passe incorrect")

    if not user.get("is_active", True):
        raise HTTPException(403, "Compte bloqué. Contactez l'administrateur.")

    db.users.update_one(
        {"id": user["id"]},
        {"$set": {"failed_attempts": 0, "locked_until": 0,
                  "last_login": datetime.utcnow().isoformat()}}
    )
    user["failed_attempts"] = 0
    user["locked_until"] = 0
    return {"access_token": make_token(user), "token_type": "bearer", "user": public_user(user)}


@app.post("/api/auth/me")
def api_me(user: dict = Depends(current_user)):
    return {"user": public_user(user)}


# ------------------------------------------------------------ gestion utilisateurs (ADMIN uniquement)
@app.post("/api/users/list")
def api_users_list(user: dict = Depends(current_user)):
    require_admin(user)
    rows = db.users.find({}, {"_id": 0, "password_hash": 0, "failed_attempts": 0, "locked_until": 0}).sort("username", ASCENDING)
    return {"users": [public_user(r) for r in rows]}


@app.post("/api/users/create")
def api_users_create(body: UserCreateBody, user: dict = Depends(current_user)):
    require_admin(user)

    username = body.username.strip().lower()
    if any(ch.isspace() for ch in username) or not username.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(422, "Nom d'utilisateur invalide : lettres, chiffres, _ ou - uniquement")

    role = body.role.strip().upper()
    permissions = normalize_permissions(role, body.permissions)
    new_user = {
        "id": secrets.token_hex(12),
        "username": username,
        "role": role,
        "permissions": permissions,
        "is_active": body.is_active,
        "password_hash": _hash_password(body.password),
        "created_at": datetime.utcnow().isoformat(),
        "last_login": "",
        "failed_attempts": 0,
        "locked_until": 0,
    }
    try:
        db.users.insert_one(new_user)
    except DuplicateKeyError:
        raise HTTPException(409, "Ce nom d'utilisateur existe déjà")
    return {"user": public_user(new_user)}


@app.post("/api/users/update")
def api_users_update(body: UserUpdateBody, user: dict = Depends(current_user)):
    require_admin(user)

    target = db.users.find_one({"id": body.user_id})
    if not target:
        raise HTTPException(404, "Utilisateur introuvable")

    if target["id"] == user["id"] and body.role and body.role.upper() != "ADMIN":
        raise HTTPException(400, "Vous ne pouvez pas retirer le rôle ADMIN de votre propre compte.")

    update = {}
    if body.role is not None:
        update["role"] = body.role.upper()
        update["permissions"] = normalize_permissions(update["role"], body.permissions)
    elif body.permissions is not None:
        update["permissions"] = normalize_permissions(target.get("role", "MEMBER"), body.permissions)

    if body.is_active is not None:
        if target["id"] == user["id"] and not body.is_active:
            raise HTTPException(400, "Vous ne pouvez pas bloquer votre propre compte.")
        update["is_active"] = body.is_active

    if not update:
        return {"user": public_user(target)}

    # Empêche de désactiver / rétrograder le dernier administrateur.
    resulting_role = update.get("role", target.get("role", "MEMBER"))
    resulting_active = update.get("is_active", target.get("is_active", True))
    if target.get("role") == "ADMIN" and (resulting_role != "ADMIN" or not resulting_active):
        admin_count = db.users.count_documents({"role": "ADMIN", "is_active": True})
        if admin_count <= 1:
            raise HTTPException(400, "Impossible de désactiver ou rétrograder le dernier administrateur.")

    db.users.update_one({"id": target["id"]}, {"$set": update, "$inc": {"token_version": 1}})
    target.update(update)
    target["token_version"] = int(target.get("token_version", 0)) + 1
    return {"user": public_user(target)}


@app.post("/api/users/password")
def api_users_password(body: UserPasswordBody, user: dict = Depends(current_user)):
    require_admin(user)
    target = db.users.find_one({"id": body.user_id})
    if not target:
        raise HTTPException(404, "Utilisateur introuvable")
    db.users.update_one(
        {"id": target["id"]},
        {"$set": {"password_hash": _hash_password(body.password),
                  "failed_attempts": 0, "locked_until": 0},
         "$inc": {"token_version": 1}}
    )
    return {"ok": True}

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

@app.post("/api/db/find", dependencies=[Depends(current_user)])

def api_find(body: FindBody):

    check_ops(body.filter, FILTER_OPS)

    cur = coll_of(body.collection).find(body.filter, clean_projection(body.projection))

    if body.sort:

        cur = cur.sort(to_sort(body.sort))

    if body.skip:

        cur = cur.skip(body.skip)

    cur = cur.limit(body.limit or MAX_ROWS)

    return {"rows": [jsonable(r) for r in cur]}





@app.post("/api/db/find_one", dependencies=[Depends(current_user)])

def api_find_one(body: FindBody):

    check_ops(body.filter, FILTER_OPS)

    row = coll_of(body.collection).find_one(body.filter, clean_projection(body.projection),

                                            sort=to_sort(body.sort) or None)

    return {"row": jsonable(row) if row else None}





@app.post("/api/db/insert_one", dependencies=[Depends(current_user)])

def api_insert_one(body: InsertOneBody):

    check_doc(body.document)

    try:

        res = coll_of(body.collection).insert_one(body.document)

    except DuplicateKeyError as exc:

        raise HTTPException(409, f"Duplicate key: {exc}")

    return {"inserted_id": str(res.inserted_id)}





@app.post("/api/db/insert_many", dependencies=[Depends(current_user)])

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





@app.post("/api/db/update_one", dependencies=[Depends(current_user)])

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





@app.post("/api/db/delete_one", dependencies=[Depends(current_user)])

def api_delete_one(body: FilterBody):

    check_ops(body.filter, FILTER_OPS)

    return {"deleted_count": coll_of(body.collection).delete_one(body.filter).deleted_count}





@app.post("/api/db/delete_many", dependencies=[Depends(current_user)])

def api_delete_many(body: FilterBody):

    check_ops(body.filter, FILTER_OPS)

    return {"deleted_count": coll_of(body.collection).delete_many(body.filter).deleted_count}





@app.post("/api/db/count_documents", dependencies=[Depends(current_user)])

def api_count_documents(body: FilterBody):

    check_ops(body.filter, FILTER_OPS)

    return {"count": coll_of(body.collection).count_documents(body.filter)}





@app.post("/api/db/find_one_and_update", dependencies=[Depends(current_user)])

def api_find_one_and_update(body: UpdateBody):

    check_ops(body.filter, FILTER_OPS)

    check_ops(body.update, UPDATE_OPS)

    try:

        row = coll_of(body.collection).find_one_and_update(

            body.filter, body.update, return_document=ReturnDocument.AFTER, upsert=body.upsert)

    except DuplicateKeyError as exc:

        raise HTTPException(409, f"Duplicate key: {exc}")

    return {"row": jsonable(row) if row else None}





@app.post("/api/db/next_id", dependencies=[Depends(current_user)])

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





@app.post("/api/db/batch", dependencies=[Depends(current_user)])

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
