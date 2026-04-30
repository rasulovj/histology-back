import json
from typing import Any


class ControlTestParseError(ValueError):
    pass


def parse_control_test_text(content: str) -> list[dict[str, Any]]:
    """
    Parse admin control-test text format:
      # Question text
      + correct option
      - wrong option
      * accepted open answer
    """
    if not isinstance(content, str) or not content.strip():
        raise ControlTestParseError("File is empty")

    content = content.lstrip("\ufeff")

    questions: list[dict[str, Any]] = []
    current_question: dict[str, Any] | None = None

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("#"):
            # Finalize previous block before opening a new question.
            if current_question is not None:
                _validate_question_block(current_question)
                questions.append(current_question)

            question_text = line[1:].strip()
            if not question_text:
                raise ControlTestParseError("Question text cannot be empty after '#'")

            current_question = {
                "question": question_text,
                "options": [],
                "correct_indices": [],
                "accepted_answers": [],
            }
            continue

        if line.startswith("+") or line.startswith("-"):
            if current_question is None:
                raise ControlTestParseError("Option found before first question header")
            if current_question["accepted_answers"]:
                raise ControlTestParseError("Open answers '*' cannot be mixed with '+/-' options")

            option_text = line[1:].strip()
            if not option_text:
                raise ControlTestParseError("Option text cannot be empty")

            option_index = len(current_question["options"])
            current_question["options"].append(option_text)
            if line.startswith("+"):
                current_question["correct_indices"].append(option_index)
            continue

        if line.startswith("*"):
            if current_question is None:
                raise ControlTestParseError("Accepted answer found before first question header")
            if current_question["options"]:
                raise ControlTestParseError("Accepted answers '*' cannot be mixed with '+/-' options")

            answer_text = line[1:].strip()
            if not answer_text:
                raise ControlTestParseError("Accepted answer text cannot be empty")

            if answer_text not in current_question["accepted_answers"]:
                current_question["accepted_answers"].append(answer_text)
            continue

        raise ControlTestParseError(
            "Invalid line format. Expected '#', '+', '-', '*' or empty line."
        )

    if current_question is not None:
        _validate_question_block(current_question)
        questions.append(current_question)

    if not questions:
        raise ControlTestParseError("No valid questions found in file")

    return questions


def serialize_options(options: list[str]) -> str:
    return json.dumps(options, ensure_ascii=False)


def serialize_correct_indices(indices: list[int]) -> str:
    return json.dumps(indices, ensure_ascii=False)


def deserialize_options(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def deserialize_correct_indices(value: str) -> list[int]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    result: list[int] = []
    for item in parsed:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _validate_question_block(question: dict[str, Any]) -> None:
    options = question.get("options", [])
    correct_indices = question.get("correct_indices", [])
    accepted_answers = question.get("accepted_answers", [])

    if accepted_answers:
        if options or correct_indices:
            raise ControlTestParseError("Open questions cannot include '+/-' options")
        return

    if len(options) < 2:
        raise ControlTestParseError("Each multiple-choice question must have at least 2 options")
    if not correct_indices:
        raise ControlTestParseError("Each multiple-choice question must have at least 1 correct option")
