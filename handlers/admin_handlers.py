import os
import asyncio
from aiogram import Router, F, Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile
from aiogram.filters import Filter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import RAW_FILES_PATH
from services.rag_service import add_pdf_to_db
from services.library_service import sync_library
from services.user_service import get_admin_role, add_admin, get_all_feedback, get_all_users_for_broadcast
from services.ai_service import translate_broadcast_message

router = Router()

class AdminState(StatesGroup):
    waiting_for_new_admin = State()
    waiting_for_broadcast = State()

class IsAdmin(Filter):
    async def __call__(self, message: Message) -> bool:
        role = await get_admin_role(message.from_user.id)
        return bool(role)

class IsAdminForDocs(Filter):
    async def __call__(self, message: Message) -> bool:
        if not message.document:
            return False
        allowed_exts = ('.pdf', '.docx', '.pptx')
        if not message.document.file_name.lower().endswith(allowed_exts):
            return False 
        role = await get_admin_role(message.from_user.id)
        return bool(role)

def get_admin_keyboard(role):
    buttons = [
        [InlineKeyboardButton(text="📚 Индексация базы данных", callback_data="admin_index_db")],
        [InlineKeyboardButton(text="📥 Выгрузить отчет по отзывам", callback_data="admin_get_feedback")],
        [InlineKeyboardButton(text="📢 Рассылка пользователям", callback_data="admin_broadcast")]
    ]
    if role == "superadmin":
        buttons.append([InlineKeyboardButton(text="👑 Добавить администратора", callback_data="admin_add_admin")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@router.message(F.text.in_({"🛠 Админ панель", "/admin"}), IsAdmin())
async def admin_panel(message: Message):
    role = await get_admin_role(message.from_user.id)
    await message.answer(f"🔧 Панель управления\nВаша роль: {role.upper()}", reply_markup=get_admin_keyboard(role))

@router.callback_query(F.data == "admin_index_db")
async def process_indexing(callback: CallbackQuery):
    await callback.message.edit_text("⏳ Запущена синхронизация библиотеки и RAG-индекса...")
    await sync_library()
    await callback.message.edit_text("✅ Библиотека успешно обновлена и проиндексирована!")

@router.callback_query(F.data == "admin_add_admin")
async def add_admin_start(callback: CallbackQuery, state: FSMContext):
    role = await get_admin_role(callback.from_user.id)
    if role != "superadmin":
        return await callback.answer("❌ У вас нет прав!", show_alert=True)
        
    await callback.message.answer("Отправьте Telegram ID нового администратора:")
    await state.set_state(AdminState.waiting_for_new_admin)
    await callback.answer()

@router.message(AdminState.waiting_for_new_admin, IsAdmin())
async def save_new_admin(message: Message, state: FSMContext):
    new_id = message.text.strip()
    if not new_id.isdigit():
        return await message.answer("❌ ID должен содержать только цифры.")
        
    await add_admin(new_id, "department", "general")
    await message.answer(f"✅ Пользователь с ID {new_id} назначен Администратором!")
    await state.clear()

@router.callback_query(F.data == "admin_get_feedback")
async def export_feedback(callback: CallbackQuery):
    feedbacks = await get_all_feedback()
    if not feedbacks:
        return await callback.answer("📭 Отзывов пока нет.", show_alert=True)
        
    file_path = os.path.join("data", "feedback_report.txt")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("=== ОТЧЕТ ПО ОБРАТНОЙ СВЯЗИ ===\n\n")
        for fb in feedbacks:
            f.write(f"ID: {fb[0]} | Дата: {fb[3]}\n")
            f.write(f"Студент: {fb[4]} ({fb[5]})\n")
            f.write(f"Тип: {fb[1]}\n")
            f.write(f"Текст: {fb[2]}\n")
            f.write("-" * 40 + "\n")
            
    await callback.message.answer_document(FSInputFile(file_path), caption="📊 Выгрузка обратной связи")
    os.remove(file_path)
    await callback.answer()

@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("📢 Отправьте текст для рассылки (на русском языке).\nИИ автоматически переведет его на английский и узбекский языки, после чего разошлет пользователям в соответствии с их настройками.")
    await state.set_state(AdminState.waiting_for_broadcast)
    await callback.answer()
    
@router.message(AdminState.waiting_for_broadcast, IsAdmin())
async def admin_broadcast_send(message: Message, state: FSMContext, bot: Bot):
    text_ru = message.text
    status_msg = await message.answer("⏳ Перевожу текст на другие языки (EN, UZ)...")
    
    translations = await translate_broadcast_message(text_ru)
    users = await get_all_users_for_broadcast()
    
    await status_msg.edit_text(f"🚀 Начинаю рассылку для {len(users)} пользователей...")
    
    success = 0
    for u in users:
        lang = u.get("lang", "ru")
        text_to_send = translations.get(lang, text_ru)
        try:
            await bot.send_message(chat_id=u["user_id"], text=text_to_send)
            success += 1
        except Exception:
            pass
        await asyncio.sleep(0.05) # Защита от лимитов Telegram
        
    await message.answer(f"✅ Рассылка завершена! Успешно доставлено: {success}/{len(users)}")
    await state.clear()

@router.message(F.document, IsAdminForDocs())
async def handle_admin_docs(message: Message, bot: Bot):
    role = await get_admin_role(message.from_user.id)
    file_name = message.document.file_name
    file_size = message.document.file_size
    
    max_size_bytes = 20 * 1024 * 1024
    if file_size and file_size > max_size_bytes:
        size_mb = file_size / (1024 * 1024)
        return await message.answer(f"❌ Файл слишком большой!\nВаш файл весит: ~{size_mb:.1f} МБ.\nTelegram ограничивает ботов (до 20 МБ).")
    
    status_msg = await message.answer(f"📥 Скачиваю материал: {file_name}...")
    file_path = os.path.join(RAW_FILES_PATH, file_name)
    
    try:
        file = await bot.get_file(message.document.file_id)
        await bot.download_file(file.file_path, file_path)
    except Exception as e:
        return await status_msg.edit_text(f"❌ Ошибка скачивания: {e}")
        
    await status_msg.edit_text("⏳ Обрабатываю документ и добавляю в базу ИИ...")
    success = add_pdf_to_db(file_path, file_name)
    
    if success:
        await status_msg.edit_text(f"✅ Документ {file_name} успешно добавлен в базу знаний ИИ!")
    else:
        await status_msg.edit_text(f"❌ Ошибка добавления {file_name} в базу.")