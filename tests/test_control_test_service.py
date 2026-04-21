import unittest

from services.control_test_service import (
    ControlTestParseError,
    deserialize_correct_indices,
    deserialize_options,
    parse_control_test_text,
    serialize_correct_indices,
    serialize_options,
)


class ControlTestParserTests(unittest.TestCase):
    def test_parses_hash_plus_minus_format(self):
        content = """
# To'g'ri biriktiruvchi to'qimaning asosiy funktsiyalari qaysilar?
- Harakatni amalga oshirish
+ Moddalar almashinuvi va trofik funktsiya
+ Himoya va regeneratsiya
+ Mexanik qo'llab-quvvatlash
        """.strip()

        questions = parse_control_test_text(content)
        self.assertEqual(len(questions), 1)
        self.assertEqual(
            questions[0]["question"],
            "To'g'ri biriktiruvchi to'qimaning asosiy funktsiyalari qaysilar?",
        )
        self.assertEqual(len(questions[0]["options"]), 4)
        self.assertEqual(questions[0]["correct_indices"], [1, 2, 3])

    def test_parses_open_question_with_hidden_answers(self):
        content = """
# Rasmda ko'rsatilgan hujayrani yozing
* fibroblast
* fibrocyte
        """.strip()

        questions = parse_control_test_text(content)
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["question"], "Rasmda ko'rsatilgan hujayrani yozing")
        self.assertEqual(questions[0]["accepted_answers"], ["fibroblast", "fibrocyte"])
        self.assertEqual(questions[0]["options"], [])
        self.assertEqual(questions[0]["correct_indices"], [])

    def test_rejects_option_before_question(self):
        content = "+ A\n# Question\n- B"
        with self.assertRaises(ControlTestParseError):
            parse_control_test_text(content)

    def test_rejects_question_without_correct_answer(self):
        content = "# Question\n- A\n- B"
        with self.assertRaises(ControlTestParseError):
            parse_control_test_text(content)

    def test_rejects_mixing_open_answers_with_choice_options(self):
        content = "# Question\n* answer\n+ A\n- B"
        with self.assertRaises(ControlTestParseError):
            parse_control_test_text(content)


class ControlTestSerializationTests(unittest.TestCase):
    def test_roundtrip_options_and_indices(self):
        options = ["A", "B", "C"]
        indices = [0, 2]

        options_json = serialize_options(options)
        indices_json = serialize_correct_indices(indices)

        self.assertEqual(deserialize_options(options_json), options)
        self.assertEqual(deserialize_correct_indices(indices_json), indices)


if __name__ == "__main__":
    unittest.main()
