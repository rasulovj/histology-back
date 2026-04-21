import aiohttp
import asyncio
import random
import os
import urllib.parse # 👈 Добавили для правильного кодирования ссылок

# Папка для временного сохранения сгенерированных картинок
TEMP_IMG_PATH = os.path.join("data", "temp_images")
os.makedirs(TEMP_IMG_PATH, exist_ok=True)

async def generate_image_async(prompt: str, user_id: int) -> str:
    """Генерирует изображение по промпту через внешний API."""
    
    enhanced_prompt = f"Medical illustration, anatomical drawing, detailed style: {prompt}"
    
    # 1. КОДИРУЕМ ПРОБЕЛЫ И СИМВОЛЫ (Без этого aiohttp на Linux падает)
    encoded_prompt = urllib.parse.quote(enhanced_prompt)
    
    api_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true&seed={random.randint(1, 100000)}"
    file_path = os.path.join(TEMP_IMG_PATH, f"gen_{user_id}_{random.randint(100, 999)}.jpg")

    # 2. МАСКИРУЕМСЯ ПОД БРАУЗЕР (Чтобы сервер нас не заблокировал)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        print(f"🎨 Начинаю генерацию изображения для запроса: '{prompt}'...")
        async with aiohttp.ClientSession() as session:
            # Передаем headers в запрос
            async with session.get(api_url, headers=headers, timeout=30) as response:
                if response.status == 200:
                    image_data = await response.read()
                    with open(file_path, 'wb') as f:
                        f.write(image_data)
                    print(f"✅ Изображение успешно сгенерировано: {file_path}")
                    return file_path
                else:
                    print(f"❌ Ошибка API генерации. Статус: {response.status}")
                    return None
    except asyncio.TimeoutError:
        print("❌ Превышено время ожидания генерации изображения.")
        return None
    except Exception as e:
        print(f"❌ Критическая ошибка генерации: {e}")
        return None