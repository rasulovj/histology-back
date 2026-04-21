import os
import logging
from services.ai_service import _ask_ai

SYSTEM_PROMPT_DRAWING = """
Ты — профессиональный медицинский иллюстратор и программист SVG.
Твоя задача: Написать валидный XML код формата SVG (Scalable Vector Graphics), изображающий медицинскую схему или орган.

ТРЕБОВАНИЯ К ЧЕРТЕЖУ:
1. Визуализация: Используй простые фигуры (circle, rect, path, line) для схематичного изображения органа/процесса.
2. Цвета: Используй медицинские цвета (красный для артерий, синий для вен, желтый для нервов, розовый для тканей).
3. Фон: Белый или прозрачный.
4. Размеры: viewBox="0 0 500 500".

ТРЕБОВАНИЯ К ПОДПИСЯМ (Labels):
1. Обязательно подпиши ключевые элементы.
2. Язык подписей: ЛАТЫНЬ (обязательно) + Язык пользователя (в скобках).
   Пример: "Cor (Сердце)", "Vena Cava (Hollow vein)".
3. Шрифт: Arial, размер читаемый.

ФОРМАТ ВЫВОДА:
Верни ТОЛЬКО чистый XML код, начиная с <svg ...> и заканчивая </svg>.
Не пиши ```xml или ```svg. Не пиши вступлений.
"""

async def generate_medical_scheme(topic, lang="ru"):
    """
    Генерирует SVG-схему по запросу.
    Uses Gemini first, falls back to DeepSeek if quota exceeded.
    """
    prompt = f"""
    {SYSTEM_PROMPT_DRAWING}
    
    ЗАДАНИЕ: Начерти схему на тему "{topic}".
    Язык пользователя для перевода терминов: {lang}.
    """
    
    try:
        svg_code = await _ask_ai(prompt)
        
        if not svg_code:
            return None
        
        # Чистка кода от лишних символов Markdown, если ИИ их добавил
        if "```" in svg_code:
            svg_code = svg_code.replace("```xml", "").replace("```svg", "").replace("```", "")
            
        svg_code = svg_code.strip()
        
        # Проверка валидности
        if not svg_code.startswith("<svg") or not svg_code.endswith("</svg>"):
            return None
            
        return svg_code
        
    except Exception as e:
        logging.error(f"Drawing Error: {e}")
        return None

def save_svg_file(svg_code, user_id):
    """Сохраняет код в реальный файл"""
    directory = os.path.join("data", "drawings")
    os.makedirs(directory, exist_ok=True)
    
    filename = f"scheme_{user_id}.svg"
    path = os.path.join(directory, filename)
    
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg_code)
        
    return path