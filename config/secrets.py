from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = BASE_DIR / ".env"
BINANCE_API_KEY_ENV = "BINANCE_API_KEY"
BINANCE_API_SECRET_ENV = "BINANCE_API_SECRET"


@dataclass(frozen=True)
class BinanceReadOnlyCredentials:
    api_key: str | None
    api_secret: str | None

    @property
    def api_key_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def api_secret_configured(self) -> bool:
        return bool(self.api_secret)

    @property
    def configured(self) -> bool:
        return self.api_key_configured and self.api_secret_configured

    def public_status(self) -> dict[str, bool | str]:
        return {
            "api_key_configured": self.api_key_configured,
            "api_secret_configured": self.api_secret_configured,
            "configured": self.configured,
            "source": "environment_or_dotenv",
            "mode": "read_only",
        }


def _parse_dotenv_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None

    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def load_dotenv_values(path: Path = DEFAULT_ENV_PATH) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_dotenv_line(line)
        if parsed is None:
            continue
        key, value = parsed
        values[key] = value
    return values


def _secret_from_environment_or_dotenv(name: str, dotenv_values: dict[str, str]) -> str | None:
    value = os.environ.get(name)
    if value is not None:
        value = value.strip()
    if not value:
        value = dotenv_values.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def load_binance_readonly_credentials(env_path: Path = DEFAULT_ENV_PATH) -> BinanceReadOnlyCredentials:
    dotenv_values = load_dotenv_values(env_path)
    return BinanceReadOnlyCredentials(
        api_key=_secret_from_environment_or_dotenv(BINANCE_API_KEY_ENV, dotenv_values),
        api_secret=_secret_from_environment_or_dotenv(BINANCE_API_SECRET_ENV, dotenv_values),
    )


def main() -> None:
    env_path = DEFAULT_ENV_PATH
    credentials = load_binance_readonly_credentials(env_path)
    print(f".env path: {env_path}")
    print(f".env exists: {str(env_path.exists()).lower()}")
    print(f"api_key_configured: {str(credentials.api_key_configured).lower()}")
    print(f"api_secret_configured: {str(credentials.api_secret_configured).lower()}")


if __name__ == "__main__":
    main()
