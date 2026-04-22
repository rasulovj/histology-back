import unittest
from unittest.mock import patch

from services import ai_service


class _FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return self._body


class _FakeSession:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self._response


class AskAiTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_none_when_key_is_missing(self):
        with (
            patch("services.ai_service.DEEPSEEK_KEY", ""),
            patch("builtins.print") as print_mock,
        ):
            result = await ai_service._ask_ai("hello")

        self.assertIsNone(result)
        print_mock.assert_called_once_with(
            "❌ DeepSeek unavailable — DEEPSEEK_KEY is missing in .env"
        )

    async def test_returns_message_content_on_successful_response(self):
        session = _FakeSession(
            _FakeResponse(
                200,
                '{"choices":[{"message":{"content":"Generated answer"}}]}',
            )
        )

        with (
            patch("services.ai_service.DEEPSEEK_KEY", "sk-test"),
            patch("services.ai_service.aiohttp.ClientSession", return_value=session),
        ):
            result = await ai_service._ask_ai("hello")

        self.assertEqual(result, "Generated answer")
        self.assertEqual(session.calls[0]["url"], ai_service.DEEPSEEK_API_URL)
        self.assertEqual(
            session.calls[0]["headers"]["Authorization"],
            "Bearer sk-test",
        )
        self.assertEqual(session.calls[0]["json"]["model"], ai_service.DEEPSEEK_MODEL)

    async def test_returns_none_on_http_error(self):
        session = _FakeSession(_FakeResponse(401, '{"error":"invalid key"}'))

        with (
            patch("services.ai_service.DEEPSEEK_KEY", "sk-test"),
            patch("services.ai_service.aiohttp.ClientSession", return_value=session),
            patch("builtins.print") as print_mock,
        ):
            result = await ai_service._ask_ai("hello")

        self.assertIsNone(result)
        print_mock.assert_called_once_with(
            '❌ DeepSeek HTTP 401: {"error":"invalid key"}'
        )


if __name__ == "__main__":
    unittest.main()
