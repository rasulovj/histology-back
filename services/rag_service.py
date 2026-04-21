import os
import shutil
import asyncio
import json
from config import DB_PATH
from services.library_service import load_json, INDEX_FILE, KNOWLEDGE_MAP_FILE, CACHE_DIR

def add_pdf_to_db(temp_file_path, file_name):
    try:
        os.makedirs(DB_PATH, exist_ok=True)
        dest_path = os.path.join(DB_PATH, file_name)
        shutil.move(temp_file_path, dest_path)
        return True
    except Exception as e:
        print(f"❌ Ошибка добавления документа: {e}")
        return False

def _get_cached_pages_sync(book_id, page_indices):
    content = ""
    try:
        cache_path = os.path.join(CACHE_DIR, f"{book_id}.json")
        if not os.path.exists(cache_path):
            return ""
            
        with open(cache_path, 'r', encoding='utf-8') as f:
            chunks = json.load(f)
            
        for idx in page_indices:
            if idx < len(chunks):
                content += f"\n--- [Фрагмент {idx+1}] ---\n{chunks[idx]}\n"
    except Exception as e: 
        print(f"Ошибка извлечения текста из кэша: {e}")
    return content

async def search_knowledge_base(query):
    print(f"🔎 Анализирую запрос по локальной базе: '{query}'...")
    from services.ai_service import get_russian_keywords

    index = load_json(INDEX_FILE)
    k_map = load_json(KNOWLEDGE_MAP_FILE)

    # Translate query to Russian keywords so Uzbek/English queries match
    # Cyrillic-indexed book content
    ru_keywords_str = await get_russian_keywords(query)
    print(f"   🔤 Russian keywords: {ru_keywords_str}")

    # Combine original query words + translated Russian keywords for search
    combined = f"{query} {ru_keywords_str}"
    keywords = combined.lower().split()

    hits = {}

    for word in keywords:
        if len(word) < 4:
            continue

        for kw, locations in k_map.items():
            if word in kw.lower():
                for loc in locations:
                    bid = loc['b']
                    pid = loc['p']
                    if bid not in hits:
                        hits[bid] = set()
                    hits[bid].add(pid)
                    
    context_data = ""
    sorted_books = sorted(hits.items(), key=lambda item: len(item[1]), reverse=True)
    
    for book_id, pages in sorted_books[:5]:
        book_info = next((v for k, v in index.items() if v['id'] == book_id), None)
        if not book_info:
            continue
            
        title = book_info['title']
        top_pages = sorted(list(pages))[:6]
        
        text = await asyncio.to_thread(_get_cached_pages_sync, book_id, top_pages)
        if text.strip():
            context_data += f"\n\n=== ИСТОЧНИК: '{title}' ===\n{text}"
        
    if not context_data.strip(): 
        print("⚠️ В локальной базе ничего не найдено.")
        
    return context_data.strip()