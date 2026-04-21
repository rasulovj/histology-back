import asyncio
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from api.auth import verify_token

router = APIRouter()


class PreviewRequest(BaseModel):
    text: str


class SendRequest(BaseModel):
    ru: str
    en: str
    uz: str


@router.post("/preview")
async def preview_broadcast(body: PreviewRequest, _: str = Depends(verify_token)):
    from services.ai_service import translate_broadcast_message
    result = await translate_broadcast_message(body.text)
    if not result:
        raise HTTPException(status_code=500, detail="Translation failed")
    return result


@router.post("/send")
async def send_broadcast(body: SendRequest, request: Request, _: str = Depends(verify_token)):
    from services.user_service import get_all_users_for_broadcast

    bot = getattr(request.app.state, "bot", None)
    if bot is None:
        # Fallback: create a temporary bot instance
        import os
        from aiogram import Bot
        bot = Bot(token=os.getenv("BOT_TOKEN"))
        should_close = True
    else:
        should_close = False

    translations = {"ru": body.ru, "en": body.en, "uz": body.uz}
    users = await get_all_users_for_broadcast()

    sent = 0
    failed = 0
    for user in users:
        lang = user.get("lang", "ru")
        text = translations.get(lang, body.ru)
        try:
            await bot.send_message(chat_id=user["user_id"], text=text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    if should_close:
        await bot.session.close()

    return {"sent": sent, "failed": failed}
