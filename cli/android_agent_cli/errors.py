from __future__ import annotations

from typing import Any


class AxiError(Exception):
    def __init__(
        self,
        message: str,
        help_hints: list[str] | None = None,
        exit_code: int = 1,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.help_hints = list(help_hints) if help_hints else []
        self.exit_code = exit_code
        self.details = dict(details) if details else None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"error": self.message}
        if self.details:
            for key, value in self.details.items():
                if key not in ("error", "help"):
                    payload[key] = value
        if len(self.help_hints) == 1:
            payload["help"] = self.help_hints[0]
        elif len(self.help_hints) > 1:
            payload["help"] = self.help_hints
        return payload


class UsageError(AxiError):
    def __init__(
        self,
        message: str,
        help_hints: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, help_hints=help_hints, exit_code=2, details=details)


class RuntimeAxiError(AxiError):
    def __init__(
        self,
        message: str,
        help_hints: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, help_hints=help_hints, exit_code=1, details=details)
