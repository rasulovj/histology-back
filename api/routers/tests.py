import os
import time
from datetime import datetime
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api.auth import verify_token
from config import BASE_DIR
from services.control_test_service import (
    ControlTestParseError,
    deserialize_correct_indices,
    deserialize_options,
    parse_control_test_text,
    serialize_correct_indices,
    serialize_options,
)
from services.user_service import DB_PATH

router = APIRouter()

TEST_IMAGES_DIR = os.path.join(BASE_DIR, "data", "test_images")
MAX_TEST_TXT_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB


class TestUpdateBody(BaseModel):
    title: Optional[str] = None
    is_active: Optional[bool] = None


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _stem_filename(filename: Optional[str]) -> str:
    base = os.path.basename(filename or "test.txt")
    stem, _ = os.path.splitext(base)
    return stem.strip() or "test"


@router.get("")
async def list_tests(_: str = Depends(verify_token)):
    query = """
        SELECT t.id, t.title, t.source_filename, t.is_active, t.created_by, t.created_at, t.updated_at,
               COUNT(q.id) as question_count
        FROM tests t
        LEFT JOIN test_questions q ON q.test_id = t.id
        GROUP BY t.id
        ORDER BY t.created_at DESC, t.id DESC
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(query) as cur:
            rows = await cur.fetchall()

    return [
        {
            "id": row[0],
            "title": row[1],
            "source_filename": row[2],
            "is_active": bool(row[3]),
            "created_by": row[4],
            "created_at": row[5],
            "updated_at": row[6],
            "question_count": row[7],
        }
        for row in rows
    ]


@router.post("/upload")
async def upload_test_txt(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    activate: bool = Form(False),
    _: str = Depends(verify_token),
):
    filename = file.filename or ""
    if not filename.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="Only .txt files are allowed")

    content = await file.read()
    if len(content) > MAX_TEST_TXT_SIZE:
        raise HTTPException(status_code=413, detail="Test file is too large (max 5MB)")

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded text")

    try:
        questions = parse_control_test_text(text)
    except ControlTestParseError as e:
        raise HTTPException(status_code=400, detail=str(e))

    test_title = (title or "").strip() or _stem_filename(filename)
    now = _now_iso()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        cur = await db.execute(
            """
            INSERT INTO tests (title, source_filename, is_active, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (test_title, filename, 1 if activate else 0, "admin_panel", now, now),
        )
        test_id = cur.lastrowid

        for idx, question in enumerate(questions):
            question_type = "open" if question.get("accepted_answers") else "choice"
            stored_options = question.get("accepted_answers") or question["options"]
            stored_correct_indices = [] if question_type == "open" else question["correct_indices"]
            await db.execute(
                """
                INSERT INTO test_questions (
                    test_id, position, question, options_json, correct_indices_json, image_path, created_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    test_id,
                    idx,
                    question["question"],
                    serialize_options(stored_options),
                    serialize_correct_indices(stored_correct_indices),
                    now,
                ),
            )

        await db.commit()

    return {
        "id": test_id,
        "title": test_title,
        "source_filename": filename,
        "is_active": bool(activate),
        "question_count": len(questions),
    }


@router.patch("/{test_id}")
async def update_test(
    test_id: int,
    body: TestUpdateBody,
    _: str = Depends(verify_token),
):
    if body.title is None and body.is_active is None:
        raise HTTPException(status_code=422, detail="Nothing to update")

    now = _now_iso()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")

        async with db.execute("SELECT id FROM tests WHERE id = ?", (test_id,)) as cur:
            if not await cur.fetchone():
                raise HTTPException(status_code=404, detail="Test not found")

        if body.is_active is True:
            await db.execute(
                "UPDATE tests SET is_active = 1, updated_at = ? WHERE id = ?",
                (now, test_id),
            )
        elif body.is_active is False:
            await db.execute(
                "UPDATE tests SET is_active = 0, updated_at = ? WHERE id = ?",
                (now, test_id),
            )

        if body.title is not None:
            cleaned = body.title.strip()
            if not cleaned:
                raise HTTPException(status_code=400, detail="Title cannot be empty")
            await db.execute(
                "UPDATE tests SET title = ?, updated_at = ? WHERE id = ?",
                (cleaned, now, test_id),
            )

        await db.commit()

        async with db.execute(
            "SELECT id, title, source_filename, is_active, created_by, created_at, updated_at FROM tests WHERE id = ?",
            (test_id,),
        ) as cur:
            row = await cur.fetchone()

    return {
        "id": row[0],
        "title": row[1],
        "source_filename": row[2],
        "is_active": bool(row[3]),
        "created_by": row[4],
        "created_at": row[5],
        "updated_at": row[6],
    }


@router.get("/{test_id}/questions")
async def get_test_questions(test_id: int, _: str = Depends(verify_token)):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM tests WHERE id = ?", (test_id,)) as cur:
            if not await cur.fetchone():
                raise HTTPException(status_code=404, detail="Test not found")

        async with db.execute(
            """
            SELECT id, position, question, options_json, correct_indices_json, image_path
            FROM test_questions
            WHERE test_id = ?
            ORDER BY position ASC
            """,
            (test_id,),
        ) as cur:
            rows = await cur.fetchall()

    return [
        {
            "id": row[0],
            "position": row[1],
            "question": row[2],
            "question_type": "open" if deserialize_options(row[3]) and not deserialize_correct_indices(row[4]) else "choice",
            "accepted_answers": deserialize_options(row[3]) if (deserialize_options(row[3]) and not deserialize_correct_indices(row[4])) else [],
            "options": [] if (deserialize_options(row[3]) and not deserialize_correct_indices(row[4])) else deserialize_options(row[3]),
            "correct_indices": [] if (deserialize_options(row[3]) and not deserialize_correct_indices(row[4])) else deserialize_correct_indices(row[4]),
            "image_url": f"/tests/questions/{row[0]}/image" if row[5] else None,
            "has_image": bool(row[5]),
        }
        for row in rows
    ]


@router.post("/{test_id}/questions/{question_id}/image")
async def upload_question_image(
    test_id: int,
    question_id: int,
    file: UploadFile = File(...),
    _: str = Depends(verify_token),
):
    if file.content_type and not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are allowed")

    content = await file.read()
    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=413, detail="Image is too large (max 10MB)")

    ext = os.path.splitext(file.filename or "")[1].lower()
    if not ext:
        ext = ".jpg"

    os.makedirs(TEST_IMAGES_DIR, exist_ok=True)
    filename = f"test_{test_id}_q_{question_id}_{int(time.time())}{ext}"
    full_path = os.path.join(TEST_IMAGES_DIR, filename)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT image_path
            FROM test_questions
            WHERE id = ? AND test_id = ?
            """,
            (question_id, test_id),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Question not found for this test")
            old_path = row[0]

        with open(full_path, "wb") as f:
            f.write(content)

        await db.execute(
            "UPDATE test_questions SET image_path = ? WHERE id = ? AND test_id = ?",
            (full_path, question_id, test_id),
        )
        await db.commit()

    if old_path and os.path.exists(old_path):
        try:
            os.remove(old_path)
        except OSError:
            pass

    return {"question_id": question_id, "image_url": f"/tests/questions/{question_id}/image"}


@router.get("/questions/{question_id}/image")
async def get_question_image(question_id: int, _: str = Depends(verify_token)):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT image_path FROM test_questions WHERE id = ?",
            (question_id,),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Question not found")
            image_path = row[0]

    if not image_path or not os.path.exists(image_path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(image_path)


@router.delete("/{test_id}/questions/{question_id}/image")
async def remove_question_image(
    test_id: int,
    question_id: int,
    _: str = Depends(verify_token),
):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT image_path
            FROM test_questions
            WHERE id = ? AND test_id = ?
            """,
            (question_id, test_id),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Question not found for this test")
            image_path = row[0]

        if image_path:
            await db.execute(
                "UPDATE test_questions SET image_path = NULL WHERE id = ? AND test_id = ?",
                (question_id, test_id),
            )
            await db.commit()

    deleted = False
    if image_path:
        deleted = True
        if os.path.exists(image_path):
            try:
                os.remove(image_path)
            except OSError:
                pass
    return {"question_id": question_id, "deleted": deleted}


@router.delete("/{test_id}")
async def delete_test(test_id: int, _: str = Depends(verify_token)):
    image_paths: list[str] = []
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")

        async with db.execute("SELECT id FROM tests WHERE id = ?", (test_id,)) as cur:
            if not await cur.fetchone():
                raise HTTPException(status_code=404, detail="Test not found")

        async with db.execute(
            "SELECT image_path FROM test_questions WHERE test_id = ? AND image_path IS NOT NULL",
            (test_id,),
        ) as cur:
            image_paths = [row[0] for row in await cur.fetchall()]

        await db.execute("DELETE FROM tests WHERE id = ?", (test_id,))
        await db.commit()

    for path in image_paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

    return {"deleted": True}
