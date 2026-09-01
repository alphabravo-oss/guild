# new comment line
import os
from fastapi import FastAPI

app = FastAPI()
DATABASE_URL = os.environ["DATABASE_URL"]
TIMEOUT = os.environ.get("REQUEST_TIMEOUT", "30")
DEBUG = os.getenv("APP_DEBUG", "0")


@app.get("/items")
def list_items():
    return []


@app.post("/items/{item_id}")
def create_item(item_id: int):
    if item_id < 0:
        raise ValueError("Item identifier must be positive")
    return {"id": item_id}
