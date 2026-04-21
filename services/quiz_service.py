import json
import os
import re
import asyncio
import logging
from services.ai_service import _ask_ai

QUIZ_SEMAPHORE = None

def _get_quiz_semaphore():
    global QUIZ_SEMAPHORE
    if QUIZ_SEMAPHORE is None:
        QUIZ_SEMAPHORE = asyncio.Semaphore(3)
    return QUIZ_SEMAPHORE

async def generate_test_questions(topic, context="", num_questions=20, course_level=None, lang="ru"):
    """Генерация теста с нуля"""
    q_1 = int(num_questions * 0.40)
    q_2 = int(num_questions * 0.30)
    q_34 = int(num_questions * 0.20)
    q_5 = num_questions - (q_1 + q_2 + q_34) 

    lang_instruction = f"Output language: {lang}."
    
    prompt = f"""
    You are a Professor at AI Study Assistant.
    Generate a quiz of exactly {num_questions} questions on: "{topic}".
    {lang_instruction}
    
    STRICT DIFFICULTY DISTRIBUTION (New Grading System):
    1. {q_1} questions: EXACTLY 1 correct answer (Simple).
    2. {q_2} questions: EXACTLY 2 correct answers (Medium).
    3. {q_34} questions: 3 or 4 correct answers (Hard).
    4. {q_5} questions: 5 correct answers (Very Hard - Ensure at least 6-7 options provided).

    Course Level: {course_level if course_level else "General"}.
    Context: {context[:3000] if context else "Use general medical knowledge."}

    JSON FORMAT (Array of Objects):
    [
        {{
            "question": "Question text...",
            "options": ["A", "B", "C", "D", "E", "F"],
            "correct_indices": [0], 
            "explanation": "Why..."
        }}
    ]
    IMPORTANT: 'correct_indices' MUST be a LIST of integers (0-based index).
    """
    
    try:
        text = await _ask_ai(prompt)
        if not text:
            return []
        start_idx = text.find('[')
        end_idx = text.rfind(']') + 1
        if start_idx != -1 and end_idx != -1:
            clean_json = text[start_idx:end_idx]
        else:
            clean_json = text.replace("```json", "").replace("```", "").strip()

        questions = json.loads(clean_json)

        for q in questions:
            if "correct_index" in q:
                q["correct_indices"] = [q["correct_index"]]
            if "correct_indices" not in q:
                q["correct_indices"] = [0]

        return questions

    except Exception as e:
        logging.error(f"Generate Error: {e}")
        return []

async def enrich_questions_with_explanations(questions, lang="ru"):
    """
    Берет список загруженных вопросов и просит ИИ написать к ним объяснения.
    """
    if not questions: return []
    
    # Чтобы не перегружать ИИ, берем только текст вопроса и правильный ответ для контекста
    simplified_qs = []
    for q in questions:
        correct_opts = [q['options'][i] for i in q['correct_indices']]
        simplified_qs.append({
            "q": q['question'],
            "correct": correct_opts
        })
    
    data_str = json.dumps(simplified_qs, ensure_ascii=False)
    
    prompt = f"""
    You are a Medical Professor. A student uploaded these questions.
    Analyze them and provide a short, academic explanation for EACH question in {lang}.
    Explain WHY the correct answer is right and (briefly) why distractors might be wrong.

    Questions Data:
    {data_str}

    RETURN ONLY A JSON ARRAY OF STRINGS.
    Example: ["Explanation for Q1...", "Explanation for Q2..."]
    The array length must match the number of questions exactly.
    """
    
    try:
        text = await _ask_ai(prompt)
        if text:
            start_idx = text.find('[')
            end_idx = text.rfind(']') + 1
            if start_idx != -1 and end_idx != -1:
                explanations = json.loads(text[start_idx:end_idx])
                if len(explanations) == len(questions):
                    for i, expl in enumerate(explanations):
                        questions[i]['explanation'] = expl
    except Exception as e:
        logging.error(f"Enrichment Error: {e}")

    # Return questions as-is if enrichment failed
    return questions

def normalize_correct_indices(correct_indices, option_count):
    seen = set()
    normalized = []
    for idx in correct_indices or []:
        try:
            parsed = int(idx)
        except (TypeError, ValueError):
            continue
        if 0 <= parsed < option_count and parsed not in seen:
            seen.add(parsed)
            normalized.append(parsed)
    return normalized

def normalize_question_answer_rule(question):
    options = [str(opt).strip() for opt in question.get("options", []) if str(opt).strip()]
    question["options"] = options
    correct_indices = normalize_correct_indices(question.get("correct_indices", []), len(options))

    if len(correct_indices) == 0:
        return None
    if len(correct_indices) > 2:
        return None

    question["correct_indices"] = correct_indices
    return question

def filter_questions_by_answer_rule(questions):
    filtered = []
    for question in questions:
        normalized = normalize_question_answer_rule(question)
        if normalized is not None:
            filtered.append(normalized)
    return filtered

def get_test_as_text(questions):
    text_lines = []
    for q in questions:
        text_lines.append(f"# {q['question']}")
        correct_set = set(q.get('correct_indices', [0]))
        for idx, opt in enumerate(q['options']):
            prefix = "+ " if idx in correct_set else "- "
            clean_opt = re.sub(r'^[A-DА-Дa-dа-д]\.\s*', '', opt).strip()
            text_lines.append(f"{prefix}{clean_opt}")
        text_lines.append("")
    return "\n".join(text_lines).strip()

def create_test_txt_file(questions, file_path):
    with open(file_path, 'w', encoding='utf-8') as f:
        for q in questions:
            f.write(f"# {q['question']}\n")
            correct_indices = q.get('correct_indices', [])
            if not correct_indices and 'correct_index' in q:
                correct_indices = [q['correct_index']]
            correct_set = set(correct_indices)

            for idx, opt in enumerate(q['options']):
                prefix = "+ " if idx in correct_set else "- "
                clean_opt = re.sub(r'^[A-DА-Дa-dа-д]\.\s*', '', opt).strip()
                f.write(f"{prefix}{clean_opt}\n")
            f.write("\n")
    return file_path

def parse_test_txt_file(file_path):
    questions = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        current_q = None
        for line in lines:
            line = line.strip()
            if not line: continue
            
            if line.startswith('#'):
                if current_q and len(current_q['options']) > 0:
                    questions.append(current_q)
                
                current_q = {
                    "question": line[1:].strip(),
                    "options": [],
                    "correct_indices": [], 
                    "explanation": "Uploaded file (No explanation)." # Временно, потом ИИ заменит
                }
            elif line.startswith('+') and current_q is not None:
                current_q['correct_indices'].append(len(current_q['options']))
                current_q['options'].append(line[1:].strip())
            elif line.startswith('-') and current_q is not None:
                current_q['options'].append(line[1:].strip())
                
        if current_q and len(current_q['options']) > 0:
            questions.append(current_q)
    except Exception as e:
        logging.error(f"Parse error: {e}")
        
    return questions

def clean_and_format_questions(questions):
    seen = set()
    cleaned = []
    for q in questions:
        raw_text = re.sub(r'^\d+[\.\)]\s*', '', q['question']).strip()
        comp = raw_text.lower()
        if comp and comp not in seen:
            seen.add(comp)
            if raw_text: raw_text = raw_text[0].upper() + raw_text[1:]
            new_q = q.copy()
            new_q['question'] = raw_text
            cleaned.append(new_q)
    return cleaned
