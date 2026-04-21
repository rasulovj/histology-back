import json
import os
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from api.auth import verify_token
from services.ai_service import translate_name_multilang
from services.preparations_service import PREPARATIONS_DIR, INDEX_FILE, CATEGORIES_FILE

router = APIRouter()

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


def _save_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _default_category(value: Optional[str]) -> str:
    cleaned = (value or "").strip()
    return cleaned or "Разное"


def _default_i18n_name(value: str) -> dict:
    fallback = (value or "").strip()
    return {"ru": fallback, "en": fallback, "uz": fallback}


def _normalize_i18n_name(data: Any, fallback: str) -> dict:
    if isinstance(data, dict):
        ru = str(data.get("ru", fallback)).strip() or fallback
        en = str(data.get("en", fallback)).strip() or fallback
        uz = str(data.get("uz", fallback)).strip() or fallback
        return {"ru": ru, "en": en, "uz": uz}
    return _default_i18n_name(fallback)


def _load_categories() -> dict:
    raw = _load_json(CATEGORIES_FILE)
    if isinstance(raw, dict):
        values = raw.get("categories", {})
        if isinstance(values, dict):
            result = {}
            for key, value in values.items():
                canonical = _default_category(key)
                result[canonical] = _normalize_i18n_name(value, canonical)
            return result
        if isinstance(values, list):
            # Legacy format: {"categories": ["Category A", "Category B"]}
            return {
                _default_category(item): _default_i18n_name(_default_category(item))
                for item in values
            }
    elif isinstance(raw, list):
        # Legacy format: list of names.
        return {_default_category(item): _default_i18n_name(_default_category(item)) for item in raw}
    return {}


def _save_categories(categories: dict) -> None:
    _save_json(CATEGORIES_FILE, {"categories": categories})


async def _ensure_category_exists(category: str) -> dict:
    categories = _load_categories()
    if category in categories:
        current = _normalize_i18n_name(categories[category], category)
        # If canonical admin-entered name is missing from all localized variants,
        # refresh translations and preserve original wording in detected source language.
        values = {str(current.get("ru", "")).strip(), str(current.get("en", "")).strip(), str(current.get("uz", "")).strip()}
        if category not in values:
            refreshed = await translate_name_multilang(category)
            normalized = _normalize_i18n_name(refreshed, category)
            categories[category] = normalized
            _save_categories(categories)
            return normalized
        return current
    i18n_name = await translate_name_multilang(category)
    normalized = _normalize_i18n_name(i18n_name, category)
    categories[category] = normalized
    _save_categories(categories)
    return normalized


class CategoryCreateBody(BaseModel):
    name: str


@router.get("")
async def list_preparations(_: str = Depends(verify_token)):
    os.makedirs(PREPARATIONS_DIR, exist_ok=True)
    index = _load_json(INDEX_FILE)
    result = []
    for prep_id, meta in index.items():
        path = meta.get("path", "")
        size = os.path.getsize(path) if path and os.path.exists(path) else 0
        category = _default_category(meta.get("category"))
        title = str(meta.get("title", "") or "")
        category_i18n = _normalize_i18n_name(meta.get("category_i18n"), category)
        title_i18n = _normalize_i18n_name(meta.get("title_i18n"), title)
        result.append({
            "id": prep_id,
            "title": title,
            "title_i18n": title_i18n,
            "category": category,
            "category_i18n": category_i18n,
            "path": path,
            "size_bytes": size,
            "created_at": meta.get("created_at"),
        })
    result.sort(key=lambda x: (x.get("category", ""), x.get("title", "").lower()))
    return result


@router.get("/categories")
async def list_preparation_categories(_: str = Depends(verify_token)):
    os.makedirs(PREPARATIONS_DIR, exist_ok=True)
    index = _load_json(INDEX_FILE)
    counts = {}
    from_index_i18n = {}
    for meta in index.values():
        path = meta.get("path", "")
        if path and os.path.exists(path):
            category = _default_category(meta.get("category"))
            counts[category] = counts.get(category, 0) + 1
            from_index_i18n[category] = _normalize_i18n_name(meta.get("category_i18n"), category)

    stored_categories = _load_categories()
    categories = set(stored_categories.keys()) | set(counts.keys())
    sorted_categories = sorted(categories, key=lambda c: c.lower())
    return [
        {
            "name": name,
            "name_i18n": _normalize_i18n_name(stored_categories.get(name) or from_index_i18n.get(name), name),
            "count": counts.get(name, 0),
        }
        for name in sorted_categories
    ]


@router.post("/categories")
async def create_preparation_category(body: CategoryCreateBody, _: str = Depends(verify_token)):
    os.makedirs(PREPARATIONS_DIR, exist_ok=True)
    category = _default_category(body.name)
    i18n_name = await _ensure_category_exists(category)
    return {"name": category, "name_i18n": i18n_name}


@router.delete("/categories/{category_name}")
async def delete_preparation_category(
    category_name: str,
    cascade: bool = False,
    _: str = Depends(verify_token),
):
    os.makedirs(PREPARATIONS_DIR, exist_ok=True)
    category = _default_category(category_name)

    index = _load_json(INDEX_FILE)
    related = []
    for prep_id, meta in index.items():
        item_category = _default_category(meta.get("category"))
        if item_category == category:
            related.append((prep_id, meta))

    if related and not cascade:
        raise HTTPException(
            status_code=409,
            detail=f"Category '{category}' is not empty. Pass cascade=true to delete with files.",
        )

    deleted_files = 0
    if related:
        for prep_id, meta in related:
            file_path = meta.get("path", "")
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    deleted_files += 1
                except Exception:
                    # File cleanup errors should not block index cleanup.
                    pass
            if prep_id in index:
                del index[prep_id]
        _save_json(INDEX_FILE, index)

    categories = _load_categories()
    had_category = category in categories
    if had_category:
        del categories[category]
        _save_categories(categories)

    if not had_category and not related:
        raise HTTPException(status_code=404, detail="Category not found")

    return {
        "deleted": True,
        "category": category,
        "deleted_preparations": len(related),
        "deleted_files": deleted_files,
    }


@router.post("/upload")
async def upload_preparation(
    file: UploadFile = File(...),
    category: str = Form(""),
    _: str = Depends(verify_token),
):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed. Use: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    if file.content_type and file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=400, detail="Invalid MIME type")

    content = await file.read()
    if len(content) > MAX_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 500MB)")

    os.makedirs(PREPARATIONS_DIR, exist_ok=True)

    base_name = os.path.splitext(file.filename or "preparation")[0]
    dest_filename = f"{base_name}{ext}"
    dest_path = os.path.join(PREPARATIONS_DIR, dest_filename)
    if os.path.exists(dest_path):
        dest_filename = f"{base_name}_{int(time.time())}{ext}"
        dest_path = os.path.join(PREPARATIONS_DIR, dest_filename)

    with open(dest_path, "wb") as f:
        f.write(content)

    index = _load_json(INDEX_FILE)
    prep_id = f"prep_{uuid.uuid4().hex[:8]}"
    while prep_id in index:
        prep_id = f"prep_{uuid.uuid4().hex[:8]}"

    prep_category = _default_category(category)
    prep_category_i18n = await _ensure_category_exists(prep_category)
    title_base = os.path.splitext(dest_filename)[0].replace("_", " ").strip() or dest_filename
    title_i18n_source = await translate_name_multilang(title_base)
    title_i18n = {}
    for lang in ("ru", "en", "uz"):
        variant = str(title_i18n_source.get(lang, title_base)).strip() or title_base
        title_i18n[lang] = f"{variant}{ext}" if not variant.lower().endswith(ext) else variant

    index[prep_id] = {
        "id": prep_id,
        "title": dest_filename,
        "title_i18n": title_i18n,
        "path": dest_path,
        "category": prep_category,
        "category_i18n": prep_category_i18n,
        "created_at": int(time.time()),
    }
    _save_json(INDEX_FILE, index)

    return {"id": prep_id, "title": dest_filename, "category": prep_category}


@router.delete("/{prep_id}")
async def delete_preparation(prep_id: str, _: str = Depends(verify_token)):
    os.makedirs(PREPARATIONS_DIR, exist_ok=True)
    index = _load_json(INDEX_FILE)
    if prep_id not in index:
        raise HTTPException(status_code=404, detail="Preparation not found")

    meta = index[prep_id]
    file_path = meta.get("path", "")
    if file_path and os.path.exists(file_path):
        os.remove(file_path)

    del index[prep_id]
    _save_json(INDEX_FILE, index)
    return {"deleted": True}
