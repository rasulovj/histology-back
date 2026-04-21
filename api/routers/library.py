import os
import json
import time
import math
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File

from api.auth import verify_token

router = APIRouter()

from config import DB_PATH as KB_DIR, RAW_FILES_PATH

INDEX_FILE = os.path.join(KB_DIR, "library_index.json")
KNOWLEDGE_MAP_FILE = os.path.join(KB_DIR, "knowledge_map.json")
CACHE_DIR = os.path.join(KB_DIR, "text_cache")

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx"}
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
MAX_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB


def _load_json(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


@router.get("")
async def list_library(_: str = Depends(verify_token)):
    index = _load_json(INDEX_FILE)
    result = []
    for doc_id, meta in index.items():
        path = meta.get("path", "")
        size = 0
        if os.path.exists(path):
            size = os.path.getsize(path)
        result.append({
            "id": doc_id,
            "title": meta.get("title", ""),
            "category": meta.get("category", ""),
            "path": path,
            "size_bytes": size,
        })
    return result


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    _: str = Depends(verify_token),
):
    # Validate extension
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File type not allowed. Use: {', '.join(ALLOWED_EXTENSIONS)}")

    # Validate MIME type
    if file.content_type and file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=400, detail="Invalid MIME type")

    # Read and size-check
    content = await file.read()
    if len(content) > MAX_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 500MB)")

    # Resolve filename collision
    base_name = os.path.splitext(file.filename or "upload")[0]
    dest_filename = f"{base_name}{ext}"
    dest_path = os.path.join(RAW_FILES_PATH, dest_filename)
    if os.path.exists(dest_path):
        dest_filename = f"{base_name}_{int(time.time())}{ext}"
        dest_path = os.path.join(RAW_FILES_PATH, dest_filename)

    os.makedirs(RAW_FILES_PATH, exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(content)

    # Index the document
    try:
        from services.library_service import index_document
        index = _load_json(INDEX_FILE)
        doc_id = f"doc_{len(index)}"
        # Ensure unique doc_id
        while doc_id in index:
            doc_id = f"doc_{len(index) + int(time.time()) % 1000}"

        category = await index_document(dest_path, doc_id, dest_filename)
        index[doc_id] = {
            "id": doc_id,
            "title": dest_filename,
            "path": dest_path,
            "category": category or "General",
        }
        _save_json(INDEX_FILE, index)
    except Exception as e:
        # Clean up uploaded file if indexing fails
        if os.path.exists(dest_path):
            os.remove(dest_path)
        raise HTTPException(status_code=500, detail=f"Indexing failed: {str(e)}")

    return {"id": doc_id, "title": dest_filename, "category": category or "General"}


@router.post("/reindex")
async def reindex_library(_: str = Depends(verify_token)):
    from services.library_service import sync_library
    try:
        await sync_library()
        index = _load_json(INDEX_FILE)
        return {"status": "ok", "indexed": len(index)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reindex failed: {str(e)}")


@router.delete("/{doc_id}")
async def delete_document(doc_id: str, _: str = Depends(verify_token)):
    index = _load_json(INDEX_FILE)
    if doc_id not in index:
        raise HTTPException(status_code=404, detail="Document not found")

    meta = index[doc_id]

    # 1. Remove source file
    file_path = meta.get("path", "")
    if file_path and os.path.exists(file_path):
        os.remove(file_path)

    # 2. Remove from index
    del index[doc_id]
    _save_json(INDEX_FILE, index)

    # 3. Remove all knowledge_map entries for this doc
    k_map = _load_json(KNOWLEDGE_MAP_FILE)
    updated_map = {}
    for word, entries in k_map.items():
        filtered = [e for e in entries if e.get("b") != doc_id]
        if filtered:
            updated_map[word] = filtered
    _save_json(KNOWLEDGE_MAP_FILE, updated_map)

    # 4. Remove text cache
    cache_path = os.path.join(CACHE_DIR, f"{doc_id}.json")
    if os.path.exists(cache_path):
        os.remove(cache_path)

    return {"deleted": True}
