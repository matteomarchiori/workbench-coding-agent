import asyncio
import os
import time

import httpx


class ModelProvider:
    def __init__(self):
        self.base_url = os.getenv(
            "MODEL_BASE_URL",
            "http://host.containers.internal:8000/v1",
        )

        self.model = os.getenv(
            "MODEL_NAME",
            "Qwen/Qwen3-8B-MLX-4bit",
        )
        self.timeout = float(os.getenv("MODEL_TIMEOUT_SECONDS", "180"))
        self.max_retries = int(os.getenv("MODEL_MAX_RETRIES", "2"))
        self._request_count = 0
        self._success_count = 0
        self._error_count = 0
        self._attempt_count = 0
        self._retry_count = 0
        self._total_duration_seconds = 0.0

        if not 1 <= self.timeout <= 600:
            raise ValueError("MODEL_TIMEOUT_SECONDS must be between 1 and 600")

        if not 0 <= self.max_retries <= 3:
            raise ValueError("MODEL_MAX_RETRIES must be between 0 and 3")

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=min(5, self.timeout)) as client:
                response = await client.get(f"{self.base_url}/models")
                response.raise_for_status()
                return True
        except httpx.HTTPError:
            return False

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
    ) -> dict:
        started = time.perf_counter()
        self._request_count += 1

        try:
            result = await self._complete_with_retries(
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
            )
        except Exception:
            self._error_count += 1
            raise
        else:
            self._success_count += 1
            return result
        finally:
            self._total_duration_seconds += time.perf_counter() - started

    def metrics(self) -> dict[str, int | float]:
        average_duration = (
            self._total_duration_seconds / self._request_count
            if self._request_count
            else 0
        )
        return {
            "requests": self._request_count,
            "successes": self._success_count,
            "errors": self._error_count,
            "attempts": self._attempt_count,
            "retries": self._retry_count,
            "average_duration_seconds": round(average_duration, 3),
        }

    async def _complete_with_retries(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
    ) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "top_p": 0.8,
            "max_tokens": 1500,
            "chat_template_kwargs": {"enable_thinking": False},
        }

        if tools is not None:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"

        retryable_statuses = {429, 500, 502, 503, 504}
        result = None

        for attempt in range(self.max_retries + 1):
            self._attempt_count += 1
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        json=payload,
                    )

                    response.raise_for_status()
                    result = response.json()
                break
            except httpx.HTTPStatusError as exc:
                if (
                    exc.response.status_code not in retryable_statuses
                    or attempt == self.max_retries
                ):
                    raise
            except httpx.TransportError:
                if attempt == self.max_retries:
                    raise

            self._retry_count += 1
            await asyncio.sleep(min(0.25 * (2**attempt), 2))

        if not isinstance(result, dict):
            raise RuntimeError("Invalid model response format")
        choices = result.get("choices")

        if not isinstance(choices, list) or not choices:
            raise RuntimeError("Model returned invalid or empty choices")

        choice = choices[0]
        if not isinstance(choice, dict):
            raise RuntimeError("Invalid model choice format")
        message = choice.get("message")

        if not isinstance(message, dict):
            raise RuntimeError("Invalid assistant message")

        role = message.get("role")

        if role is not None and not isinstance(role, str):
            raise RuntimeError("Invalid assistant role")

        content = message.get("content")

        if content is not None and not isinstance(content, str):
            raise RuntimeError("Invalid assistant content")

        finish_reason = choice.get("finish_reason")

        if finish_reason is not None and not isinstance(finish_reason, str):
            raise RuntimeError("Invalid finish_reason format")

        tool_calls = message.get("tool_calls")

        if tool_calls is not None:
            if not isinstance(tool_calls, list):
                raise RuntimeError("Invalid tool_calls format")

            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    raise RuntimeError("Invalid tool call format")

                if not isinstance(tool_call.get("id"), str):
                    raise RuntimeError("Invalid tool call ID")

                function = tool_call.get("function")

                if not isinstance(function, dict):
                    raise RuntimeError("Invalid tool call function")

                if not isinstance(function.get("name"), str):
                    raise RuntimeError("Invalid tool call name")

                if not isinstance(function.get("arguments"), str):
                    raise RuntimeError("Invalid tool call arguments")

        if tools is not None and finish_reason == "tool_calls":
            if not isinstance(tool_calls, list) or not tool_calls:
                raise RuntimeError(
                    "tool_calls finish reason requires tool calls"
                )
        return {
            "message": message,
            "finish_reason": finish_reason,
        }

    async def generate(self, messages: list[dict]) -> str:
        result = await self.complete(messages)
        content = result["message"].get("content")

        return content or ""
