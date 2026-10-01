"""
unik_api.py - Client API pour UNIK SYSTEM Invoice Manager PRO MAX (API)
Remplace pymongo : expose la même interface (DB.collection.find / insert_one / ...)
mais envoie les requêtes au serveur HTTP (server.py) au lieu de MongoDB directement.
pip install requests
"""
import time
import requests

ASCENDING, DESCENDING = 1, -1


class ReturnDocument:
    BEFORE, AFTER = False, True


class DuplicateKeyError(Exception):
    pass


class ApiError(Exception):
    pass


class Cursor:
    def __init__(self, coll, filter, projection):
        self.coll, self.filter, self.projection = coll, filter or {}, projection
        self._sort, self._limit, self._data = None, 0, None

    def sort(self, key, direction=ASCENDING):
        self._sort = [[key, direction]] if isinstance(key, str) else [list(k) for k in key]
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _load(self):
        if self._data is None:
            self._data = self.coll._call("find", filter=self.filter, projection=self.projection,
                                         sort=self._sort, limit=self._limit)
        return self._data

    def __iter__(self):
        return iter(self._load())

    def __len__(self):
        return len(self._load())


class Collection:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def _call(self, op, **args):
        return self.db._post({"coll": self.name, "op": op, **args})

    def find(self, filter=None, projection=None):
        return Cursor(self, filter, projection)

    def find_one(self, filter=None, projection=None, sort=None):
        c = Cursor(self, filter, projection)
        if sort:
            c.sort(sort)
        r = self._call("find_one", filter=filter or {}, projection=projection, sort=c._sort)
        return r

    def insert_one(self, doc):
        return self._call("insert_one", doc=doc)

    def insert_many(self, docs):
        return self._call("insert_many", docs=list(docs))

    def update_one(self, filter, update, upsert=False):
        return self._call("update_one", filter=filter, update=update, upsert=upsert)

    def find_one_and_update(self, filter, update, return_document=False, upsert=False):
        return self._call("find_one_and_update", filter=filter, update=update,
                          after=bool(return_document), upsert=upsert)

    def delete_one(self, filter):
        return self._call("delete_one", filter=filter)

    def delete_many(self, filter):
        return self._call("delete_many", filter=filter)

    def count_documents(self, filter=None):
        return self._call("count_documents", filter=filter or {})

    def create_index(self, *a, **k):  # les index sont créés côté serveur
        return None


class RemoteDB:
    def __init__(self, url, api_key="", timeout=90):
        self.url, self.timeout = url.rstrip("/"), timeout
        self.session = requests.Session()
        if api_key:
            self.session.headers["X-API-Key"] = api_key

    def ping(self, tries=3):
        """Réveille le serveur Render (démarrage à froid possible) et vérifie la connexion."""
        last = None
        for _ in range(tries):
            try:
                r = self.session.get(self.url + "/", timeout=self.timeout)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last = e
                time.sleep(3)
        raise ApiError(f"API injoignable ({self.url}) : {last}")

    def _post(self, payload):
        try:
            r = self.session.post(self.url + "/api/db", json=payload, timeout=self.timeout)
        except requests.RequestException as e:
            raise ApiError(f"Erreur réseau : {e}")
        if r.status_code == 409:
            raise DuplicateKeyError(r.text)
        if r.status_code == 401:
            raise ApiError("Clé API invalide (variable API_KEY).")
        if r.status_code >= 400:
            raise ApiError(f"Erreur API {r.status_code} : {r.text[:300]}")
        return r.json()["result"]

    def __getitem__(self, name):
        return Collection(self, name)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return Collection(self, name)
