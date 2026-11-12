import json
import unittest
from unittest.mock import patch

import httpx

from app.integrations.channels.wazzup import IntegrationError, WazzupAdapter


class WazzupAdapterTests(unittest.IsolatedAsyncioTestCase):
    def mock_client(self, handler):
        original = httpx.AsyncClient
        return patch(
            "app.integrations.channels.wazzup.httpx.AsyncClient",
            side_effect=lambda **kwargs: original(
                **kwargs, transport=httpx.MockTransport(handler)
            ),
        )

    async def test_subscription_accepts_text_acknowledgement_and_remains_verifiable(self):
        callback = "https://crm.example.invalid/callback"
        subscription = {}

        def handler(request):
            self.assertEqual(request.url.path, "/v3/webhooks")
            if request.method == "PATCH":
                subscription.update(json.loads(request.content))
                self.assertEqual(subscription["webhooksUri"], callback)
                self.assertTrue(subscription["subscriptions"]["messagesAndStatuses"])
                return httpx.Response(200, text="OK")
            self.assertEqual(request.method, "GET")
            return httpx.Response(200, json=subscription)

        with self.mock_client(handler):
            adapter = WazzupAdapter("test-api-key")
            self.assertEqual(await adapter.subscribe_webhooks(callback), {})
            result = await adapter.check_subscription()
        self.assertEqual(result["webhooksUri"], callback)
        self.assertTrue(result["subscriptions"]["messagesAndStatuses"])

    async def test_subscription_accepts_empty_success_response(self):
        with self.mock_client(lambda request: httpx.Response(204)):
            result = await WazzupAdapter("test-api-key").subscribe_webhooks(
                "https://crm.example.invalid/callback"
            )
        self.assertEqual(result, {})

    async def test_subscription_http_error_is_not_treated_as_success(self):
        with self.mock_client(lambda request: httpx.Response(401, text="Unauthorized")):
            with self.assertRaises(IntegrationError) as error:
                await WazzupAdapter("test-api-key").subscribe_webhooks(
                    "https://crm.example.invalid/callback"
                )
        self.assertEqual(error.exception.code, "http_401")

    async def test_subscription_check_still_requires_json(self):
        with self.mock_client(lambda request: httpx.Response(200, text="not-json")):
            with self.assertRaises(IntegrationError) as error:
                await WazzupAdapter("test-api-key").check_subscription()
        self.assertEqual(error.exception.code, "invalid_response")


if __name__ == "__main__":
    unittest.main()
