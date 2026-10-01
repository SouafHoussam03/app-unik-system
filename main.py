import os

from fastapi import FastAPI
from pymongo import MongoClient
from pymongo.errors import PyMongoError

app = FastAPI(title="UNIK SYSTEM API")

MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DB = os.getenv("MONGODB_DB", "app-unik-system")

client = MongoClient(
    MONGODB_URI,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000
)

db = client[MONGODB_DB]


@app.get("/")
def root():
    return {
        "status": "online",
        "application": "UNIK SYSTEM",
        "database": MONGODB_DB
    }


@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {
            "status": "ok",
            "mongodb": "connected"
        }
    except PyMongoError as e:
        return {
            "status": "error",
            "mongodb": "disconnected",
            "error": str(e)
        }