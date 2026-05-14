import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers.user_handlers import (
    _rewrite_question_for_open_mode,
    _build_image_search_query,
    _is_image_result_allowed,
    build_answer_keyboard,
    build_pdf_export_filename,
    build_quiz_export_filename,
    check_limit,
    get_registration_welcome_text,
    get_grade_key,
    is_open_question,
    is_multi_answer_question,
    prepare_control_test_questions,
    select_random_control_test_questions,
    select_control_test_questions,
    send_open_question,
    should_send_quiz_export,
    shuffle_question_options,
)
from services.quiz_service import filter_questions_by_answer_rule
from services.usecases.quiz_uc import QuizUseCase


class CheckLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_callback_flow_uses_explicit_user_id_for_premium_lookup(self):
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=999999),
            answer=AsyncMock(),
        )

        with (
            patch("handlers.user_handlers.get_user_premium_status", new=AsyncMock(return_value=1)) as premium_mock,
            patch("handlers.user_handlers.check_and_increment_requests", new=AsyncMock()) as limit_mock,
        ):
            allowed = await check_limit(message, "en", user_id=12345)

        self.assertTrue(allowed)
        premium_mock.assert_awaited_once_with(12345)
        limit_mock.assert_not_awaited()
        message.answer.assert_not_awaited()

    async def test_open_question_with_image_sends_photo_before_prompt(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            answer_photo=AsyncMock(),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={
                "questions": [
                    {
                        "question": "What is shown?",
                        "question_type": "open",
                        "accepted_answers": ["fibroblast"],
                        "image_path": "/tmp/test-open-image.jpg",
                    }
                ],
                "current_index": 0,
            }),
            set_state=AsyncMock(),
        )

        with (
            patch("handlers.user_handlers.get_user_lang", new=AsyncMock(return_value="en")),
            patch("handlers.user_handlers.get_cancel_keyboard", return_value=None),
            patch("handlers.user_handlers.os.path.exists", return_value=True),
        ):
            await send_open_question(message, state)

        message.answer_photo.assert_awaited_once()
        message.answer.assert_awaited_once()


class OpenQuestionRewriteTests(unittest.TestCase):
    def test_rewrites_uz_closed_question_without_qaysi_biri_phrasing(self):
        rewritten = _rewrite_question_for_open_mode(
            "Qaysi juftlik to'g'ri biriktiruvchi to'qimaning turlari hisoblanadi?",
            "uz",
        )

        self.assertEqual(
            rewritten,
            "To'g'ri biriktiruvchi to'qimaning turlari hisoblangan juftlikni yozing."
        )

    def test_rewrites_quyidagilardan_qaysi_biri_into_open_wording(self):
        rewritten = _rewrite_question_for_open_mode(
            "Quyidagilardan qaysi biri elastik tog'ayga kiradi?",
            "uz",
        )

        self.assertEqual(
            rewritten,
            "Elastik tog'ayga kiradigan to'g'ri javobni yozing."
        )


class QuizResultFormattingTests(unittest.TestCase):
    def test_localized_grade_key_for_failed_result(self):
        self.assertEqual(get_grade_key(0), "grade_fail")

    def test_control_tests_skip_txt_export(self):
        self.assertFalse(
            should_send_quiz_export({"test_source": "admin_test", "export_results_file": False})
        )

    def test_custom_tests_keep_txt_export(self):
        self.assertTrue(
            should_send_quiz_export({"test_source": "file", "export_results_file": True})
        )

    def test_builds_readable_export_filename_from_topic(self):
        filename = build_quiz_export_filename(
            {"topic": "To'g'ri biriktiruvchi to'qima"},
            "uz",
        )

        self.assertEqual(
            filename,
            "Test_natijalari_Togri_biriktiruvchi_toqima.txt",
        )

    def test_builds_readable_theory_pdf_filename_from_topic(self):
        filename = build_pdf_export_filename(
            "To'g'ri biriktiruvchi to'qima",
            "uz",
            kind="theory",
        )

        self.assertEqual(
            filename,
            "Nazariya_Togri_biriktiruvchi_toqima.pdf",
        )


class RegistrationWelcomeTests(unittest.TestCase):
    def test_registration_welcome_text_is_trilingual(self):
        text = get_registration_welcome_text()

        self.assertIn("Добро пожаловать", text)
        self.assertIn("Welcome", text)
        self.assertIn("Xush kelibsiz", text)
        self.assertIn("Выберите язык", text)
        self.assertIn("Select language", text)
        self.assertIn("Tilni tanlang", text)


class StudyImageFilteringTests(unittest.TestCase):
    def test_uzbek_query_uses_uzbek_search_terms(self):
        query = _build_image_search_query("biriktiruvchi to'qima", "uz")
        self.assertIn("o'zbekcha", query)
        self.assertIn("gistologiya", query)

    def test_uzbek_results_require_uzbek_signal(self):
        self.assertTrue(_is_image_result_allowed({"title": "O'zbekcha gistologiya diagramma"}, "uz"))
        self.assertFalse(_is_image_result_allowed({"title": "Histology connective tissue diagram"}, "uz"))


class QuizOptionShuffleTests(unittest.TestCase):
    def test_shuffle_preserves_multiple_correct_answers_with_duplicate_option_text(self):
        question = {
            "question": "Sample",
            "options": ["Fibroblast", "Fibroblast", "Chondrocyte", "Osteocyte"],
            "correct_indices": [0, 1],
        }

        shuffled = shuffle_question_options(question)

        self.assertEqual(len(shuffled["correct_indices"]), 2)
        self.assertEqual(
            sorted(shuffled["options"][idx] for idx in shuffled["correct_indices"]),
            ["Fibroblast", "Fibroblast"],
        )


class QuizAnswerRuleTests(unittest.TestCase):
    def test_filter_questions_keeps_multiple_correct_answers(self):
        questions = [
            {"question": "Q1", "options": ["A", "B", "C"], "correct_indices": [0]},
            {"question": "Q2", "options": ["A", "B", "C"], "correct_indices": [0, 1]},
            {"question": "Q3", "options": ["A", "B", "C", "D"], "correct_indices": [0, 1, 2]},
        ]

        filtered = filter_questions_by_answer_rule(questions)

        self.assertEqual(len(filtered), 3)
        self.assertEqual(filtered[0]["correct_indices"], [0])
        self.assertEqual(filtered[1]["correct_indices"], [0, 1])
        self.assertEqual(filtered[2]["correct_indices"], [0, 1, 2])

    def test_quiz_use_case_normalization_keeps_three_correct_answers(self):
        use_case = QuizUseCase(user_id=1, course="1", lang="en")
        questions = [
            {
                "question": "Q",
                "options": ["A", "B", "C", "D"],
                "correct_indices": [0, 1, 2],
                "explanation": "Because",
            },
        ]

        normalized = use_case._normalize_questions(questions)

        self.assertEqual(normalized[0]["correct_indices"], [0, 1, 2])


class QuizUiRuleTests(unittest.TestCase):
    def test_admin_open_question_is_routed_as_open_without_index_list(self):
        data = {
            "questions": [
                {"question": "What is shown?", "question_type": "open", "accepted_answers": ["fibroblast"]},
            ],
            "current_index": 0,
            "open_question_indices": [],
        }
        self.assertTrue(is_open_question(data, 0))

    @patch("handlers.user_handlers.random.shuffle", side_effect=lambda seq: seq.reverse())
    def test_control_test_choice_answers_are_shuffled_but_open_questions_are_untouched(self, _shuffle_mock):
        questions = [
            {
                "question": "Choice",
                "question_type": "choice",
                "options": ["A", "B", "C"],
                "correct_indices": [0],
            },
            {
                "question": "Open",
                "question_type": "open",
                "accepted_answers": ["fibroblast"],
                "options": [],
                "correct_indices": [],
            },
        ]

        prepared = prepare_control_test_questions(questions)

        self.assertEqual(prepared[0]["options"], ["C", "B", "A"])
        self.assertEqual(prepared[0]["correct_indices"], [2])
        self.assertEqual(prepared[1]["accepted_answers"], ["fibroblast"])
        self.assertEqual(prepared[1]["options"], [])

    def test_control_test_selection_uses_5_3_2_mix_when_available(self):
        multi = [
            {"question": f"M{i}", "question_type": "choice", "options": ["A", "B", "C"], "correct_indices": [0, 1]}
            for i in range(6)
        ]
        single = [
            {"question": f"S{i}", "question_type": "choice", "options": ["A", "B", "C"], "correct_indices": [1]}
            for i in range(5)
        ]
        open_q = [
            {"question": f"O{i}", "question_type": "open", "accepted_answers": ["x"], "options": [], "correct_indices": []}
            for i in range(4)
        ]

        selected = select_control_test_questions(multi + single + open_q)

        self.assertEqual(len(selected), 10)
        self.assertEqual(sum(1 for q in selected if q.get("question_type") == "open"), 2)
        self.assertEqual(sum(1 for q in selected if q.get("question_type") != "open" and len(q.get("correct_indices", [])) > 1), 5)
        self.assertEqual(sum(1 for q in selected if q.get("question_type") != "open" and len(q.get("correct_indices", [])) == 1), 3)

    def test_control_test_selection_backfills_when_type_is_short(self):
        multi = [
            {"question": f"M{i}", "question_type": "choice", "options": ["A", "B", "C"], "correct_indices": [0, 1]}
            for i in range(8)
        ]
        single = [
            {"question": f"S{i}", "question_type": "choice", "options": ["A", "B", "C"], "correct_indices": [1]}
            for i in range(3)
        ]
        open_q = [
            {"question": f"O{i}", "question_type": "open", "accepted_answers": ["x"], "options": [], "correct_indices": []}
            for i in range(1)
        ]

        selected = select_control_test_questions(multi + single + open_q)

        self.assertEqual(len(selected), 10)
        self.assertEqual(sum(1 for q in selected if q.get("question_type") == "open"), 1)
        self.assertGreaterEqual(sum(1 for q in selected if q.get("question_type") != "open" and len(q.get("correct_indices", [])) > 1), 5)

    def test_random_control_test_selection_respects_requested_count(self):
        questions = [{"question": str(i)} for i in range(40)]
        selected = select_random_control_test_questions(questions, 25)
        self.assertEqual(len(selected), 25)
        self.assertEqual(len({id(item) for item in selected}), 25)

    def test_random_control_test_selection_returns_all_when_short(self):
        questions = [{"question": str(i)} for i in range(7)]
        selected = select_random_control_test_questions(questions, 25)
        self.assertEqual(len(selected), 7)

    def test_single_answer_question_has_no_submit_button(self):
        question = {"options": ["A", "B", "C"], "correct_indices": [1]}
        keyboard = build_answer_keyboard(question, set(), "en")
        self.assertFalse(is_multi_answer_question(question))
        self.assertEqual(len(keyboard.inline_keyboard), 1)

    def test_multi_answer_question_keeps_submit_button(self):
        question = {"options": ["A", "B", "C", "D"], "correct_indices": [1, 2, 3]}
        keyboard = build_answer_keyboard(question, {1, 2}, "en")
        self.assertTrue(is_multi_answer_question(question))
        self.assertEqual(len(keyboard.inline_keyboard), 2)


if __name__ == "__main__":
    unittest.main()
