import httpx


class IntegrationError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class WazzupAdapter:
    def __init__(self, api_key: str):
        self.api_key = api_key

    async def request(self, method: str, path: str, payload=None, *, expect_json: bool = True):
        if not self.api_key:
            raise IntegrationError("key_not_configured")
        try:
            async with httpx.AsyncClient(
                base_url="https://api.wazzup24.com/v3", timeout=35, follow_redirects=False
            ) as client:
                response = await client.request(
                    method, path, json=payload, headers={"Authorization": "Bearer " + self.api_key}
                )
        except httpx.HTTPError:
            raise IntegrationError("connection_failed") from None
        if not response.is_success:
            # Provider descriptions may contain credentials or callback URLs; never echo them.
            raise IntegrationError(f"http_{response.status_code}")
        if not expect_json or not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            raise IntegrationError("invalid_response") from None

    async def list_channels(self):
        data = await self.request("GET", "/channels")
        if not isinstance(data, list):
            raise IntegrationError("invalid_channels_response")
        return [
            {k: item.get(k) for k in ("channelId", "transport", "state", "name")} for item in data
        ]

    async def subscribe_webhooks(self, url: str):
        return await self.request(
            "PATCH",
            "/webhooks",
            {
                "webhooksUri": url,
                "subscriptions": {
                    "messagesAndStatuses": True,
                    "contactsAndDealsCreation": False,
                    "channelsUpdates": True,
                },
            },
            # Wazzup may acknowledge PATCH with plain text; callers verify via GET.
            expect_json=False,
        )

    async def check_subscription(self):
        result = await self.request("GET", "/webhooks")
        if not isinstance(result, dict):
            raise IntegrationError("invalid_subscription_response")
        return result

    @staticmethod
    def parse_webhook(payload: dict) -> tuple[list[dict], list[dict]]:
        messages = payload.get("messages", [])
        statuses = payload.get("statuses", [])
        if not isinstance(messages, list) or not isinstance(statuses, list):
            raise ValueError("invalid_webhook_shape")
        if len(messages) + len(statuses) > 1000:
            raise ValueError("too_many_events")
        for item in messages:
            if not isinstance(item, dict) or any(
                not isinstance(item.get(k), str) or not item[k]
                for k in ("messageId", "channelId", "chatId")
            ):
                raise ValueError("invalid_message")
            if any(
                len(item[k]) > n
                for k, n in (("messageId", 100), ("channelId", 100), ("chatId", 200))
            ):
                raise ValueError("invalid_message_id")
        for item in statuses:
            if not isinstance(item, dict) or not isinstance(item.get("messageId"), str):
                raise ValueError("invalid_status")
        return messages, statuses
