import re
from typing import List, Dict

class RAGEngine:
    CHUNK_SIZE = 1000  # Символов в чанке
    OVERLAP = 100      # Перекрытие

    @staticmethod
    def clean_text(text: str) -> str:
        """Удаляет лишние пробелы"""
        if not text: return ""
        return re.sub(r'\s+', ' ', text).strip()

    @classmethod
    def create_chunks(cls, text: str, source_name: str) -> List[Dict]:
        """Разбивает текст на куски"""
        text = cls.clean_text(text)
        chunks = []
        if not text: return []
        
        for i in range(0, len(text), cls.CHUNK_SIZE - cls.OVERLAP):
            chunk_text = text[i:i + cls.CHUNK_SIZE]
            chunks.append({
                "text": chunk_text,
                "source": source_name,
                "id": i // (cls.CHUNK_SIZE - cls.OVERLAP)
            })
        return chunks

    @staticmethod
    def rank_chunks(query: str, chunks: List[Dict], top_n: int = 5) -> List[Dict]:
        """Находит лучшие куски текста по запросу"""
        query_words = set(re.findall(r'\w+', query.lower()))
        if not query_words or not chunks:
            return chunks[:top_n]

        scored_chunks = []
        for chunk in chunks:
            chunk_words = set(re.findall(r'\w+', chunk['text'].lower()))
            score = len(query_words.intersection(chunk_words))
            if score > 0:
                scored_chunks.append((score, chunk))

        scored_chunks.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored_chunks[:top_n]]

    @staticmethod
    def format_context(chunks: List[Dict]) -> str:
        """Собирает текст для ИИ"""
        formatted = ""
        for chunk in chunks:
            safe_text = chunk['text'].replace("{", "{{").replace("}", "}}")
            formatted += f"[SOURCE: {chunk['source']}]\n...{safe_text}...\n\n"
        return formatted