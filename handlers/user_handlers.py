import os
import asyncio
import hashlib
import random
import re
from typing import Any, Optional, Union
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import (Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ReplyKeyboardRemove, FSInputFile, InputMediaPhoto, LabeledPrice, PreCheckoutQuery, SuccessfulPayment)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from duckduckgo_search import DDGS
from config import ADMIN_ID, PAYMENT_TOKEN

from services.pdf_service import generate_theory_pdf
from services.rag_service import search_knowledge_base
from services.ai_service import get_collaborative_response, get_chat_response, is_medical_topic, check_open_answer
from services.image_gen_service import generate_image_async
from services.usecases.quiz_uc import QuizUseCase
from services.quiz_service import get_test_as_text, parse_test_txt_file, create_test_txt_file, clean_and_format_questions, enrich_questions_with_explanations, filter_questions_by_answer_rule
from services.library_service import sync_library, get_library_catalog, KB_DIR
from services.preparations_service import get_preparations_catalog
from services.user_service import get_user_profile, save_user_profile, get_user_course, get_bot_statistics, get_admin_statistics, update_user_activity, get_user_lang, update_user_lang, is_user_registered, set_user_premium, get_user_premium_status, update_last_topic, get_last_topic, save_feedback, check_and_increment_requests, save_pending_payment, get_pending_payment, delete_pending_payment, record_payment, list_active_control_tests, get_control_test_by_id, has_active_narozat_access
from services.sofpay_service import create_payment, check_payment
from services.ktp_service import get_topics_for_faculty, get_topic_label
from services.localization_service import t

router = Router()

def clean_text_output(text):
    if not text:
        return "..."
    text = text.replace("*", "").replace("_", "").replace("#", "").replace("`", "")
    text = re.sub(r'"([^"]{1,50})"', r'\1', text)
    text = re.sub(r'^\s*[\-•]\s+', '🔹 ', text, flags=re.MULTILINE)
    text = re.sub(r'(?m)^(📌\s*\d\.\s+)', r'\n\1', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()

def _normalize_open_answer_text(value: str) -> str:
    if not value:
        return ""
    text = value.strip().lower()
    text = re.sub(r'^[a-fа-ф][\)\.\:\-\s]+', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip(" .,:;!?\"'()[]{}")

def _rewrite_question_for_open_mode(question: str, lang: str) -> str:
    if not question:
        return ""

    text = re.sub(r'\s+', ' ', question).strip()
    base = text.rstrip(" ?.!")
    low = base.lower()

    def _sentence_case(value: str) -> str:
        value = value.strip()
        if not value:
            return value
        return value[0].upper() + value[1:]

    def _uz_open_predicate(value: str) -> str:
        value = value.strip()
        replacements = (
            (" kiradi", " kiradigan"),
            (" bo'ladi", " bo'ladigan"),
            (" hisoblanadi", " hisoblangan"),
        )
        for old, new in replacements:
            if value.endswith(old):
                return value[: -len(old)] + new
        return value

    # EN: convert closed-form multiple-choice wording into self-contained open wording.
    if low.startswith("which of the following are "):
        rest = base[len("which of the following are "):]
        return f"Write one correct answer that is {rest}."
    if low.startswith("which of the following is "):
        rest = base[len("which of the following is "):]
        return f"Write the correct answer that is {rest}."
    if low.startswith("which of the following "):
        rest = base[len("which of the following "):]
        return f"Write one correct answer for: {rest}."

    # RU
    if low.startswith("какие из перечисленных "):
        rest = base[len("какие из перечисленных "):]
        return f"Напишите один правильный ответ: {rest}."
    if low.startswith("что из перечисленного "):
        rest = base[len("что из перечисленного "):]
        return f"Напишите правильный ответ: {rest}."

    # UZ
    if low.startswith("quyidagilardan qaysilari "):
        rest = base[len("quyidagilardan qaysilari "):]
        rest = _uz_open_predicate(rest)
        return f"{_sentence_case(rest)} bitta to'g'ri javobini yozing."
    if low.startswith("quyidagilardan qaysi biri "):
        rest = base[len("quyidagilardan qaysi biri "):]
        rest = _uz_open_predicate(rest)
        return f"{_sentence_case(rest)} to'g'ri javobni yozing."

    uz_match = re.match(r"^qaysi\s+(\S+)\s+(.+?)\s+hisoblanadi$", low, flags=re.IGNORECASE)
    if uz_match:
        subject = base[len("qaysi "):]
        parts = re.match(r"^(\S+)\s+(.+?)\s+hisoblanadi$", subject, flags=re.IGNORECASE)
        if parts:
            noun, rest = parts.groups()
            return _sentence_case(f"{rest} hisoblangan {noun}ni yozing.")

    return text

def _is_premium_status_active(value) -> bool:
    try:
        return int(value or 0) > 0
    except (TypeError, ValueError):
        return str(value).strip().lower() in {"true", "yes", "premium", "active"}

async def send_safe_message(message: Message, text: str, reply_markup=None):
    text = clean_text_output(text)
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for i, chunk in enumerate(chunks):
        try:
            if i == len(chunks) - 1:
                await message.answer(chunk, reply_markup=reply_markup)
            else:
                await message.answer(chunk)
        except Exception:
            pass
        await asyncio.sleep(0.3)

async def send_test_as_messages(message: Message, test_text: str):
    for block in test_text.split('\n\n'):
        if block.strip():
            await message.answer(clean_text_output(block.strip()))
            await asyncio.sleep(0.3)

def _build_image_search_query(topic: str, lang: str) -> str:
    topic = (topic or "").strip()
    if lang == "uz":
        return f"{topic} o'zbekcha gistologiya diagramma"
    if lang == "ru":
        return f"{topic} гистология схема"
    return f"{topic} anatomy biology diagram"

def _build_image_region(lang: str) -> str:
    if lang == "uz":
        return "uz-uz"
    if lang == "ru":
        return "ru-ru"
    return "wt-wt"

def _contains_uzbek_signal(text: str) -> bool:
    value = (text or "").lower()
    if not value:
        return False
    uzbek_markers = (
        "o'zbek", "ozbek", "uzbek", "tilida", "dars", "mavzu", "to'qima", "toqima",
        "hujayra", "qavat", "yadro", "gistolog", "biriktiruvchi", "epiteliy",
        "tog'ay", "suyak", "muskul", "qon tomir", "bez", "ko'p", "bolim",
    )
    if any(marker in value for marker in uzbek_markers):
        return True
    return any(ch in value for ch in ("o'", "g'", "ʻ", "ʼ", "‘"))

def _is_image_result_allowed(result: dict, lang: str) -> bool:
    if lang != "uz":
        return True
    combined = " ".join(
        str(result.get(key, ""))
        for key in ("title", "source", "url", "thumbnail", "image")
    )
    return _contains_uzbek_signal(combined)

def _ddg_images_sync(topic, count, lang):
    try:
        with DDGS() as ddgs:
            query = _build_image_search_query(topic, lang)
            region = _build_image_region(lang)
            raw_results = list(ddgs.images(query, region=region, max_results=max(count * 5, count)))
            filtered = [item for item in raw_results if _is_image_result_allowed(item, lang)]
            return filtered[:count]
    except Exception:
        return []

async def find_study_images(topic, lang="en", count=2):
    results = await asyncio.to_thread(_ddg_images_sync, topic, count, lang)
    return [r['image'] for r in results]

def shuffle_question_options(question: dict) -> dict:
    options = list(question.get("options", []))
    correct_indices = set(question.get("correct_indices", []))
    paired = [
        {"option": option, "is_correct": idx in correct_indices}
        for idx, option in enumerate(options)
    ]
    random.shuffle(paired)
    question["options"] = [item["option"] for item in paired]
    question["correct_indices"] = [idx for idx, item in enumerate(paired) if item["is_correct"]]
    return question

def prepare_control_test_questions(questions: list[dict]) -> list[dict]:
    prepared_questions = []
    for raw_question in questions:
        question = dict(raw_question)
        if question.get("question_type") != "open" and question.get("options"):
            shuffle_question_options(question)
        prepared_questions.append(question)
    return prepared_questions


def _pick_random_questions(questions: list[dict], count: int) -> list[dict]:
    if count <= 0 or not questions:
        return []
    if len(questions) <= count:
        return list(questions)
    return random.sample(questions, k=count)


def select_control_test_questions(
    questions: list[dict],
    multi_answer_count: int = 5,
    single_answer_count: int = 3,
    open_question_count: int = 2,
) -> list[dict]:
    if not questions:
        return []

    multi_answer_questions: list[dict] = []
    single_answer_questions: list[dict] = []
    open_questions: list[dict] = []

    for question in questions:
        if question.get("question_type") == "open":
            open_questions.append(question)
            continue
        if len(question.get("correct_indices", [])) > 1:
            multi_answer_questions.append(question)
            continue
        single_answer_questions.append(question)

    selected: list[dict] = []
    selected.extend(_pick_random_questions(multi_answer_questions, multi_answer_count))
    selected.extend(_pick_random_questions(single_answer_questions, single_answer_count))
    selected.extend(_pick_random_questions(open_questions, open_question_count))

    target_total = multi_answer_count + single_answer_count + open_question_count
    if len(selected) < target_total:
        selected_ids = {id(item) for item in selected}
        remaining_pool = [question for question in questions if id(question) not in selected_ids]
        selected.extend(_pick_random_questions(remaining_pool, target_total - len(selected)))

    random.shuffle(selected)
    return selected

def is_multi_answer_question(question: dict) -> bool:
    return len(question.get("correct_indices", [])) > 1

def get_multi_answer_count(question: dict) -> int:
    return len(question.get("correct_indices", []))

def build_answer_keyboard(question: dict, selected_indices: set[int], lang: str) -> InlineKeyboardMarkup:
    def option_letter(i: int) -> str:
        return chr(ord("A") + i) if 0 <= i < 26 else str(i + 1)

    letter_buttons = []
    for i in range(len(question["options"])):
        label = option_letter(i)
        if i in selected_indices:
            label = f"✅ {label}"
        letter_buttons.append(InlineKeyboardButton(text=label, callback_data=f"ans_{i}"))

    kb_rows = [letter_buttons]
    if is_multi_answer_question(question):
        kb_rows.append([InlineKeyboardButton(text=t("submit_answer", lang), callback_data="ans_submit")])
    return InlineKeyboardMarkup(inline_keyboard=kb_rows)

def calculate_grade(percent):
    if percent >= 86: return "5 (Excellent)", "🏆"
    if percent >= 71: return "4 (Good)", "✅"
    if percent >= 56: return "3 (Satisfactory)", "⚠️"
    return "2 (Fail)", "❌"

def get_grade_key(percent: int) -> str:
    if percent >= 86:
        return "grade_excellent"
    if percent >= 71:
        return "grade_good"
    if percent >= 56:
        return "grade_satisfactory"
    return "grade_fail"

def _sanitize_filename_part(value: str, fallback: str = "") -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    text = re.sub(r'[\\/:*?"<>|]+', "", text)
    text = text.replace("'", "").replace("`", "")
    text = text.replace(".", "")
    text = text[:60].strip()
    text = re.sub(r"\s+", "_", text)
    return text or fallback

def build_quiz_export_filename(data: dict, lang: str) -> str:
    prefix = _sanitize_filename_part(t("results_file_prefix", lang), "quiz_results")
    source_name = data.get("topic") or data.get("export_source_name") or ""
    safe_source = _sanitize_filename_part(source_name)
    if safe_source:
        return f"{prefix}_{safe_source}.txt"
    return f"{prefix}.txt"

def should_send_quiz_export(data: dict) -> bool:
    if "export_results_file" in data:
        return bool(data.get("export_results_file"))
    return data.get("test_source") != "admin_test"

def build_pdf_export_filename(topic: str, lang: str, kind: str = "theory") -> str:
    prefix_key = "theory_file_prefix" if kind == "theory" else "answer_file_prefix"
    default_prefix = "theory" if kind == "theory" else "answer"
    prefix = _sanitize_filename_part(t(prefix_key, lang), default_prefix)
    safe_topic = _sanitize_filename_part(topic)
    if safe_topic:
        return f"{prefix}_{safe_topic}.pdf"
    return f"{prefix}.pdf"

def build_mixed_open_indices(total_questions):
    if total_questions <= 0:
        return []
    if total_questions == 1:
        return [0]

    ratio = random.uniform(0.30, 0.40)
    open_count = round(total_questions * ratio)
    open_count = max(1, min(total_questions - 1, open_count))
    return random.sample(range(total_questions), k=open_count)

def is_open_question(data, index):
    questions = data.get("questions", [])
    if 0 <= index < len(questions):
        if questions[index].get("question_type") == "open":
            return True
    open_indices = data.get("open_question_indices")
    if open_indices is None:
        return data.get("quiz_type") == "open"
    return index in set(open_indices)

def get_category_hash(cat_name):
    return hashlib.md5((cat_name or "Misc").encode()).hexdigest()[:8]


def _resolve_i18n_lang(lang: str) -> str:
    key = (lang or "en").lower()
    if key.startswith("ru"):
        return "ru"
    if key.startswith("uz"):
        return "uz"
    return "en"


def _pick_localized_name(raw_i18n: Any, fallback: str, lang: str) -> str:
    if isinstance(raw_i18n, dict):
        lang_key = _resolve_i18n_lang(lang)
        value = str(raw_i18n.get(lang_key, "")).strip()
        if value:
            return value
    return str(fallback or "").strip()

def get_main_keyboard(lang, user_id=None):
    keyboard = [
        [
            KeyboardButton(text=t("menu_theory", lang)),
            KeyboardButton(text=t("menu_quiz", lang))
        ],
        [
            KeyboardButton(text=t("menu_control_test", lang)),
            KeyboardButton(text=t("menu_file", lang))
        ],
        [
            KeyboardButton(text=t("menu_library", lang)),
            KeyboardButton(text=t("menu_preparations", lang))
        ],
        [
            KeyboardButton(text=t("menu_sub", lang))
        ],
        [
            KeyboardButton(text=t("menu_profile", lang))
        ],
        [
            KeyboardButton(text=t("feedback_btn", lang))
        ]
    ]
    if user_id and str(user_id) == str(ADMIN_ID):
        keyboard.append([KeyboardButton(text=t("menu_admin", lang))])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_registration_welcome_text() -> str:
    return (
        "👋 Добро пожаловать! / Welcome! / Xush kelibsiz!\n"
        "📝 Пожалуйста, зарегистрируйтесь. / Please register. / Iltimos, ro'yxatdan o'ting.\n"
        "🌍 Выберите язык / Select language / Tilni tanlang:"
    )

MENU_PROFILE_TEXTS = {"👤 Профиль", "👤 Profile", "👤 Profil", "👤 Мой профиль", "👤 Mening profilim"}
MENU_FEEDBACK_TEXTS = {"📩 Обратная связь", "📩 Feedback", "📩 Fikr bildirish"}
MENU_LIBRARY_TEXTS = {"📚 Библиотека", "📚 Library", "📚 Kutubxona", "📚 Материалы кафедры", "📚 Department Materials", "📚 Kafedra materiallari"}
MENU_PREPARATIONS_TEXTS = {"🧫 Препараты", "🧫 Preparations", "🧫 Preparatlar", "Preparatlar"}
MENU_THEORY_TEXTS = {"📖 Теория", "📖 Theory", "📖 Nazariya", "🔬 База Знаний", "🔬 Knowledge Base", "🔬 Bilimlar bazasi"}
MENU_AI_QUIZ_TEXTS = {"🧠 Тест (AI)", "🧠 Quiz (AI)", "🧠 Test (AI)", "🧠 AI-Экзаменатор", "🧠 AI Examiner", "🧠 AI-Imtihon"}
MENU_FILE_QUIZ_TEXTS = {
    "📁 Решить свой тест",
    "📁 Solve custom test",
    "📁 O'z testingizni yechish",
    "📁 Загрузить тест",
    "Загрузить тест",
    "📁 Upload Test",
    "📁 Test yuklash",
}
MENU_CONTROL_TEST_TEXTS = {"📝 Контрольный тест", "📝 Control Test", "📝 Nazorat testi", "Nazorat testi"}
MENU_PREMIUM_TEXTS = {"💎 Подписка", "💎 Subscription", "💎 Obuna"}
MAIN_MENU_INTERRUPT_TEXTS = (
    MENU_PROFILE_TEXTS
    | MENU_FEEDBACK_TEXTS
    | MENU_LIBRARY_TEXTS
    | MENU_PREPARATIONS_TEXTS
    | MENU_THEORY_TEXTS
    | MENU_AI_QUIZ_TEXTS
    | MENU_CONTROL_TEST_TEXTS
    | MENU_FILE_QUIZ_TEXTS
    | MENU_PREMIUM_TEXTS
)

async def handle_menu_interrupt(message: Message, state: FSMContext) -> bool:
    text = (message.text or "").strip()
    if text not in MAIN_MENU_INTERRUPT_TEXTS:
        return False

    await state.clear()

    if text in MENU_PROFILE_TEXTS:
        await profile_handler(message, state)
    elif text in MENU_FEEDBACK_TEXTS:
        await fb_reply_btn_handler(message, state)
    elif text in MENU_LIBRARY_TEXTS:
        await lib_start(message, state)
    elif text in MENU_PREPARATIONS_TEXTS:
        await preparations_start(message, state)
    elif text in MENU_THEORY_TEXTS:
        await theory_start(message, state)
    elif text in MENU_AI_QUIZ_TEXTS:
        await quiz_start(message, state)
    elif text in MENU_CONTROL_TEST_TEXTS:
        await control_test_start(message, state)
    elif text in MENU_FILE_QUIZ_TEXTS:
        await file_quiz_prompt(message, state)
    elif text in MENU_PREMIUM_TEXTS:
        await premium_menu(message)

    return True

def get_cancel_keyboard(lang):
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t("back", lang))]], resize_keyboard=True)

def get_lang_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru")], 
        [InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en")], 
        [InlineKeyboardButton(text="🇺🇿 O'zbek", callback_data="lang_uz")]
    ])

def get_course_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1", callback_data="set_course_1"), InlineKeyboardButton(text="2", callback_data="set_course_2")], 
        [InlineKeyboardButton(text="3", callback_data="set_course_3"), InlineKeyboardButton(text="4", callback_data="set_course_4")], 
        [InlineKeyboardButton(text="5", callback_data="set_course_5"), InlineKeyboardButton(text="6", callback_data="set_course_6")]
    ])

def get_faculty_keyboard(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("fac_lech", lang), callback_data="fac_lech"), InlineKeyboardButton(text=t("fac_ped", lang), callback_data="fac_ped")], 
        [InlineKeyboardButton(text=t("fac_stom", lang), callback_data="fac_stom"), InlineKeyboardButton(text=t("fac_medped", lang), callback_data="fac_medped")], 
        [InlineKeyboardButton(text=t("fac_medprof", lang), callback_data="fac_medprof"), InlineKeyboardButton(text=t("fac_farm", lang), callback_data="fac_farm")], 
        [InlineKeyboardButton(text=t("fac_nurse", lang), callback_data="fac_nurse"), InlineKeyboardButton(text=t("fac_mil", lang), callback_data="fac_mil")], 
        [InlineKeyboardButton(text=t("fac_inter", lang), callback_data="fac_inter"), InlineKeyboardButton(text=t("fac_postgrad", lang), callback_data="fac_postgrad")]
    ])

def get_feedback_keyboard(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("feedback_btn", lang), callback_data="feedback_menu")]
    ])


def get_control_tests_keyboard(tests: list[dict], lang: str) -> InlineKeyboardMarkup:
    rows = []
    for test in tests:
        title = str(test.get("title") or "Test").strip()
        question_count = test.get("question_count", 0)
        button_text = f"📝 {title} ({question_count})"
        rows.append([InlineKeyboardButton(text=button_text, callback_data=f"ctest_pick_{test['id']}")])

    rows.append([InlineKeyboardButton(text=t("back", lang), callback_data="ctest_back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_control_test_count_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="10", callback_data="ctest_count_10"),
                InlineKeyboardButton(text="25", callback_data="ctest_count_25"),
            ],
            [InlineKeyboardButton(text=t("back", lang), callback_data="ctest_back_main")],
        ]
    )

def select_random_control_test_questions(questions: list[dict], desired_count: int) -> list[dict]:
    if not questions or desired_count <= 0:
        return []
    if len(questions) <= desired_count:
        return list(questions)
    return random.sample(questions, k=desired_count)

class RegState(StatesGroup):
    lang = State()
    fio = State()
    course = State()
    year = State()
    faculty = State()

class TmaState(StatesGroup):
    waiting_for_theory = State()
    waiting_for_ktp_type = State()
    waiting_for_ai_quiz = State()
    waiting_for_quiz_type = State()
    waiting_for_quiz_count = State()
    waiting_for_file_quiz = State()
    in_quiz = State()
    in_open_quiz = State()

class FeedbackState(StatesGroup):
    waiting_for_text = State()

class EditProfileState(StatesGroup):
    choosing_field = State()
    editing_fio    = State()
    editing_course = State()
    editing_year   = State()
    editing_faculty = State()

def _premium_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("buy_premium_btn", lang), callback_data="buy_premium")]
    ])

def _narozat_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("buy_narozat_btn", lang), callback_data="buy_narozat")]
    ])

async def check_limit(message: Message, lang: str, user_id: Optional[Union[int, str]] = None) -> bool:
    """Returns True if user may proceed. Sends paywall message if not."""
    actor_id = user_id if user_id is not None else message.from_user.id

    premium_status = await get_user_premium_status(actor_id)
    if _is_premium_status_active(premium_status):
        return True

    allowed, remaining = await check_and_increment_requests(actor_id)
    if not allowed:
        # Double-check premium to avoid race conditions right after payment activation.
        if _is_premium_status_active(await get_user_premium_status(actor_id)):
            return True
        await message.answer(t("limit_reached", lang), reply_markup=_premium_kb(lang))
        return False
    if remaining > 0:
        await message.answer(t("requests_left", lang).format(n=remaining))
    return True

async def check_auth(message: Message, state: FSMContext):
    if not await is_user_registered(message.from_user.id):
        await message.answer(get_registration_welcome_text(), reply_markup=get_lang_keyboard())
        await state.set_state(RegState.lang)
        return False
    return True

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    if not await is_user_registered(message.from_user.id):
        return await check_auth(message, state)
        
    profile = await get_user_profile(message.from_user.id)
    await message.answer(t("welcome", profile.get("lang", "ru")), reply_markup=get_main_keyboard(profile.get("lang", "ru"), message.from_user.id))

@router.callback_query(RegState.lang, F.data.startswith("lang_"))
async def set_lang(callback: CallbackQuery, state: FSMContext):
    lang = callback.data.split("_")[1]
    await state.update_data(lang=lang)
    await callback.message.answer(t("reg_fio", lang), reply_markup=ReplyKeyboardRemove())
    await state.set_state(RegState.fio)
    await callback.answer()

@router.message(RegState.fio, F.text)
async def set_fio(message: Message, state: FSMContext):
    await state.update_data(fio=message.text)
    data = await state.get_data()
    await message.answer(t("reg_course", data['lang']), reply_markup=get_course_keyboard())
    await state.set_state(RegState.course)

@router.callback_query(RegState.course, F.data.startswith("set_course_"))
async def set_course(callback: CallbackQuery, state: FSMContext):
    course = callback.data.split("_")[2]
    await state.update_data(course=course)
    data = await state.get_data()
    await callback.message.edit_text(f"{t('course_saved', data['lang'])} {course}")
    await callback.message.answer(t("reg_year", data['lang']))
    await state.set_state(RegState.year)
    await callback.answer()

@router.message(RegState.year, F.text)
async def set_year(message: Message, state: FSMContext):
    await state.update_data(year=message.text)
    data = await state.get_data()
    await message.answer(t("reg_faculty", data['lang']), reply_markup=get_faculty_keyboard(data['lang']))
    await state.set_state(RegState.faculty)

@router.callback_query(RegState.faculty, F.data.startswith("fac_"))
async def set_faculty(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await save_user_profile(
        callback.from_user.id, 
        {
            "lang": data['lang'], 
            "fio": data['fio'], 
            "course": data['course'], 
            "year": data['year'], 
            "faculty": t(callback.data, data['lang']), 
            "activity": 0
        }
    )
    await callback.message.delete()
    await callback.message.answer(t("reg_success", data['lang']), reply_markup=get_main_keyboard(data['lang'], callback.from_user.id))
    await state.clear()
    await callback.answer()

# --- ПРОФИЛЬ И ОБРАТНАЯ СВЯЗЬ ---

@router.message(F.text.in_({"👤 Профиль", "👤 Profile", "👤 Profil", "👤 Мой профиль", "👤 Mening profilim"}))
async def profile_handler(message: Message, state: FSMContext):
    if not await check_auth(message, state):
        return 
        
    profile = await get_user_profile(message.from_user.id)
    lang = profile.get('lang', 'ru')
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t('change_profile', lang), callback_data="edit_profile")], 
        [InlineKeyboardButton(text=t('change_lang', lang), callback_data="switch_lang_menu")]
    ])
    
    await message.answer(f"👤 {profile['fio']}\n📚 {profile.get('course', '1')} {t('course_word', lang)}\n🎓 {profile.get('faculty', 'Unknown')}", reply_markup=kb)

@router.message(F.text.in_({"📩 Обратная связь", "📩 Feedback", "📩 Fikr bildirish"}))
async def fb_reply_btn_handler(message: Message, state: FSMContext):
    if not await check_auth(message, state):
        return 
        
    lang = await get_user_lang(message.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("fb_review", lang), callback_data="fb_review")],
        [InlineKeyboardButton(text=t("fb_bug", lang), callback_data="fb_bug")],
        [InlineKeyboardButton(text=t("fb_feature", lang), callback_data="fb_feature")]
    ])
    await message.answer(t("fb_ask", lang), reply_markup=kb)

@router.callback_query(F.data == "feedback_menu")
async def fb_menu_handler(callback: CallbackQuery):
    lang = await get_user_lang(callback.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("fb_review", lang), callback_data="fb_review")],
        [InlineKeyboardButton(text=t("fb_bug", lang), callback_data="fb_bug")],
        [InlineKeyboardButton(text=t("fb_feature", lang), callback_data="fb_feature")]
    ])
    await callback.message.edit_text(t("fb_ask", lang), reply_markup=kb)

@router.callback_query(F.data.startswith("fb_"))
async def process_fb_selection(callback: CallbackQuery, state: FSMContext):
    fb_type = callback.data.split("_")[1]
    type_map = {"review": "Отзыв", "bug": "Ошибка", "feature": "Предложение"}
    
    await state.update_data(fb_type=type_map.get(fb_type, "Другое"))
    await state.set_state(FeedbackState.waiting_for_text)
    
    lang = await get_user_lang(callback.from_user.id)
    await callback.message.delete()
    await callback.message.answer(t("fb_prompt", lang), reply_markup=get_cancel_keyboard(lang))
    await callback.answer()

@router.message(FeedbackState.waiting_for_text, F.text)
async def process_fb_text(message: Message, state: FSMContext):
    if await handle_menu_interrupt(message, state):
        return

    lang = await get_user_lang(message.from_user.id)
    if message.text in [t("back", "ru"), t("back", "en"), t("back", "uz"), t("back", lang)]:
        return await back_handler(message, state)
        
    data = await state.get_data()
    fb_type = data.get("fb_type", "Другое")
    
    await save_feedback(message.from_user.id, fb_type, message.text)
    await message.answer(t("fb_success", lang), reply_markup=get_main_keyboard(lang, message.from_user.id))
    await state.clear()

@router.callback_query(F.data == "edit_profile")
async def edit_profile(callback: CallbackQuery, state: FSMContext):
    lang = await get_user_lang(callback.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("edit_fio",     lang), callback_data="ep_fio")],
        [InlineKeyboardButton(text=t("edit_course",  lang), callback_data="ep_course")],
        [InlineKeyboardButton(text=t("edit_year",    lang), callback_data="ep_year")],
        [InlineKeyboardButton(text=t("edit_faculty", lang), callback_data="ep_faculty")],
    ])
    await state.set_state(EditProfileState.choosing_field)
    await callback.message.answer(t("edit_profile_menu", lang), reply_markup=kb)
    await callback.answer()


@router.callback_query(EditProfileState.choosing_field, F.data == "ep_fio")
async def ep_ask_fio(callback: CallbackQuery, state: FSMContext):
    lang = await get_user_lang(callback.from_user.id)
    await state.set_state(EditProfileState.editing_fio)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(t("reg_fio", lang), reply_markup=get_cancel_keyboard(lang))
    await callback.answer()

@router.message(EditProfileState.editing_fio, F.text)
async def ep_save_fio(message: Message, state: FSMContext):
    if await handle_menu_interrupt(message, state):
        return

    lang = await get_user_lang(message.from_user.id)
    if message.text in [t("back", "ru"), t("back", "en"), t("back", "uz")]:
        await state.clear()
        return await message.answer("🏠", reply_markup=get_main_keyboard(lang, message.from_user.id))
    profile = await get_user_profile(message.from_user.id)
    profile["fio"] = message.text
    await save_user_profile(message.from_user.id, profile)
    await state.clear()
    await message.answer(t("edit_saved", lang), reply_markup=get_main_keyboard(lang, message.from_user.id))


@router.callback_query(EditProfileState.choosing_field, F.data == "ep_course")
async def ep_ask_course(callback: CallbackQuery, state: FSMContext):
    lang = await get_user_lang(callback.from_user.id)
    await state.set_state(EditProfileState.editing_course)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(t("reg_course", lang), reply_markup=get_course_keyboard())
    await callback.answer()

@router.callback_query(EditProfileState.editing_course, F.data.startswith("set_course_"))
async def ep_save_course(callback: CallbackQuery, state: FSMContext):
    lang = await get_user_lang(callback.from_user.id)
    course = callback.data.replace("set_course_", "")
    profile = await get_user_profile(callback.from_user.id)
    profile["course"] = course
    await save_user_profile(callback.from_user.id, profile)
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(t("edit_saved", lang), reply_markup=get_main_keyboard(lang, callback.from_user.id))
    await callback.answer()


@router.callback_query(EditProfileState.choosing_field, F.data == "ep_year")
async def ep_ask_year(callback: CallbackQuery, state: FSMContext):
    lang = await get_user_lang(callback.from_user.id)
    await state.set_state(EditProfileState.editing_year)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(t("reg_year", lang), reply_markup=get_cancel_keyboard(lang))
    await callback.answer()

@router.message(EditProfileState.editing_year, F.text)
async def ep_save_year(message: Message, state: FSMContext):
    if await handle_menu_interrupt(message, state):
        return

    lang = await get_user_lang(message.from_user.id)
    if message.text in [t("back", "ru"), t("back", "en"), t("back", "uz")]:
        await state.clear()
        return await message.answer("🏠", reply_markup=get_main_keyboard(lang, message.from_user.id))
    if not message.text.isdigit() or len(message.text) != 4:
        return await message.answer(t("err_num", lang))
    profile = await get_user_profile(message.from_user.id)
    profile["year"] = message.text
    await save_user_profile(message.from_user.id, profile)
    await state.clear()
    await message.answer(t("edit_saved", lang), reply_markup=get_main_keyboard(lang, message.from_user.id))


@router.callback_query(EditProfileState.choosing_field, F.data == "ep_faculty")
async def ep_ask_faculty(callback: CallbackQuery, state: FSMContext):
    lang = await get_user_lang(callback.from_user.id)
    await state.set_state(EditProfileState.editing_faculty)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(t("reg_faculty", lang), reply_markup=get_faculty_keyboard(lang))
    await callback.answer()

@router.callback_query(EditProfileState.editing_faculty, F.data.startswith("fac_"))
async def ep_save_faculty(callback: CallbackQuery, state: FSMContext):
    lang = await get_user_lang(callback.from_user.id)
    profile = await get_user_profile(callback.from_user.id)
    profile["faculty"] = t(callback.data, lang)
    await save_user_profile(callback.from_user.id, profile)
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(t("edit_saved", lang), reply_markup=get_main_keyboard(lang, callback.from_user.id))
    await callback.answer()

@router.callback_query(F.data == "switch_lang_menu")
async def switch_lang_menu(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 Русский",  callback_data="setlang_ru")],
        [InlineKeyboardButton(text="🇬🇧 English",  callback_data="setlang_en")],
        [InlineKeyboardButton(text="🇺🇿 O'zbek",   callback_data="setlang_uz")],
    ])
    await callback.message.answer("Select:", reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data.startswith("setlang_"))
async def switch_lang_action(callback: CallbackQuery, state: FSMContext):
    lang = callback.data.split("_")[1]
    await update_user_lang(callback.from_user.id, lang)
    await callback.message.delete()
    await callback.message.answer(t("lang_changed", lang), reply_markup=get_main_keyboard(lang, callback.from_user.id))
    await callback.answer()

# --- БИБЛИОТЕКА И ФИЛЬТРАЦИЯ ПО КУРСАМ ---

COURSE_SUBJECTS = {
    "1": ["Анатомия", "Гистология", "Биология"],
    "2": ["Биохимия", "Физиология"],
    "3": ["Патофизиология", "Фармакология"]
}

@router.message(F.text.in_({"📚 Библиотека", "📚 Library", "📚 Kutubxona", "📚 Материалы кафедры", "📚 Department Materials", "📚 Kafedra materiallari"}))
async def lib_start(message: Message, state: FSMContext):
    if not await check_auth(message, state):
        return 
        
    lang = await get_user_lang(message.from_user.id)
    course = str(await get_user_course(message.from_user.id)) 
    
    await message.answer(t("check_books", lang))
    await sync_library()
    
    cat = await get_library_catalog()
    if not cat:
        return await message.answer(t("lib_empty", lang), reply_markup=get_main_keyboard(lang, message.from_user.id))
        
    all_cats_in_db = list(set((b.get('category') or 'Разное') for b in cat.values()))
    allowed_for_course = COURSE_SUBJECTS.get(course, [])
    
    if allowed_for_course:
        display_cats = [c for c in allowed_for_course if c in all_cats_in_db]
    else:
        display_cats = sorted(all_cats_in_db)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📂 {c}", callback_data=f"lib_cat_{get_category_hash(c)}")] for c in display_cats
    ])
    
    if allowed_for_course and len(display_cats) < len(all_cats_in_db):
        kb.inline_keyboard.append([InlineKeyboardButton(text="📚 Показать все предметы", callback_data="lib_all_cats")])
        
    await message.answer(t("select_section", lang) + f" (Курс: {course})", reply_markup=kb)

@router.callback_query(F.data == "lib_all_cats")
async def show_all_categories(callback: CallbackQuery):
    lang = await get_user_lang(callback.from_user.id)
    catalog = await get_library_catalog()
    
    all_cats = sorted(list(set((b.get('category') or 'Разное') for b in catalog.values())))
    
    builder = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📂 {c}", callback_data=f"lib_cat_{get_category_hash(c)}")] for c in all_cats
    ])
    builder.inline_keyboard.append([InlineKeyboardButton(text="🔙 Назад к моим предметам", callback_data="lib_main")])
    
    await callback.message.edit_text("📚 Все доступные дисциплины:", reply_markup=builder)
    await callback.answer()

@router.callback_query(F.data == "lib_main")
async def back_to_categories(callback: CallbackQuery):
    lang = await get_user_lang(callback.from_user.id)
    course = str(await get_user_course(callback.from_user.id))
    catalog = await get_library_catalog()
    
    all_cats_in_db = list(set((b.get('category') or 'Разное') for b in catalog.values()))
    allowed_for_course = COURSE_SUBJECTS.get(course, [])
    
    if allowed_for_course:
        display_cats = [c for c in allowed_for_course if c in all_cats_in_db]
    else:
        display_cats = sorted(all_cats_in_db)

    builder = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📂 {c}", callback_data=f"lib_cat_{get_category_hash(c)}")] for c in display_cats
    ])
    
    if allowed_for_course and len(display_cats) < len(all_cats_in_db):
        builder.inline_keyboard.append([InlineKeyboardButton(text="📚 Показать все предметы", callback_data="lib_all_cats")])
        
    await callback.message.edit_text(t("select_section", lang) + f" (Курс: {course})", reply_markup=builder)
    await callback.answer()

@router.callback_query(F.data.startswith("lib_cat_"))
async def show_books_in_category(callback: CallbackQuery):
    cat_hash = callback.data.replace("lib_cat_", "")
    catalog = await get_library_catalog()
    target_cat = "Раздел"
    buttons = []
    
    for d in catalog.values():
        c = d.get('category') or 'Разное'
        if get_category_hash(c) == cat_hash:
            target_cat = c
            if d.get('id'):
                buttons.append([InlineKeyboardButton(text=f"📘 {d.get('title', 'Book')[:30]}", callback_data=f"lib_b_{d.get('id')}")])
                
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="lib_main")])
    await callback.message.edit_text(f"📂 {target_cat}", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()

@router.callback_query(F.data.startswith("lib_b_"))
async def send_book_file(callback: CallbackQuery):
    short_id = callback.data.replace("lib_b_", "")
    catalog = await get_library_catalog()
    found_path = next((d.get('path') for d in catalog.values() if d.get('id') == short_id), None)
    
    if found_path and os.path.exists(found_path):
        await callback.message.answer_document(FSInputFile(found_path))
    else:
        await callback.answer("❌ Файл не найден", show_alert=True)
        
    await callback.answer()


def _collect_preparations_categories(catalog: dict, lang: str) -> list[tuple[str, str]]:
    categories: dict[str, str] = {}
    for item in catalog.values():
        canonical = str(item.get("category") or "Разное")
        localized = _pick_localized_name(item.get("category_i18n"), canonical, lang)
        categories[canonical] = localized
    return sorted(categories.items(), key=lambda pair: pair[1].lower())


def _build_preparations_categories_keyboard(categories: list[tuple[str, str]], lang: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f"🧫 {localized}", callback_data=f"prep_cat_{get_category_hash(canonical)}")]
        for canonical, localized in categories
    ]
    buttons.append([InlineKeyboardButton(text=t("back", lang), callback_data="prep_back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(F.text.in_(MENU_PREPARATIONS_TEXTS))
async def preparations_start(message: Message, state: FSMContext):
    if not await check_auth(message, state):
        return

    lang = await get_user_lang(message.from_user.id)
    catalog = await get_preparations_catalog()
    if not catalog:
        return await message.answer(t("prep_empty", lang), reply_markup=get_main_keyboard(lang, message.from_user.id))

    categories = _collect_preparations_categories(catalog, lang)
    kb = _build_preparations_categories_keyboard(categories, lang)
    await message.answer(t("prep_select_category", lang), reply_markup=kb)


@router.callback_query(F.data == "prep_main")
async def preparations_show_categories(callback: CallbackQuery):
    lang = await get_user_lang(callback.from_user.id)
    catalog = await get_preparations_catalog()
    if not catalog:
        await callback.message.edit_text(t("prep_empty", lang))
        await callback.answer()
        return

    categories = _collect_preparations_categories(catalog, lang)
    kb = _build_preparations_categories_keyboard(categories, lang)
    await callback.message.edit_text(t("prep_select_category", lang), reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "prep_back_menu")
async def preparations_back_menu(callback: CallbackQuery):
    lang = await get_user_lang(callback.from_user.id)
    await callback.message.answer(t("back", lang), reply_markup=get_main_keyboard(lang, callback.from_user.id))
    await callback.answer()


@router.callback_query(F.data.startswith("prep_cat_"))
async def preparations_show_files(callback: CallbackQuery):
    lang = await get_user_lang(callback.from_user.id)
    cat_hash = callback.data.replace("prep_cat_", "")
    catalog = await get_preparations_catalog()

    target_cat = "Разное"
    target_cat_localized = target_cat
    buttons = []
    for item in catalog.values():
        category = str(item.get("category") or "Разное")
        if get_category_hash(category) == cat_hash:
            target_cat = category
            target_cat_localized = _pick_localized_name(item.get("category_i18n"), target_cat, lang)
            prep_id = item.get("id")
            if prep_id:
                fallback_title = str(item.get("title", "Preparation"))
                title = _pick_localized_name(item.get("title_i18n"), fallback_title, lang)[:36]
                buttons.append([InlineKeyboardButton(text=f"📄 {title}", callback_data=f"prep_file_{prep_id}")])

    buttons.append([InlineKeyboardButton(text=t("back", lang), callback_data="prep_main")])
    buttons.append([InlineKeyboardButton(text="🏠", callback_data="prep_back_menu")])
    await callback.message.edit_text(
        f"{t('prep_select_item', lang)}\n\n🧫 {target_cat_localized}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("prep_file_"))
async def preparations_send_file(callback: CallbackQuery):
    lang = await get_user_lang(callback.from_user.id)
    prep_id = callback.data.replace("prep_file_", "")
    catalog = await get_preparations_catalog()

    found_path = None
    for key, item in catalog.items():
        item_id = item.get("id") or key
        if item_id == prep_id:
            found_path = item.get("path")
            break

    if found_path and os.path.exists(found_path):
        await callback.message.answer_document(FSInputFile(found_path))
    else:
        await callback.answer(t("prep_not_found", lang), show_alert=True)
        return

    await callback.answer()

# --- ТЕОРИЯ И БЫСТРЫЕ КНОПКИ ---

@router.message(F.text.in_({"📖 Теория", "📖 Theory", "📖 Nazariya", "🔬 База Знаний", "🔬 Knowledge Base", "🔬 Bilimlar bazasi"}))
async def theory_start(message: Message, state: FSMContext):
    if not await check_auth(message, state):
        return

    lang = await get_user_lang(message.from_user.id)
    user_id = message.from_user.id

    profile = await get_user_profile(user_id)
    faculty_key = profile.get("faculty") if profile else None
    topics = get_topics_for_faculty(faculty_key, "practicals") if faculty_key else []

    if topics:
        # Resolve to canonical key (ktp_service may match by alias)
        from services.ktp_service import FACULTY_MAP
        canonical = FACULTY_MAP.get(faculty_key, faculty_key)
        await _show_ktp_list(message, state, lang, topics, canonical)
    else:
        last_topic = await get_last_topic(user_id)
        await _show_topic_input(message, state, lang, last_topic)


async def _show_ktp_list(message, state, lang, topics, faculty_key):
    """Show KTP topic list. Each button callback encodes faculty_key + topic num."""
    rows = []

    # Last used topic shortcut at the top
    last_topic = await get_last_topic(message.from_user.id)
    if last_topic:
        rows.append([InlineKeyboardButton(
            text=f"🔄 {last_topic[:40]}...",
            callback_data="use_last_topic"
        )])

    # KTP topics — label in user's language, Russian topic used for AI
    for tp in topics[:20]:
        display = get_topic_label(tp, lang)
        label = f"{tp['num']}. {display[:45]}"
        rows.append([InlineKeyboardButton(
            text=label,
            callback_data=f"ktp_topic_{faculty_key}_{tp['num']}"
        )])

    # Custom topic option at the bottom
    rows.append([InlineKeyboardButton(text=t("custom_btn", lang), callback_data="theory_source_custom")])

    await state.set_state(TmaState.waiting_for_theory)
    await message.answer(t("ktp_select", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _show_topic_input(message, state, lang, last_topic=None):
    """Show the classic free-text topic prompt with optional last-topic shortcut."""
    if last_topic is None:
        last_topic = await get_last_topic(message.from_user.id)
    await state.set_state(TmaState.waiting_for_theory)
    if last_topic:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"🔄 {last_topic[:35]}...",
                callback_data="use_last_topic"
            )]
        ])
        await message.answer(t("enter_topic", lang), reply_markup=kb)
        await message.answer(t("or_new_topic", lang), reply_markup=get_cancel_keyboard(lang))
    else:
        await message.answer(t("enter_topic", lang), reply_markup=get_cancel_keyboard(lang))


# ── KTP / topic source callbacks ─────────────────────────────────────────────

@router.callback_query(F.data == "theory_source_custom")
async def theory_source_custom(callback: CallbackQuery, state: FSMContext):
    lang = await get_user_lang(callback.from_user.id)
    last_topic = await get_last_topic(callback.from_user.id)
    await callback.message.delete()
    await _show_topic_input(callback.message, state, lang, last_topic)
    await callback.answer()


@router.callback_query(F.data.startswith("ktp_topic_"))
async def ktp_topic_chosen(callback: CallbackQuery, state: FSMContext):
    lang = await get_user_lang(callback.from_user.id)
    await callback.answer()

    # callback_data format: ktp_topic_{faculty_key}_{num}
    # faculty_key itself may contain underscores (e.g. fac_stom_inter)
    # so split from right: last token = num, rest = faculty_key
    raw = callback.data[len("ktp_topic_"):]     # e.g. "fac_stom_12"
    last_sep = raw.rfind("_")
    faculty_key = raw[:last_sep]                # e.g. "fac_stom"
    topic_num   = int(raw[last_sep + 1:])       # e.g. 12

    topics = get_topics_for_faculty(faculty_key, "practicals")
    topic_entry = next((tp for tp in topics if tp["num"] == topic_num), None)

    if not topic_entry:
        await callback.message.answer(t("ktp_no_data", lang))
        await state.clear()
        return

    # Keep the selected KTP topic in the user's language for AI, PDF title, and history.
    topic_text = get_topic_label(topic_entry, lang)
    await callback.message.delete()
    await process_topic_logic(topic_text, callback.message, state, callback.from_user.id)


@router.callback_query(F.data == "use_last_topic")
async def trigger_last_topic(callback: CallbackQuery, state: FSMContext):
    topic = await get_last_topic(callback.from_user.id)
    if not topic:
        return await callback.answer("Тема не найдена", show_alert=True)
        
    # СРАЗУ отвечаем Telegram, чтобы не было тайм-аута (ошибка query is too old)
    await callback.answer()
        
    current_state = await state.get_state()
    await callback.message.delete()
    
    if current_state == TmaState.waiting_for_theory:
        # Долгая загрузка ИИ теперь не сломает кнопку
        await process_topic_logic(topic, callback.message, state, callback.from_user.id)
    elif current_state == TmaState.waiting_for_ai_quiz:
        await state.update_data(topic=topic)
        lang = await get_user_lang(callback.from_user.id)
        await callback.message.answer(t("enter_quiz_count", lang), reply_markup=get_cancel_keyboard(lang))
        await state.set_state(TmaState.waiting_for_quiz_count)

async def process_topic_logic(topic_text, message, state, user_id):
    lang = await get_user_lang(user_id)

    # ── Off-topic guard ──────────────────────────────────────────────────────
    if not await is_medical_topic(topic_text):
        await message.answer(t("off_topic", lang))
        await state.clear()
        return
    # ── Request limit guard ──────────────────────────────────────────────────
    if not await check_limit(message, lang, user_id=user_id):
        await state.clear()
        return
    # ────────────────────────────────────────────────────────────────────────

    await update_user_activity(user_id)
    await update_last_topic(user_id, topic_text)
    profile = await get_user_profile(user_id)

    status_msg = await message.answer(t("search_books", lang))
    book_context = await search_knowledge_base(topic_text)
    await status_msg.edit_text(t("write_lecture", lang))
    
    response = await get_collaborative_response(topic_text, profile.get('course', '1'), lang, book_context)
    response = clean_text_output(response)
    await status_msg.delete()
    
    pdf_path = await asyncio.to_thread(generate_theory_pdf, response, user_id, topic_text, lang)
    if pdf_path and os.path.exists(pdf_path):
        await message.answer_document(
            FSInputFile(pdf_path, filename=build_pdf_export_filename(topic_text, lang, kind="theory")),
            caption=t("theory_pdf_caption", lang),
            reply_markup=get_main_keyboard(lang, user_id),
        )
        os.remove(pdf_path)
    else:
        await send_safe_message(message, response, reply_markup=get_main_keyboard(lang, user_id))
    
    images = await find_study_images(topic_text, lang=lang, count=2)
    if images:
        try:
            await message.answer_media_group([InputMediaPhoto(media=img) for img in images])
        except Exception:
            pass
            
    await message.answer(t("fb_request", lang), reply_markup=get_feedback_keyboard(lang))
    await state.clear()

@router.message(TmaState.waiting_for_theory, F.text)
async def theory_process_text(message: Message, state: FSMContext):
    if await handle_menu_interrupt(message, state):
        return

    lang = await get_user_lang(message.from_user.id)
    if message.text in [t("back", "ru"), t("back", "en"), t("back", "uz"), t("back", lang)]:
        await state.clear()
        return await message.answer("🏠", reply_markup=get_main_keyboard(lang, message.from_user.id))
        
    await process_topic_logic(message.text, message, state, message.from_user.id)

@router.message(F.text.in_({"🧠 Тест (AI)", "🧠 Quiz (AI)", "🧠 Test (AI)", "🧠 AI-Экзаменатор", "🧠 AI Examiner", "🧠 AI-Imtihon"}))
async def quiz_start(message: Message, state: FSMContext):
    if not await check_auth(message, state):
        return 
        
    lang = await get_user_lang(message.from_user.id)
    last_topic = await get_last_topic(message.from_user.id)
    await state.set_state(TmaState.waiting_for_ai_quiz)
    
    if last_topic:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{t('repeat_topic', lang)} {last_topic[:25]}...", callback_data="use_last_topic")]
        ])
        await message.answer(t("enter_quiz_topic", lang), reply_markup=kb)
        await message.answer(t("or_new_topic", lang), reply_markup=get_cancel_keyboard(lang))
    else:
        await message.answer(t("enter_quiz_topic", lang), reply_markup=get_cancel_keyboard(lang))

@router.message(TmaState.waiting_for_ai_quiz, F.text)
async def quiz_count(message: Message, state: FSMContext):
    if await handle_menu_interrupt(message, state):
        return

    lang = await get_user_lang(message.from_user.id)
    if message.text in [t("back", "ru"), t("back", "en"), t("back", "uz"), t("back", lang)]:
        return await back_handler(message, state)

    # ── Off-topic guard ──────────────────────────────────────────────────────
    if not await is_medical_topic(message.text):
        await message.answer(t("off_topic", lang))
        await state.clear()
        return
    # ────────────────────────────────────────────────────────────────────────

    await state.update_data(topic=message.text)
    await update_last_topic(message.from_user.id, message.text)

    await message.answer(t("enter_quiz_count", lang), reply_markup=get_cancel_keyboard(lang))
    await state.set_state(TmaState.waiting_for_quiz_count)


@router.callback_query(TmaState.waiting_for_quiz_type, F.data.startswith("qtype_"))
async def quiz_type_chosen(callback: CallbackQuery, state: FSMContext):
    lang = await get_user_lang(callback.from_user.id)
    quiz_type = callback.data.split("_")[1]   # "closed" or "open"
    await state.update_data(quiz_type=quiz_type)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(t("enter_quiz_count", lang), reply_markup=get_cancel_keyboard(lang))
    await state.set_state(TmaState.waiting_for_quiz_count)

@router.message(TmaState.waiting_for_quiz_count, F.text)
async def quiz_gen(message: Message, state: FSMContext):
    if await handle_menu_interrupt(message, state):
        return

    lang = await get_user_lang(message.from_user.id)
    if message.text in [t("back", "ru"), t("back", "en"), t("back", "uz"), t("back", lang)]:
        return await back_handler(message, state)

    try:
        cnt = int(message.text)
    except ValueError:
        return await message.answer(t("err_num", lang))

    profile = await get_user_profile(message.from_user.id)
    data = await state.get_data()

    # ── Request limit guard ──────────────────────────────────────────────────
    if not await check_limit(message, lang):
        await state.clear()
        return
    # ────────────────────────────────────────────────────────────────────────

    await message.answer(t("gen_wait", lang), reply_markup=ReplyKeyboardRemove())

    usecase = QuizUseCase(message.from_user.id, profile.get('course', '1'), lang)
    questions = await usecase.execute(data['topic'], cnt)

    if not questions:
        return await message.answer(t("err_gen", lang), reply_markup=get_main_keyboard(lang, message.from_user.id))

    random.shuffle(questions)
    for q in questions:
        shuffle_question_options(q)

    open_question_indices = build_mixed_open_indices(len(questions))

    await state.update_data(
        questions=questions,
        current_index=0,
        score=0,
        test_source="ai",
        export_source_name=data.get("topic", ""),
        export_results_file=True,
        selected_indices=[],
        open_question_indices=open_question_indices,
    )

    await message.answer(t("quiz_ready", lang), reply_markup=get_cancel_keyboard(lang))
    await send_next_question(message, state)

@router.message(F.text.in_(MENU_FILE_QUIZ_TEXTS))
async def file_quiz_prompt(message: Message, state: FSMContext):
    if not await check_auth(message, state):
        return 
        
    lang = await get_user_lang(message.from_user.id)
    
    if lang == "ru":
        text = "📁 Пожалуйста, отправьте файл с вашими тестами строго в формате .txt"
    elif lang == "uz":
        text = "📁 Testlaringizni qat'iy .txt formatidagi fayl sifatida yuboring"
    else:
        text = "📁 Please send your tests file strictly in .txt format"
        
    await message.answer(clean_text_output(text), reply_markup=get_cancel_keyboard(lang))
    await state.set_state(TmaState.waiting_for_file_quiz)


@router.message(F.text.in_({"📝 Контрольный тест", "📝 Control Test", "📝 Nazorat testi", "Nazorat testi"}))
async def control_test_start(message: Message, state: FSMContext):
    if not await check_auth(message, state):
        return

    lang = await get_user_lang(message.from_user.id)
    if not await has_active_narozat_access(message.from_user.id):
        await message.answer(
            t("narozat_required", lang),
            reply_markup=_narozat_kb(lang),
        )
        return

    tests = await list_active_control_tests()
    if not tests:
        await message.answer(t("control_test_not_available", lang), reply_markup=get_main_keyboard(lang, message.from_user.id))
        return

    await message.answer(
        t("control_test_choose", lang),
        reply_markup=get_control_tests_keyboard(tests, lang),
    )


@router.callback_query(F.data == "ctest_back_main")
async def control_test_back_to_main(callback: CallbackQuery, state: FSMContext):
    lang = await get_user_lang(callback.from_user.id)
    await state.clear()
    await callback.message.answer(t("back", lang), reply_markup=get_main_keyboard(lang, callback.from_user.id))
    await callback.answer()


@router.callback_query(F.data.startswith("ctest_pick_"))
async def control_test_pick(callback: CallbackQuery, state: FSMContext):
    lang = await get_user_lang(callback.from_user.id)
    if not await is_user_registered(callback.from_user.id):
        await callback.answer(t("welcome", lang), show_alert=True)
        return

    try:
        test_id = int(callback.data.replace("ctest_pick_", ""))
    except ValueError:
        await callback.answer()
        return

    payload = await get_control_test_by_id(test_id)
    if not payload:
        await callback.message.answer(t("control_test_unavailable_pick", lang))
        await callback.answer()
        return

    questions = payload.get("questions", [])
    if not questions:
        await callback.message.answer(t("control_test_unavailable_pick", lang))
        await callback.answer()
        return

    await state.clear()
    await state.update_data(
        control_test_raw_questions=questions,
        control_test_title=payload.get("title", "control_test"),
    )

    await callback.message.answer(
        t("control_test_choose_count", lang),
        reply_markup=get_control_test_count_keyboard(lang),
    )
    await callback.answer()

@router.callback_query(F.data.startswith("ctest_count_"))
async def control_test_count_pick(callback: CallbackQuery, state: FSMContext):
    lang = await get_user_lang(callback.from_user.id)
    await callback.answer()

    state_data = await state.get_data()
    raw_questions = state_data.get("control_test_raw_questions", [])
    title = state_data.get("control_test_title", "control_test")
    if not raw_questions:
        await callback.message.answer(t("control_test_unavailable_pick", lang))
        return

    try:
        desired_count = int(callback.data.replace("ctest_count_", ""))
    except ValueError:
        await callback.message.answer(t("control_test_unavailable_pick", lang))
        return

    selected = select_random_control_test_questions(raw_questions, desired_count)
    questions = prepare_control_test_questions(selected)
    if not questions:
        await callback.message.answer(t("control_test_unavailable_pick", lang))
        return

    await state.clear()
    await state.update_data(
        questions=questions,
        current_index=0,
        score=0,
        test_source="admin_test",
        export_source_name=title,
        export_results_file=False,
        selected_indices=[],
        open_question_indices=[],
    )

    await callback.message.answer(
        t("control_test_starting", lang).format(title=title),
        reply_markup=get_cancel_keyboard(lang),
    )
    await send_next_question(callback.message, state)

@router.message(TmaState.waiting_for_file_quiz, F.text)
async def wait_for_file_text(message: Message, state: FSMContext):
    if await handle_menu_interrupt(message, state):
        return

    lang = await get_user_lang(message.from_user.id)
    if message.text in [t("back", "ru"), t("back", "en"), t("back", "uz"), t("back", lang)]:
        return await back_handler(message, state)
        
    if lang == "ru": err = "❌ Ошибка. Отправьте файл .txt или нажмите В главное меню"
    elif lang == "uz": err = "❌ Xato. .txt faylini yuboring yoki Bosh menyu ni bosing"
    else: err = "❌ Error. Please send a .txt file or press Main Menu"
    
    await message.answer(clean_text_output(err))

@router.message(F.text.in_({"📊 Статистика", "📊 Statistics", "📊 Statistika"}))
async def show_stats(message: Message, state: FSMContext):
    if not await check_auth(message, state):
        return 
        
    lang = await get_user_lang(message.from_user.id)
    stats = await get_bot_statistics()
    
    text = f"{t('stats_title', lang)}\n\n{t('users_total', lang)} {stats['total']}\n\n{t('stats_langs', lang)}\n"
    for l, count in stats['langs'].items():
        text += f"▪️ {l.upper()}: {count}\n"
        
    text += f"\n{t('stats_courses', lang)}\n"
    for c, count in stats['courses'].items():
        text += f"▪️ {c} {t('course_word', lang).lower()}: {count}\n"
    
    if str(message.from_user.id) == str(ADMIN_ID):
        adv_stats = await get_admin_statistics()
        text += f"\n👑 Аналитика активности (Админ):\n"
        text += f"🟢 Активность за сегодня (DAU): {adv_stats['dau']}\n"
        text += f"📅 Активность за неделю (WAU): {adv_stats['wau']}\n"
        text += f"🆕 Новых за неделю: {adv_stats['new_weekly']}\n"
        text += f"♻️ Вернувшихся (Retention): {adv_stats['retention']} (заходили повторно)\n"

    await message.answer(text, reply_markup=get_main_keyboard(lang, message.from_user.id))

@router.message(F.text.in_({"ℹ️ Инструкция", "ℹ️ Info", "ℹ️ Yo'riqnoma"}))
async def info_handler(message: Message, state: FSMContext):
    if not await check_auth(message, state):
        return 
        
    lang = await get_user_lang(message.from_user.id)
    await message.answer(t("welcome", lang), reply_markup=get_main_keyboard(lang, message.from_user.id))

@router.message(F.text.in_({"🔙 В меню", "🔙 Main Menu", "🔙 Menyuga qaytish", "🔙 В главное меню", "🔙 Bosh menyu"}))
async def back_handler(message: Message, state: FSMContext):
    if not await check_auth(message, state):
        return 
        
    lang = await get_user_lang(message.from_user.id)
    await state.clear()
    await message.answer(t("back", lang), reply_markup=get_main_keyboard(lang, message.from_user.id))

@router.message(F.text.in_({"💎 Подписка", "💎 Subscription", "💎 Obuna"}))
async def premium_menu(message: Message):
    lang = await get_user_lang(message.from_user.id)
    status = await get_user_premium_status(message.from_user.id)

    if _is_premium_status_active(status):
        return await message.answer(t("premium_active", lang))

    await message.answer(
        f"{t('premium_title', lang)}\n\n{t('premium_desc', lang)}",
        reply_markup=_premium_kb(lang)
    )

@router.callback_query(F.data == "buy_premium")
async def send_invoice_handler(callback: CallbackQuery):
    lang = await get_user_lang(callback.from_user.id)
    await callback.answer()

    tx = await create_payment(10000, "AI Study Assistant Premium")
    if not tx:
        return await callback.message.answer(t("payment_error", lang))

    tx_id = tx.get("tx_id") or tx.get("id")
    amount = tx.get("amount", 10000)
    card = tx.get("card") or tx.get("card_number") or tx.get("requisite") or "—"

    await save_pending_payment(callback.from_user.id, tx_id, amount)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("check_payment_btn", lang), callback_data="check_payment")]
    ])
    await callback.message.answer(
        t("payment_instructions", lang).format(amount=f"{amount:,}", card=card),
        reply_markup=kb,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "check_payment")
async def check_payment_cb(callback: CallbackQuery):
    lang = await get_user_lang(callback.from_user.id)
    await callback.answer()

    pending = await get_pending_payment(callback.from_user.id, payment_type="premium")
    if not pending:
        return await callback.message.answer(t("no_pending", lang))

    status = await check_payment(pending["tx_id"])

    if status == "paid":
        await record_payment(callback.from_user.id, pending["tx_id"], pending["amount"], payment_type="premium")
        await delete_pending_payment(callback.from_user.id, payment_type="premium")
        await set_user_premium(callback.from_user.id, 1)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(t("payment_success", lang))
    elif status == "cancelled":
        await delete_pending_payment(callback.from_user.id, payment_type="premium")
        await callback.message.answer(t("payment_cancelled", lang))
    elif status == "pending":
        await callback.message.answer(t("payment_pending", lang))
    else:
        await callback.message.answer(t("payment_error", lang))

@router.callback_query(F.data == "buy_narozat")
async def buy_narozat_handler(callback: CallbackQuery):
    lang = await get_user_lang(callback.from_user.id)
    await callback.answer()

    tx = await create_payment(10000, "Narozat test access (30 days)")
    if not tx:
        return await callback.message.answer(t("payment_error", lang))

    tx_id = tx.get("tx_id") or tx.get("id")
    amount = tx.get("amount", 10000)
    card = tx.get("card") or tx.get("card_number") or tx.get("requisite") or "—"
    await save_pending_payment(callback.from_user.id, tx_id, amount, payment_type="narozat")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("check_payment_btn", lang), callback_data="check_narozat_payment")]
    ])
    await callback.message.answer(
        t("payment_instructions", lang).format(amount=f"{amount:,}", card=card),
        reply_markup=kb,
        parse_mode="HTML"
    )

@router.callback_query(F.data == "check_narozat_payment")
async def check_narozat_payment_cb(callback: CallbackQuery):
    lang = await get_user_lang(callback.from_user.id)
    await callback.answer()

    pending = await get_pending_payment(callback.from_user.id, payment_type="narozat")
    if not pending:
        return await callback.message.answer(t("no_pending", lang))

    status = await check_payment(pending["tx_id"])
    if status == "paid":
        await record_payment(callback.from_user.id, pending["tx_id"], pending["amount"], payment_type="narozat")
        await delete_pending_payment(callback.from_user.id, payment_type="narozat")
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(t("narozat_payment_success", lang))
    elif status == "cancelled":
        await delete_pending_payment(callback.from_user.id, payment_type="narozat")
        await callback.message.answer(t("payment_cancelled", lang))
    elif status == "pending":
        await callback.message.answer(t("payment_pending", lang))
    else:
        await callback.message.answer(t("payment_error", lang))

@router.message(F.document)
async def handle_user_document(message: Message, state: FSMContext):
    if not await check_auth(message, state):
        return 
        
    lang = await get_user_lang(message.from_user.id)
    file_name = message.document.file_name.lower()
    
    await state.clear()
    
    if file_name.endswith('.txt'):
        await message.answer(t("clean_file_wait", lang))
        
        file = await message.bot.get_file(message.document.file_id)
        path = os.path.join("data", "tests", f"up_{message.from_user.id}.txt")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        await message.bot.download_file(file.file_path, path)
        
        raw = parse_test_txt_file(path)
        os.remove(path)
        clean = filter_questions_by_answer_rule(clean_and_format_questions(raw))
        
        if not clean:
            return await message.answer(t("err_empty_file", lang))
            
        random.shuffle(clean)
        final_q = clean[:25] 
        
        for q in final_q:
            shuffle_question_options(q)
            
        await message.answer(t("ai_analyzing", lang))
        final_q = await enrich_questions_with_explanations(final_q, lang)
        
        await state.update_data(
            questions=final_q, 
            current_index=0, 
            score=0, 
            test_source="file", 
            export_source_name=os.path.splitext(message.document.file_name)[0],
            export_results_file=True,
            selected_indices=[],
            open_question_indices=build_mixed_open_indices(len(final_q)),
        )
        
        await message.answer(f"📊 {len(clean)} {t('quiz_starting', lang)}...")
        await send_test_as_messages(message, get_test_as_text(final_q))
        
        await asyncio.sleep(1)
        await send_next_question(message, state)
    else:
        if lang == "ru":
            err_msg = "❌ Ошибка! Бот принимает тесты СТРОГО в формате .txt. Пожалуйста, пересохраните ваш файл."
        elif lang == "uz":
            err_msg = "❌ Xato! Bot testlarni QAT'IY .txt formatida qabul qiladi. Iltimos, faylingizni qayta saqlang."
        else:
            err_msg = "❌ Error! The bot accepts tests STRICTLY in .txt format. Please resave your file."
            
        await message.answer(clean_text_output(err_msg))

async def send_open_question(message: Message, state: FSMContext):
    data = await state.get_data()
    q_list = data.get('questions', [])
    idx = data.get('current_index', 0)
    lang = await get_user_lang(message.chat.id)

    if idx >= len(q_list):
        return await finish_quiz(message, state)

    q = q_list[idx]
    open_question = _rewrite_question_for_open_mode(q.get('question', ''), lang)
    txt = f"✏️ {idx+1}/{len(q_list)}\n\n{open_question}\n\n{t('open_type_hint', lang)}"
    await state.set_state(TmaState.in_open_quiz)
    image_path = q.get("image_path")
    if image_path and os.path.exists(image_path):
        try:
            await message.answer_photo(FSInputFile(image_path))
        except Exception:
            pass
    await message.answer(clean_text_output(txt), reply_markup=get_cancel_keyboard(lang))

async def send_next_question(message: Message, state: FSMContext):
    data = await state.get_data()
    idx = data.get("current_index", 0)
    if is_open_question(data, idx):
        await send_open_question(message, state)
    else:
        await send_question(message, state)


@router.message(TmaState.in_open_quiz, F.text)
async def check_open_quiz_answer(message: Message, state: FSMContext):
    if await handle_menu_interrupt(message, state):
        return

    lang = await get_user_lang(message.from_user.id)
    if message.text in [t("back", "ru"), t("back", "en"), t("back", "uz"), t("back", lang)]:
        return await back_handler(message, state)

    data = await state.get_data()
    q_list = data.get('questions', [])
    idx = data.get('current_index', 0)
    q = q_list[idx]

    # Uploaded admin open tests keep accepted answers hidden from the user.
    accepted_answers = [str(item).strip() for item in q.get("accepted_answers", []) if str(item).strip()]
    reveal_correct_answer = True
    if q.get("question_type") == "open":
        correct_options = accepted_answers
        correct_answer = "; ".join(accepted_answers)
        reveal_correct_answer = False
    else:
        correct_indices = q.get('correct_indices', [0])
        options = q.get('options', [])
        correct_options = [options[i] for i in correct_indices if 0 <= i < len(options)]
        correct_answer = "; ".join(options[i] for i in correct_indices if 0 <= i < len(options))
        if not correct_answer:
            correct_answer = q.get('explanation', '')
    
    user_norm = _normalize_open_answer_text(message.text)
    correct_norm_set = {_normalize_open_answer_text(opt) for opt in correct_options}

    feedback = ""
    open_question = _rewrite_question_for_open_mode(q.get('question', ''), lang)
    if user_norm and user_norm in correct_norm_set:
        is_correct = True
    else:
        status_msg = await message.answer(t("checking_answer", lang))
        is_correct, feedback = await check_open_answer(
            question=open_question,
            correct_answer=correct_answer,
            user_answer=message.text,
            lang=lang,
        )
        await status_msg.delete()

    result_text = t("open_correct", lang) if is_correct else t("open_wrong", lang)
    if feedback:
        result_text += f"\n\n🎓 {clean_text_output(feedback)}"
    if not is_correct and correct_answer and reveal_correct_answer:
        result_text += f"\n\n{t('open_correct_was', lang)} {correct_answer}"

    await message.answer(result_text)
    await state.update_data(
        score=data['score'] + (1 if is_correct else 0),
        current_index=idx + 1,
    )
    await asyncio.sleep(1)
    await send_next_question(message, state)


async def send_question(message: Message, state: FSMContext):
    data = await state.get_data()
    q_list = data.get('questions', [])
    idx = data.get('current_index', 0)
    
    user_id = message.chat.id
    lang = await get_user_lang(user_id)
    
    if idx >= len(q_list):
        return await finish_quiz(message, state)
        
    q = q_list[idx]

    selected_indices = set(data.get("selected_indices", []))

    # Build message: question + options as text
    opts_text = "\n".join(
        f"{chr(ord('A') + i) if 0 <= i < 26 else str(i + 1)}) {opt}" for i, opt in enumerate(q['options'])
    )
    txt = f"❓ {idx+1}/{len(q_list)}\n\n{q['question']}\n\n{opts_text}"
    if is_multi_answer_question(q):
        txt += f"\n\n{t('multi_ans_hint', lang).format(n=get_multi_answer_count(q))}"

    await state.set_state(TmaState.in_quiz)

    image_path = q.get("image_path")
    if image_path and os.path.exists(image_path):
        try:
            await message.answer_photo(FSInputFile(image_path))
        except Exception:
            pass

    try:
        await message.answer(clean_text_output(txt), reply_markup=build_answer_keyboard(q, selected_indices, lang))
    except Exception:
        pass

@router.callback_query(F.data.startswith("ans_"))
async def check_answer(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    
    if 'questions' not in data:
        await callback.answer("⏳ Этот тест устарел или бот был перезапущен. Пожалуйста, запустите новый тест.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return
        
    q = data['questions'][data['current_index']]
    
    user_id = callback.message.chat.id
    lang = await get_user_lang(user_id)
    
    action = callback.data.split("_")[1]
    if action == "submit":
        if not is_multi_answer_question(q):
            await callback.answer()
            return
        explanation = clean_text_output(q.get("explanation", ""))
        selected_indices = set(data.get("selected_indices", []))
        correct_indices = set(q.get("correct_indices", []))
        is_correct = selected_indices == correct_indices

        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(f"{t('correct', lang) if is_correct else t('wrong', lang)}\n\n🎓 {explanation}")

        await state.update_data(
            score=data["score"] + (1 if is_correct else 0),
            current_index=data["current_index"] + 1,
            selected_indices=[],
        )
        await callback.answer()
        await asyncio.sleep(1)
        await send_next_question(callback.message, state)
        return

    try:
        idx = int(action)
    except ValueError:
        await callback.answer()
        return

    selected_indices = set(data.get("selected_indices", []))
    max_allowed = get_multi_answer_count(q) if is_multi_answer_question(q) else 1
    if idx in selected_indices:
        selected_indices.remove(idx)
    else:
        if len(selected_indices) >= max_allowed:
            await callback.answer(t("select_limit_hint", lang), show_alert=False)
            return
        selected_indices.add(idx)

    if not is_multi_answer_question(q):
        explanation = clean_text_output(q.get("explanation", ""))
        correct_indices = set(q.get("correct_indices", []))
        is_correct = selected_indices == correct_indices

        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(f"{t('correct', lang) if is_correct else t('wrong', lang)}\n\n🎓 {explanation}")

        await state.update_data(
            score=data["score"] + (1 if is_correct else 0),
            current_index=data["current_index"] + 1,
            selected_indices=[],
        )
        await callback.answer()
        await asyncio.sleep(1)
        await send_next_question(callback.message, state)
        return

    await state.update_data(selected_indices=sorted(selected_indices))
    await callback.message.edit_reply_markup(reply_markup=build_answer_keyboard(q, selected_indices, lang))
    await callback.answer()

async def finish_quiz(message: Message, state: FSMContext):
    data = await state.get_data()
    
    user_id = message.chat.id
    lang = await get_user_lang(user_id)
    
    score = data['score']
    total = len(data['questions'])
    percent = int((score/total)*100) if total else 0
    grade_key = get_grade_key(percent)
    _, icon = calculate_grade(percent)
    
    await message.answer(
        f"{t('finished', lang)}\n{t('result', lang)} {score}/{total} ({percent}%)\n{icon} {t('grade_label', lang)} {t(grade_key, lang)}", 
        reply_markup=get_main_keyboard(lang, user_id)
    )
    
    if should_send_quiz_export(data):
        os.makedirs(os.path.join("data", "tests"), exist_ok=True)
        path = os.path.join("data", "tests", f"tmp_result_{user_id}.txt")
        create_test_txt_file(data['questions'], path)
        
        try:
            await message.answer_document(
                FSInputFile(path, filename=build_quiz_export_filename(data, lang)),
                caption=t("download_caption", lang),
            )
        except Exception:
            pass
        finally: 
            if os.path.exists(path):
                os.remove(path)
            
    await state.clear()

@router.message(F.text)
async def smart_assistant_handler(message: Message, state: FSMContext):
    if await state.get_state():
        return 
        
    if not await check_auth(message, state):
        return 

    course = await get_user_course(message.from_user.id)
    text = message.text.lower()
    lang = await get_user_lang(message.from_user.id)
    
    triggers = ["нарисуй", "изобрази", "покажи", "draw", "generate image", "chiz", "rasmini yarat"]
    if any(text.startswith(trigger) for trigger in triggers):
        clean_prompt = message.text
        for trigger in triggers:
            clean_prompt = clean_prompt.replace(trigger, "", 1)
            
        clean_prompt = clean_prompt.strip()
        
        if len(clean_prompt) < 3:
            return await message.answer(t("drawing_fail", lang) + " (Уточните запрос)")
            
        status_msg = await message.answer(f"🎨 {t('drawing_start', lang)}")
        await message.bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
        
        image_path = await generate_image_async(clean_prompt, message.from_user.id)
        await status_msg.delete()
        
        if image_path and os.path.exists(image_path):
            try:
                await message.answer_photo(FSInputFile(image_path), caption=f"🖼 По запросу: {clean_prompt}")
            except Exception:
                await message.answer(t("drawing_fail", lang))
            finally:
                if os.path.exists(image_path):
                    os.remove(image_path)
        else:
            await message.answer(t("drawing_fail", lang))
        return

    excluded_triggers = [
        "📁 решить свой тест", "📁 solve custom test", "📁 o'z testingizni yechish", 
        "📁 загрузить тест", "загрузить тест", "решить свой тест"
    ]
    if any(w in text for w in ["тест", "quiz", "test", "экзамен"]) and not any(ext in text for ext in excluded_triggers):
        await state.update_data(topic=message.text)
        await update_last_topic(message.from_user.id, message.text)
        await message.answer(t("enter_quiz_count", lang), reply_markup=get_cancel_keyboard(lang))
        await state.set_state(TmaState.waiting_for_quiz_count)
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    await update_last_topic(message.from_user.id, message.text)
    
    if len(text.split()) > 2:
        book_context = await search_knowledge_base(text)
        response = await get_collaborative_response(message.text, course, lang, book_context)
    else:
        response = await get_chat_response(message.text, course, lang)
        
    response = clean_text_output(response)
    
    pdf_path = await asyncio.to_thread(generate_theory_pdf, response, message.from_user.id, message.text, lang)
    
    if pdf_path and os.path.exists(pdf_path):
        await message.answer_document(
            FSInputFile(pdf_path, filename=build_pdf_export_filename(message.text, lang, kind="answer")),
            caption=t("answer_pdf_caption", lang),
            reply_markup=get_main_keyboard(lang, message.from_user.id),
        )
        os.remove(pdf_path)
    else:
        await send_safe_message(message, response, reply_markup=get_main_keyboard(lang, message.from_user.id))
    
    images = await find_study_images(text, lang=lang, count=1)
    if images:
         try:
             await message.answer_photo(images[0])
         except Exception:
             pass
             
    await message.answer(t("fb_request", lang), reply_markup=get_feedback_keyboard(lang))
    if not await has_active_narozat_access(callback.from_user.id):
        await callback.message.answer(
            t("narozat_required", lang),
            reply_markup=_narozat_kb(lang),
        )
        await callback.answer()
        return
