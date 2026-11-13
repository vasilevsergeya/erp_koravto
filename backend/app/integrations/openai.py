"""Stateless Responses adapter. Errors never contain requests, API keys or customer text."""

import json
import math

import httpx


class AIError(Exception):
    pass


class OpenAIAdapter:
    def __init__(self, key, record):
        self.key, self.record = key, record

    async def request(self, endpoint, body, purpose):
        if not self.key:
            raise AIError("not_configured")
        model = body["model"]
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                response = await client.post(
                    "https://api.openai.com/v1/" + endpoint,
                    json=body,
                    headers={"Authorization": "Bearer " + self.key},
                )
            if response.status_code >= 400:
                raise AIError("provider_http_" + str(response.status_code))
            data = response.json()
            if not isinstance(data, dict):
                raise AIError("invalid_response")
        except (httpx.HTTPError, ValueError) as error:
            self.record(model, purpose, {}, "provider_unavailable")
            raise AIError("provider_unavailable") from error
        except AIError as error:
            self.record(model, purpose, {}, str(error))
            raise
        self.record(model, purpose, data.get("usage") or {}, None)
        return data

    async def response(self, model, instructions, inputs, schema, tools=None):
        body = {
            "model": model,
            "store": False,
            "instructions": instructions,
            "input": inputs,
            "max_output_tokens": 1600,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "crm_result",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        if tools:
            body.update(tools=tools, parallel_tool_calls=False)
        result = await self.request("responses", body, "response")
        if result.get("status") != "completed":
            raise AIError("incomplete_response")
        return result.get("output", [])

    async def embedding(self, model, value):
        result = await self.request(
            "embeddings", {"model": model, "input": value[:10000], "dimensions": 1536}, "embedding"
        )
        try:
            vector = result["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError):
            raise AIError("invalid_embedding") from None
        if len(vector) != 1536 or any(
            not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector
        ):
            raise AIError("invalid_embedding")
        return vector


def output_json(output):
    texts = [
        c["text"]
        for o in output
        if o.get("type") == "message"
        for c in o.get("content", [])
        if c.get("type") == "output_text"
    ]
    try:
        result = json.loads("".join(texts))
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise AIError("invalid_structured_output") from None
