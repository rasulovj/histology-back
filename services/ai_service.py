import asyncio
import json
import re
from typing import Optional
import aiohttp
from config import DEEPSEEK_KEY

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

AI_SEMAPHORE = None

def _get_semaphore():
    global AI_SEMAPHORE
    if AI_SEMAPHORE is None:
        AI_SEMAPHORE = asyncio.Semaphore(50)
    return AI_SEMAPHORE


UZ_CYR_TO_LAT = {
    "А": "A", "а": "a",
    "Б": "B", "б": "b",
    "В": "V", "в": "v",
    "Г": "G", "г": "g",
    "Д": "D", "д": "d",
    "Е": "E", "е": "e",
    "Ё": "Yo", "ё": "yo",
    "Ж": "J", "ж": "j",
    "З": "Z", "з": "z",
    "И": "I", "и": "i",
    "Й": "Y", "й": "y",
    "К": "K", "к": "k",
    "Л": "L", "л": "l",
    "М": "M", "м": "m",
    "Н": "N", "н": "n",
    "О": "O", "о": "o",
    "П": "P", "п": "p",
    "Р": "R", "р": "r",
    "С": "S", "с": "s",
    "Т": "T", "т": "t",
    "У": "U", "у": "u",
    "Ф": "F", "ф": "f",
    "Х": "X", "х": "x",
    "Ц": "Ts", "ц": "ts",
    "Ч": "Ch", "ч": "ch",
    "Ш": "Sh", "ш": "sh",
    "Ъ": "", "ъ": "",
    "Ь": "", "ь": "",
    "Э": "E", "э": "e",
    "Ю": "Yu", "ю": "yu",
    "Я": "Ya", "я": "ya",
    "Ў": "O'", "ў": "o'",
    "Қ": "Q", "қ": "q",
    "Ғ": "G'", "ғ": "g'",
    "Ҳ": "H", "ҳ": "h",
}

LATIN_TO_CYR_CONFUSABLE = str.maketrans({
    "A": "А", "a": "а",
    "B": "В", "C": "С", "c": "с",
    "E": "Е", "e": "е",
    "H": "Н", "K": "К", "k": "к",
    "M": "М", "O": "О", "o": "о",
    "P": "Р", "p": "р",
    "T": "Т", "X": "Х", "x": "х",
    "Y": "У", "y": "у",
})


def _get_script_rule(lang_code: str) -> str:
    if lang_code == "uz":
        return (
            "КРИТИЧЕСКОЕ ПРАВИЛО ПИСЬМА: если язык ответа узбекский, "
            "пиши ТОЛЬКО на узбекской латинице. НИКОГДА не используй кириллицу."
        )
    if lang_code == "ru":
        return (
            "КРИТИЧЕСКОЕ ПРАВИЛО ПИСЬМА: если язык ответа русский, "
            "пиши ТОЛЬКО кириллицей. НИКОГДА не используй латиницу."
        )
    return ""


def _uz_cyrillic_to_latin(text: str) -> str:
    return "".join(UZ_CYR_TO_LAT.get(ch, ch) for ch in text)


def _ru_fix_mixed_script_word(match: re.Match) -> str:
    word = match.group(0)
    if re.search(r"[А-Яа-яЁё]", word) and re.search(r"[A-Za-z]", word):
        return word.translate(LATIN_TO_CYR_CONFUSABLE)
    return word


def _normalize_text_by_lang(text: str, lang_code: Optional[str]) -> str:
    if not text or not lang_code:
        return text
    if lang_code == "uz":
        return _uz_cyrillic_to_latin(text)
    if lang_code == "ru":
        return re.sub(r"\b[\w'-]+\b", _ru_fix_mixed_script_word, text)
    return text


def _normalize_data_by_lang(value, lang_code: str):
    if isinstance(value, str):
        return _normalize_text_by_lang(value, lang_code)
    if isinstance(value, list):
        return [_normalize_data_by_lang(item, lang_code) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_data_by_lang(val, lang_code) for key, val in value.items()}
    return value


def get_tutor_system_prompt(lang_code):
    langs = {
        'ru': "РУССКИЙ ЯЗЫК (Russian)",
        'en': "АНГЛИЙСКИЙ ЯЗЫК (English)",
        'uz': "УЗБЕКСКИЙ ЯЗЫК (O'zbek tili)"
    }
    target_lang = langs.get(lang_code, "РУССКИЙ ЯЗЫК")

    return f"""
РОЛЬ: Ты — Профессор-гистолог и медицинский ассистент AI Study Assistant.
УРОВЕНЬ: Строго академический, для студентов высших медицинских учебных заведений.

ПРАВИЛО ПО ИСТОЧНИКАМ:
- Используй текст из раздела "ИСТОЧНИК ИЗ БАЗЫ ЗНАНИЙ" как основу ответа.
- Если база содержит информацию по теме — ОБЯЗАТЕЛЬНО дай развёрнутый ответ, синтезируя и объясняя всё найденное. Не требуй дословного определения — объясняй из контекста.
- Только если база ПОЛНОСТЬЮ пуста или содержит материалы совершенно не по теме — сообщи об этом.
- Ты НЕ являешься общим ассистентом. Отвечай ТОЛЬКО на вопросы по медицине и гистологии.

САМОЕ ВАЖНОЕ ПРАВИЛО ЯЗЫКА: Ты ОБЯЗАН отвечать строго на языке: {target_lang}. Если предоставленный текст на другом языке, переведи его на {target_lang}.
{_get_script_rule(lang_code)}

ПРАВИЛА ОФОРМЛЕНИЯ:
1. НИКАКОГО ЖИРНОГО ШРИФТА И ЗВЕЗДОЧЕК (*). Текст должен быть абсолютно чистым.
2. ТЕРМИНЫ: если язык ответа не русский, указывай латинские названия анатомических структур в скобках. Если язык ответа русский, не используй латиницу вообще.
3. Разделяй текст на смысловые абзацы пустой строкой.
4. Используй четкие списки с эмодзи 🔹 или цифрами.
"""


def _clean_text_brutal(text, lang_code=None):
    if not text:
        return ""
    text = text.replace("*", "")
    text = re.sub(r'"([^"]{1,50})"', r'\1', text)
    text = _normalize_text_by_lang(text, lang_code)
    return text.strip()


async def _ask_ai(prompt: str):
    """Call DeepSeek. Returns text or None on failure."""
    if not DEEPSEEK_KEY:
        print("❌ DeepSeek unavailable — DEEPSEEK_KEY is missing in .env")
        return None

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }

    try:
        async with _get_semaphore():
            timeout = aiohttp.ClientTimeout(total=90)
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_KEY}",
                "Content-Type": "application/json",
            }
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(DEEPSEEK_API_URL, headers=headers, json=payload) as response:
                    response_text = await response.text()
                    if response.status >= 400:
                        print(f"❌ DeepSeek HTTP {response.status}: {response_text[:500]}")
                        return None

        data = json.loads(response_text)
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"❌ DeepSeek error: {e}")
        return None


async def get_russian_keywords(query: str) -> str:
    """Translate any-language medical query to Russian keywords for KB search."""
    prompt = (
        "You are a medical translator. Extract the key medical terms from the query below "
        "and return them translated to Russian, space-separated, lowercase. "
        "Return ONLY the Russian keywords, nothing else.\n"
        f"Query: {query}"
    )
    res = await _ask_ai(prompt)
    return res.strip() if res else ""


async def is_medical_topic(query: str) -> bool:
    """Returns True if the query is related to medicine/histology/biology."""
    prompt = (
        "You are a topic classifier. Answer ONLY with 'YES' or 'NO'.\n"
        "Is the following question related to medicine, histology, anatomy, biology, "
        "physiology, pharmacology, or any medical/health science topic?\n"
        f"Question: {query}\n"
        "Answer (YES or NO):"
    )
    res = await _ask_ai(prompt)
    if res:
        return "yes" in res.strip().lower()
    return True  # default allow if classifier fails


async def classify_book_topic(text_snippet):
    prompt = f"Analyze medical text. Choose exactly ONE category: Анатомия, Гистология, Биология, Биохимия, Физиология, Патофизиология, Фармакология, Хирургия, Терапия. If not match, reply: Разное.\nTEXT: {text_snippet[:2000]}"
    res = await _ask_ai(prompt)
    if not res:
        return "Разное"
    clean_res = res.strip().capitalize()
    cats = ["Анатомия", "Гистология", "Биология", "Биохимия", "Физиология", "Патофизиология", "Фармакология", "Хирургия", "Терапия"]
    for c in cats:
        if c.lower() in clean_res.lower():
            return c
    return "Разное"


async def get_collaborative_response(user_query, course_level, lang, context=""):
    system_prompt = get_tutor_system_prompt(lang)

    if context.strip():
        source_block = f"=== ИСТОЧНИК ИЗ БАЗЫ ЗНАНИЙ ===\n{context}"
    else:
        source_block = (
            "=== ИСТОЧНИК ИЗ БАЗЫ ЗНАНИЙ ===\n"
            "[БАЗА ЗНАНИЙ ПУСТА — по данной теме материалов не найдено]"
        )

    full_prompt = (
        f"{system_prompt}\n\n"
        f"{source_block}\n\n"
        f"=== ВОПРОС СТУДЕНТА {course_level} КУРСА ===\n{user_query}\n\n"
        "ЗАДАЧА: Дай максимально подробный ответ на основе предоставленных материалов. "
        "Синтезируй информацию из всех найденных фрагментов. "
        "Сообщи об отсутствии данных ТОЛЬКО если база полностью пуста."
    )
    res = await _ask_ai(full_prompt)
    if res:
        return _clean_text_brutal(res, lang)
    return "❌ Ошибка / Xato / Error."


async def get_chat_response(query, course, lang):
    system_prompt = get_tutor_system_prompt(lang)
    prompt = f"{system_prompt}\nВопрос пользователя: {query}"
    res = await _ask_ai(prompt)
    if res:
        return _clean_text_brutal(res, lang)
    return "..."


async def check_open_answer(question: str, correct_answer: str, user_answer: str, lang: str):
    """Check a free-text answer against the correct answer. Returns (is_correct, feedback)."""
    langs = {'ru': 'Russian', 'en': 'English', 'uz': "Uzbek (O'zbek tili)"}
    target_lang = langs.get(lang, 'Russian')
    prompt = (
        f"You are a medical professor grading a student's free-text answer. "
        f"Respond in {target_lang}.\n\n"
        f"{_get_script_rule(lang)}\n\n"
        f"Question: {question}\n"
        f"Correct answer: {correct_answer}\n"
        f"Student's answer: {user_answer}\n\n"
        "Is the student's answer correct or substantially correct? "
        "Reply with a JSON object: {\"correct\": true/false, \"feedback\": \"short explanation\"}"
    )
    res = await _ask_ai(prompt)
    if res:
        try:
            text = res.replace("```json", "").replace("```", "").strip()
            start = text.find('{')
            end = text.rfind('}') + 1
            obj = json.loads(text[start:end])
            obj = _normalize_data_by_lang(obj, lang)
            return bool(obj.get("correct")), obj.get("feedback", "")
        except Exception:
            pass
    return False, ""


async def get_ai_quiz_response(topic, context, num_questions, course, lang):
    langs = {'ru': 'Russian', 'en': 'English', 'uz': "Uzbek (O'zbek tili)"}
    target_lang = langs.get(lang, 'Russian')
    multi_count = max(1, int(round(num_questions * 0.3))) if num_questions >= 2 else 0
    prompt = (
        f"Act as Medical Professor. THE OUTPUT LANGUAGE MUST BE STRICTLY {target_lang}. "
        f"{_get_script_rule(lang)} "
        f"SOURCE: {context[:3000]}\n"
        f"Task: Generate {num_questions} medical quiz questions on '{topic}'. "
        "RETURN JSON ARRAY ONLY.\n"
        "Rules:\n"
        "- Every question must be CLOSED multiple-choice (no open questions).\n"
        "- Each question must have exactly 4 options.\n"
        f"- At least {multi_count} questions must have 2 or 3 correct answers.\n"
        "- Remaining questions must have exactly 1 correct answer.\n"
        "- 'correct_indices' MUST be a list of integers (0-based).\n"
        "Format example: "
        "[{ 'question': '...', 'options': ['A','B','C','D'], 'correct_indices': [1,3], 'explanation': '...' }]"
    )
    res = await _ask_ai(prompt)
    if not res:
        return []
    try:
        text = res.replace("```json", "").replace("```", "").strip()
        start = text.find('[')
        end = text.rfind(']') + 1
        if start != -1 and end != -1:
            return _normalize_data_by_lang(json.loads(text[start:end]), lang)
    except Exception:
        pass
    return []


async def translate_broadcast_message(text):
    prompt = (
        "Переведи текст на английский и узбекский. "
        "Русская версия должна быть только на кириллице. "
        "Узбекская версия должна быть только на латинице, без кириллицы. "
        f"Верни JSON: {{\"ru\": \"{text}\", \"en\": \"...\", \"uz\": \"...\"}}. Текст: {text}"
    )
    res = await _ask_ai(prompt)
    if res:
        try:
            clean_json = res.replace("```json", "").replace("```", "").strip()
            data = json.loads(clean_json)
            data["ru"] = _normalize_text_by_lang(data.get("ru", text), "ru")
            data["uz"] = _normalize_text_by_lang(data.get("uz", text), "uz")
            return data
        except Exception:
            pass
    return {"ru": text, "en": text, "uz": text}


async def translate_name_multilang(text: str):
    source = (text or "").strip()
    if not source:
        return {"ru": "", "en": "", "uz": ""}

    prompt = (
        "Translate the given title/category into Russian, English, and Uzbek. "
        "Keep it concise and domain-appropriate for medical education materials. "
        "Russian output must be in Cyrillic. Uzbek output must be in Latin script. "
        "Detect the source language as one of: ru, en, uz, or other. "
        "IMPORTANT: for the detected source language, keep the text EXACTLY as provided "
        "(no paraphrasing, no synonym replacement, no expansion). "
        "Return JSON ONLY in this exact shape: "
        "{\"source_lang\":\"ru|en|uz|other\",\"ru\":\"...\",\"en\":\"...\",\"uz\":\"...\"}. "
        f"Input text: {source}"
    )
    res = await _ask_ai(prompt)
    if res:
        try:
            clean_json = res.replace("```json", "").replace("```", "").strip()
            start = clean_json.find("{")
            end = clean_json.rfind("}") + 1
            data = json.loads(clean_json[start:end])
            result = {
                "ru": _normalize_text_by_lang(str(data.get("ru", source)), "ru"),
                "en": str(data.get("en", source)).strip() or source,
                "uz": _normalize_text_by_lang(str(data.get("uz", source)), "uz"),
            }
            source_lang = str(data.get("source_lang", "other")).strip().lower()
            if source_lang in {"ru", "en", "uz"}:
                # Preserve the original admin-entered wording for its native language.
                result[source_lang] = source
            return result
        except Exception:
            pass
    return {"ru": source, "en": source, "uz": source}
