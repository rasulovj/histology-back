import asyncio
import json
import os
from typing import Dict, Any

from config import BASE_DIR

PREPARATIONS_DIR = os.path.join(BASE_DIR, "data", "preparations")
INDEX_FILE = os.path.join(PREPARATIONS_DIR, "preparations_index.json")
CATEGORIES_FILE = os.path.join(PREPARATIONS_DIR, "preparations_categories.json")


def _load_json(path: str) -> Dict[str, Any]:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


async def get_preparations_catalog() -> Dict[str, Any]:
    os.makedirs(PREPARATIONS_DIR, exist_ok=True)
    index = await asyncio.to_thread(_load_json, INDEX_FILE)

    # Filter broken entries to avoid sending dead files in bot.
    filtered: Dict[str, Any] = {}
    for prep_id, meta in index.items():
        path = meta.get("path", "")
        if path and os.path.exists(path):
            filtered[prep_id] = meta
    return filtered
