import tempfile
from pathlib import Path
import unittest

from biggantt import ask, choose, confirm, path_value


class InteractiveInputTests(unittest.TestCase):
    def test_empty_answer_uses_default(self):
        self.assertEqual(ask("x", "default", input_func=lambda _: ""), "default")

    def test_required_answer_reprompts(self):
        answers = iter(["", "CFMT"])
        self.assertEqual(ask("x", required=True, input_func=lambda _: next(answers)), "CFMT")

    def test_invalid_menu_reprompts(self):
        answers = iter(["9", "2"])
        self.assertEqual(choose("x", [("1", "a"), ("2", "b")], input_func=lambda _: next(answers)), "2")

    def test_confirmation_defaults_to_no(self):
        self.assertFalse(confirm("x", input_func=lambda _: ""))
        self.assertTrue(confirm("x", input_func=lambda _: "yes"))

    def test_missing_path_reprompts(self):
        with tempfile.TemporaryDirectory() as directory:
            valid = Path(directory) / "valid.json"
            valid.write_text("{}")
            answers = iter([str(Path(directory) / "missing.json"), str(valid)])
            self.assertEqual(path_value("x", "", input_func=lambda _: next(answers)), valid.resolve())


if __name__ == "__main__":
    unittest.main()
