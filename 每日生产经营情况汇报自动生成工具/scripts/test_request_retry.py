from __future__ import annotations

import unittest

from playwright.sync_api import Error as PlaywrightError

from export_online_energy import request_json_with_retry


class FakeResponse:
    status = 200

    def __init__(self, text_or_error: str | BaseException) -> None:
        self.text_or_error = text_or_error

    def text(self) -> str:
        if isinstance(self.text_or_error, BaseException):
            raise self.text_or_error
        return self.text_or_error


class FakeRequest:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls = 0

    def get(self, url: str, **kwargs):
        response = self.responses[self.calls]
        self.calls += 1
        return response


class FakeContext:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.request = FakeRequest(responses)


class RequestJsonWithRetryTests(unittest.TestCase):
    def test_retries_failure_while_reading_200_response_body(self) -> None:
        context = FakeContext(
            [
                FakeResponse(PlaywrightError("read ETIMEDOUT; cookie: secret")),
                FakeResponse('{"retCode":"T200","data":{"ok":true}}'),
            ]
        )

        payload = request_json_with_retry(
            context,
            "get",
            "https://example.invalid/data",
            "读取测试数据",
            retry_delay_seconds=0,
        )

        self.assertTrue(payload["data"]["ok"])
        self.assertEqual(context.request.calls, 2)

    def test_final_error_does_not_repeat_sensitive_playwright_details(self) -> None:
        context = FakeContext(
            [
                FakeResponse(PlaywrightError("cookie: secret")),
                FakeResponse(PlaywrightError("cookie: secret")),
            ]
        )

        with self.assertRaisesRegex(RuntimeError, "已重试 2 次") as raised:
            request_json_with_retry(
                context,
                "get",
                "https://example.invalid/data",
                "读取测试数据",
                attempts=2,
                retry_delay_seconds=0,
            )

        self.assertNotIn("secret", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
