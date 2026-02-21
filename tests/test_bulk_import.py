import os
import unittest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "service-role-key")

from src.logic.bulk_import import parse_bulk_block, split_bulk_blocks


class BulkImportParsingTest(unittest.TestCase):
    def test_split_by_explicit_separator_without_trailing_separator(self):
        raw = (
            "Q: First?\n"
            "A) 1\nB) 2\nC) 3\nD) 4\n"
            "ANS: B\n"
            "---\n"
            "Q: Second?\n"
            "A) aa\nB) bb\nC) cc\nD) dd\n"
            "ANS: D"
        )
        blocks = split_bulk_blocks(raw)
        self.assertEqual(len(blocks), 2)
        self.assertTrue(blocks[0].startswith("Q: First?"))
        self.assertTrue(blocks[1].startswith("Q: Second?"))

    def test_split_without_separator_on_q_boundary(self):
        raw = (
            "Q: First?\n"
            "A: 1\nB: 2\nC: 3\nD: 4\n"
            "ANS: B\n"
            "В: Второй?\n"
            "A) да\nB) нет\nC) может\nD) позже\n"
            "ANS: A"
        )
        blocks = split_bulk_blocks(raw)
        self.assertEqual(len(blocks), 2)
        self.assertTrue(blocks[1].startswith("В: Второй?"))

    def test_parse_block_supports_colon_options_and_optional_fields(self):
        block = (
            "В: Столица Франции?\n"
            "A: Париж\n"
            "B: Лион\n"
            "C: Марсель\n"
            "D: Ницца\n"
            "ANS: A\n"
            "TOPIC_ID: 3\n"
            "DIFF: 2\n"
            "ACTIVE: false"
        )
        parsed = parse_bulk_block(block)
        self.assertEqual(parsed["q"], "Столица Франции?")
        self.assertEqual(parsed["correct"], 1)
        self.assertEqual(parsed["topic_id"], 3)
        self.assertEqual(parsed["difficulty"], 2)
        self.assertFalse(parsed["is_active"])


if __name__ == "__main__":
    unittest.main()
