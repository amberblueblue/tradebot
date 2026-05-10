from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from onchain_bot.quote_parser import parse_okx_quote

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOTENV_PATH = PROJECT_ROOT / ".env"

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(dotenv_path: str | os.PathLike[str] | None = None) -> bool:
        env_path = os.fspath(dotenv_path or DOTENV_PATH)
        if not os.path.exists(env_path):
            return False
        try:
            with open(env_path, "r", encoding="utf-8") as env_file:
                for raw_line in env_file:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
            return True
        except OSError:
            return False


load_dotenv(DOTENV_PATH)

LEGACY_OKX_WEB3_ENV_MAP = {
    "OKX_WEB3_API_KEY": "OKX_API_KEY",
    "OKX_WEB3_SECRET_KEY": "OKX_SECRET_KEY",
    "OKX_WEB3_PASSPHRASE": "OKX_PASSPHRASE",
}

for web3_key, legacy_key in LEGACY_OKX_WEB3_ENV_MAP.items():
    if not os.environ.get(web3_key) and os.environ.get(legacy_key):
        os.environ[web3_key] = str(os.environ[legacy_key])


OKX_DEX_BASE_URL = "https://web3.okx.com"
OKX_DEX_QUOTE_PATH = "/api/v6/dex/aggregator/quote"
OKX_DEX_USER_AGENT = "Mozilla/5.0 tradebot-onchain-quote/1.0"
OKX_WEB3_ENV_KEYS = (
    "OKX_WEB3_API_KEY",
    "OKX_WEB3_SECRET_KEY",
    "OKX_WEB3_PASSPHRASE",
)


@dataclass(frozen=True)
class OkxDexCredentials:
    api_key: str
    secret_key: str
    passphrase: str
    project_id: str | None = None


def load_okx_dex_credentials() -> tuple[OkxDexCredentials | None, list[str]]:
    missing = [
        key
        for key in OKX_WEB3_ENV_KEYS
        if not os.environ.get(key)
    ]
    if missing:
        return None, missing
    return OkxDexCredentials(
        api_key=str(os.environ["OKX_WEB3_API_KEY"]),
        secret_key=str(os.environ["OKX_WEB3_SECRET_KEY"]),
        passphrase=str(os.environ["OKX_WEB3_PASSPHRASE"]),
        project_id=os.environ.get("OKX_WEB3_PROJECT_ID") or None,
    ), []


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sign(message: str, secret_key: str) -> str:
    digest = hmac.new(
        secret_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def _elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


def _amount_to_base_units(amount_display: float | int | str, decimals: int) -> int:
    try:
        amount = Decimal(str(amount_display))
    except InvalidOperation as exc:
        raise ValueError("amount_display must be a number greater than 0") from exc
    if amount <= 0:
        raise ValueError("amount_display must be greater than 0")
    return int(amount * (Decimal(10) ** decimals))


def _headers(
    *,
    credentials: OkxDexCredentials,
    method: str,
    request_path_with_query: str,
) -> dict[str, str]:
    timestamp = _timestamp()
    signature = _sign(
        f"{timestamp}{method}{request_path_with_query}",
        credentials.secret_key,
    )
    headers = {
        "OK-ACCESS-KEY": credentials.api_key,
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": credentials.passphrase,
        "Content-Type": "application/json",
        "User-Agent": OKX_DEX_USER_AGENT,
    }
    if credentials.project_id:
        headers["OK-ACCESS-PROJECT"] = credentials.project_id
    return headers


def _request_diagnostics(
    *,
    url: str,
    headers: dict[str, str],
    status_code: int | None,
    response_body: str | None = None,
) -> dict[str, Any]:
    return {
        "http_status": status_code,
        "response_body": response_body,
        "request_url": url,
        "request_headers_present": {
            "OK-ACCESS-KEY": bool(headers.get("OK-ACCESS-KEY")),
            "OK-ACCESS-SIGN": bool(headers.get("OK-ACCESS-SIGN")),
            "OK-ACCESS-TIMESTAMP": bool(headers.get("OK-ACCESS-TIMESTAMP")),
            "OK-ACCESS-PASSPHRASE": bool(headers.get("OK-ACCESS-PASSPHRASE")),
            "OK-ACCESS-PROJECT": bool(headers.get("OK-ACCESS-PROJECT")),
            "User-Agent": bool(headers.get("User-Agent")),
        },
        "timestamp": headers.get("OK-ACCESS-TIMESTAMP"),
        "signature_rule": {
            "method": "GET",
            "message": "OK-ACCESS-TIMESTAMP + method + requestPathWithQuery",
            "headers": [
                "OK-ACCESS-KEY",
                "OK-ACCESS-SIGN",
                "OK-ACCESS-TIMESTAMP",
                "OK-ACCESS-PASSPHRASE",
                "OK-ACCESS-PROJECT",
            ],
        },
    }


def _okx_business_error(payload: Any) -> tuple[str, str] | None:
    if not isinstance(payload, dict):
        return "okx_invalid_response", "OKX response is not a JSON object."
    code = str(payload.get("code", ""))
    if code and code != "0":
        return "okx_business_error", str(payload.get("msg") or payload.get("message") or f"OKX error code {code}")
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return "okx_business_error", str(payload.get("msg") or "OKX quote response data is empty.")
    return None


class OkxDexQuoteClient:
    def __init__(self, *, base_url: str = OKX_DEX_BASE_URL, timeout_seconds: int = 10) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def quote(
        self,
        *,
        chain_id: str,
        from_token_address: str,
        to_token_address: str,
        amount: int,
    ) -> dict[str, Any]:
        started_at = time.perf_counter()
        credentials, missing = load_okx_dex_credentials()
        endpoint = f"{self.base_url}{OKX_DEX_QUOTE_PATH}"
        if credentials is None:
            return {
                "ok": False,
                "endpoint": endpoint,
                "status_code": None,
                "latency_ms": _elapsed_ms(started_at),
                "error": "missing_okx_web3_credentials",
                "message": "Missing OKX Web3 API environment variables: " + ", ".join(missing),
            }

        params = {
            "chainIndex": chain_id,
            "fromTokenAddress": from_token_address,
            "toTokenAddress": to_token_address,
            "amount": str(amount),
            "swapMode": "exactIn",
        }
        query_string = urlencode(params)
        request_path_with_query = f"{OKX_DEX_QUOTE_PATH}?{query_string}"
        url = f"{self.base_url}{request_path_with_query}"
        headers = _headers(
            credentials=credentials,
            method="GET",
            request_path_with_query=request_path_with_query,
        )

        last_error: str | None = None
        for attempt, backoff_seconds in enumerate((1, 2, 3), start=1):
            request = Request(url, headers=headers, method="GET")
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    body = response.read().decode("utf-8", "ignore")
                    try:
                        payload = json.loads(body)
                    except json.JSONDecodeError:
                        payload = {"raw_body": body}
                    business_error = _okx_business_error(payload)
                    if business_error:
                        error, message = business_error
                        return {
                            "ok": False,
                            "endpoint": endpoint,
                            "status_code": response.status,
                            "http_status": response.status,
                            "latency_ms": _elapsed_ms(started_at),
                            "quote": payload,
                            "error": error,
                            "message": message,
                        }
                    return {
                        "ok": True,
                        "endpoint": endpoint,
                        "status_code": response.status,
                        "latency_ms": _elapsed_ms(started_at),
                        "quote": payload,
                        "error": None,
                    }
            except HTTPError as exc:
                body = exc.read().decode("utf-8", "ignore")
                diagnostics = _request_diagnostics(
                    url=url,
                    headers=headers,
                    status_code=exc.code,
                    response_body=body,
                )
                return {
                    "ok": False,
                    "endpoint": endpoint,
                    "status_code": exc.code,
                    "http_status": exc.code,
                    "latency_ms": _elapsed_ms(started_at),
                    "request_url": url,
                    "request_headers_present": diagnostics["request_headers_present"],
                    "timestamp": diagnostics["timestamp"],
                    "response_body": body,
                    "diagnostics": diagnostics,
                    "quote": None,
                    "error": "okx_dex_http_error",
                    "message": body or str(exc),
                }
            except (TimeoutError, URLError, OSError) as exc:
                last_error = str(exc)
                if attempt < 3:
                    time.sleep(backoff_seconds)

        return {
            "ok": False,
            "endpoint": endpoint,
            "status_code": None,
            "latency_ms": _elapsed_ms(started_at),
            "request_url": url,
            "request_headers_present": _request_diagnostics(
                url=url,
                headers=headers,
                status_code=None,
            )["request_headers_present"],
            "timestamp": headers.get("OK-ACCESS-TIMESTAMP"),
            "quote": None,
            "error": "okx_dex_network_error",
            "message": last_error,
        }

    def get_quote(
        self,
        *,
        chain_id: str,
        from_token_address: str,
        to_token_address: str,
        from_token_decimals: int,
        to_token_decimals: int,
        amount_display: float | int | str,
        slippage_pct: float | int | str | None = None,
        direction: str,
        from_token_symbol: str | None = None,
        to_token_symbol: str | None = None,
    ) -> dict[str, Any]:
        normalized_direction = direction.strip().lower()
        if normalized_direction not in {"buy", "sell"}:
            raise ValueError("direction must be buy or sell")

        amount = _amount_to_base_units(amount_display, from_token_decimals)
        quote_result = self.quote(
            chain_id=chain_id,
            from_token_address=from_token_address,
            to_token_address=to_token_address,
            amount=amount,
        )
        parsed_quote = parse_okx_quote(
            raw_quote=quote_result.get("quote"),
            amount_display=amount_display,
            from_token_symbol=from_token_symbol,
            from_token_decimals=from_token_decimals,
            to_token_symbol=to_token_symbol,
            to_token_decimals=to_token_decimals,
            direction=normalized_direction,
            max_slippage_pct=slippage_pct,
            latency_ms=quote_result.get("latency_ms"),
        )
        return {
            "ok": bool(quote_result.get("ok")),
            "direction": normalized_direction,
            "chain_id": chain_id,
            "from_token_address": from_token_address,
            "to_token_address": to_token_address,
            "from_token_symbol": from_token_symbol,
            "to_token_symbol": to_token_symbol,
            "amount_display": float(Decimal(str(amount_display))),
            "from_token_amount": str(amount),
            "from_amount_display": parsed_quote.get("from_amount_display"),
            "to_amount_display": parsed_quote.get("to_amount_display"),
            "implied_price": parsed_quote.get("implied_price"),
            "price_impact_pct": parsed_quote.get("price_impact_pct"),
            "route": parsed_quote.get("route"),
            "quote": quote_result.get("quote"),
            "parsed_quote": parsed_quote,
            "endpoint": quote_result.get("endpoint"),
            "status_code": quote_result.get("status_code"),
            "http_status": quote_result.get("http_status"),
            "request_url": quote_result.get("request_url"),
            "request_headers_present": quote_result.get("request_headers_present"),
            "timestamp": quote_result.get("timestamp"),
            "response_body": quote_result.get("response_body"),
            "diagnostics": quote_result.get("diagnostics"),
            "latency_ms": quote_result.get("latency_ms"),
            "error": quote_result.get("error"),
            "message": quote_result.get("message"),
            "quote_only": True,
        }
