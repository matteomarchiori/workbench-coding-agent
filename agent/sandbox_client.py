import json
import os

import httpx


class SandboxClient:
    def __init__(self):
        enabled = os.getenv("SANDBOX_ENABLED", "false").casefold()

        if enabled not in ("true", "false"):
            raise ValueError("SANDBOX_ENABLED must be true or false")

        self.enabled = enabled == "true"
        self.base_url = os.getenv("SANDBOX_URL", "http://sandbox:8002").rstrip("/")
        self.token = os.getenv("SANDBOX_TOKEN", "")
        self.timeout = float(os.getenv("SANDBOX_TIMEOUT_SECONDS", "180"))

        if not 1 <= self.timeout <= 300:
            raise ValueError("SANDBOX_TIMEOUT_SECONDS must be between 1 and 300")

        if self.enabled and (
            len(self.token) < 32 or not self.token.isascii()
        ):
            raise ValueError(
                "SANDBOX_TOKEN must contain at least 32 ASCII characters"
            )

    async def run_checks(self) -> str:
        if not self.enabled:
            raise RuntimeError("Sandbox checks are disabled")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/checks",
                headers={"Authorization": f"Bearer {self.token}"},
            )
            response.raise_for_status()

        if len(response.content) > 20_000:
            raise RuntimeError("Sandbox response exceeded the size limit")

        result = response.json()
        checks = result.get("checks") if isinstance(result, dict) else None

        if (
            not isinstance(result, dict)
            or not isinstance(result.get("passed"), bool)
            or not isinstance(checks, list)
            or len(checks) != 2
            or {check.get("name") for check in checks if isinstance(check, dict)}
            != {"pytest", "python_syntax"}
            or any(
                not isinstance(check, dict)
                or not isinstance(check.get("passed"), bool)
                or not isinstance(check.get("exit_code"), int)
                or not isinstance(check.get("timed_out"), bool)
                or not isinstance(check.get("duration_seconds"), (int, float))
                for check in checks
            )
        ):
            raise RuntimeError("Sandbox returned an invalid response")

        return json.dumps(
            {"ok": True, "result": result},
            ensure_ascii=False,
        )
