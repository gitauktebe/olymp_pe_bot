import unittest

from src.logic.question_schema import normalize_question


class NormalizeQuestionTest(unittest.TestCase):
    def test_supports_supabase_schema(self):
        payload = {
            "id": 1,
            "q": "Q?",
            "a1": "A",
            "a2": "B",
            "a3": "C",
            "a4": "D",
            "correct": 2,
        }
        normalized = normalize_question(payload)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["text"], "Q?")
        self.assertEqual(normalized["option2"], "B")
        self.assertEqual(normalized["correct_option"], 2)

    def test_supports_legacy_schema(self):
        payload = {
            "id": 2,
            "text": "Legacy Q",
            "option1": "A",
            "option2": "B",
            "option3": "C",
            "option4": "D",
            "correct_option": "3",
        }
        normalized = normalize_question(payload)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["q"], "Legacy Q")
        self.assertEqual(normalized["a3"], "C")
        self.assertEqual(normalized["correct"], 3)

    def test_rejects_incomplete_payload(self):
        payload = {"id": 3, "q": "Q only"}
        self.assertIsNone(normalize_question(payload))


if __name__ == "__main__":
    unittest.main()
