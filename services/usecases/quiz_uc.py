from services.ai_service import get_ai_quiz_response
from services.rag_service import search_knowledge_base
from services.quiz_service import normalize_correct_indices

class QuizUseCase:
    def __init__(self, user_id, course, lang):
        self.user_id = user_id
        self.course = course
        self.lang = lang

    async def execute(self, topic, num_questions):
        context = await search_knowledge_base(topic)

        max_attempts = 2
        for _ in range(max_attempts):
            questions = await get_ai_quiz_response(
                topic=topic,
                context=context,
                num_questions=num_questions,
                course=self.course,
                lang=self.lang
            )
            normalized = self._normalize_questions(questions)
            if normalized and self._has_multi_correct(normalized):
                return normalized

        # Return best-effort normalized result even if AI failed to include multi-correct.
        return self._normalize_questions(questions)

    def _has_multi_correct(self, questions):
        return any(len(q.get("correct_indices", [])) == 2 for q in questions)

    def _normalize_questions(self, questions):
        if not questions or not isinstance(questions, list):
            return None

        normalized = []
        for i, q in enumerate(questions):
            if not isinstance(q, dict):
                continue

            question_text = str(q.get("question", "")).strip() or f"Question {i + 1}"
            options = q.get("options", [])
            if not isinstance(options, list):
                options = []
            options = [str(opt).strip() for opt in options if str(opt).strip()]

            if len(options) < 2:
                continue

            raw_indices = q.get("correct_indices")
            if raw_indices is None and "correct_index" in q:
                raw_indices = [q.get("correct_index")]
            if not isinstance(raw_indices, list):
                raw_indices = [raw_indices]
            correct_indices = normalize_correct_indices(raw_indices, len(options))
            if len(correct_indices) not in (1, 2):
                continue

            normalized.append({
                "question": question_text,
                "options": options,
                "correct_indices": correct_indices,
                "explanation": str(q.get("explanation", "")).strip(),
            })

        return normalized or None
