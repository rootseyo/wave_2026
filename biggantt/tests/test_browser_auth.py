import unittest
from types import SimpleNamespace
from unittest.mock import patch

from biggantt_browser_auth import prompt_login_confirmation, validate_urls, wait_authenticated


class AuthTests(unittest.TestCase):
    def test_prompt_ignores_empty_and_unrecognized_input(self):
        with patch("builtins.input", side_effect=["", "yes", "  OK  "]) as prompt, patch("builtins.print"):
            self.assertTrue(prompt_login_confirmation())
        self.assertEqual(prompt.call_count, 3)

    def test_prompt_cancel_does_not_confirm_login(self):
        with patch("builtins.input", return_value="cancel"):
            self.assertFalse(prompt_login_confirmation())

    def test_prompt_eof_does_not_confirm_login(self):
        with patch("builtins.input", side_effect=EOFError):
            self.assertFalse(prompt_login_confirmation())

    def test_cross_site_app_rejected(self):
        with self.assertRaises(ValueError):
            validate_urls("https://a.atlassian.net", "https://b.atlassian.net/app")

    def test_login_redirect_never_counts_as_authenticated(self):
        requested = []
        page = SimpleNamespace(url="https://id.atlassian.com/login", is_closed=lambda: False,
                               wait_for_timeout=lambda _: None)
        context = SimpleNamespace(request=SimpleNamespace(get=lambda *a, **k: requested.append(a)))
        with patch("biggantt_browser_auth.time.monotonic", side_effect=[0, 0, 0, 0, 2]):
            with self.assertRaises(TimeoutError):
                wait_authenticated(context, page, "https://test.atlassian.net", timeout=1)
        self.assertEqual(requested, [])

    def test_expired_session_waits_until_cookie_session_is_valid(self):
        responses = iter([
            SimpleNamespace(status=401, dispose=lambda: None),
            SimpleNamespace(status=200, json=lambda: {"accountId": "example"}, dispose=lambda: None),
        ])
        waits = []
        page = SimpleNamespace(url="https://test.atlassian.net/jira", is_closed=lambda: False,
            frames=[], wait_for_timeout=lambda ms: waits.append(ms))
        context = SimpleNamespace(request=SimpleNamespace(get=lambda *a, **k: next(responses)))
        wait_authenticated(context, page, "https://test.atlassian.net", timeout=5)
        self.assertEqual(waits, [1000])

    def test_closed_browser_is_not_success(self):
        with self.assertRaises(RuntimeError):
            wait_authenticated(None, SimpleNamespace(is_closed=lambda: True), "https://test.atlassian.net")


if __name__ == "__main__":
    unittest.main()
