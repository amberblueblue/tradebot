from __future__ import annotations

from typing import Any

import requests

from observability.event_logger import StructuredLogger


DEFAULT_BASE_URL = "https://api.binance.com"
DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_ERROR_LOG_FILE = "logs/error.log"
SUPPORTED_KLINE_INTERVALS = {"5m", "15m", "1h", "4h", "1d"}


class BinancePublicAPIError(RuntimeError):
    """Raised when Binance public market data cannot be fetched."""


class BinanceClient:
    """Binance Spot public REST client for market data only."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        error_log_file: str = DEFAULT_ERROR_LOG_FILE,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.error_logger = StructuredLogger(error_log_file)

    def _log_error(self, *, path: str, reason: str, params: dict[str, Any] | None = None) -> None:
        self.error_logger.log(
            symbol=(params or {}).get("symbol", "-"),
            action="binance_public_api_error",
            reason=reason,
            endpoint=path,
            params=params or {},
        )

    def _request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        request_params = params or {}

        try:
            response = requests.get(url, params=request_params, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            response_text = exc.response.text if exc.response is not None else ""
            reason = f"Binance public API returned HTTP error for {path}: {exc}; body={response_text}"
            self._log_error(path=path, reason=reason, params=request_params)
            raise BinancePublicAPIError(reason) from exc
        except requests.exceptions.RequestException as exc:
            reason = f"Binance public API request failed for {path}: {exc}"
            self._log_error(path=path, reason=reason, params=request_params)
            raise BinancePublicAPIError(reason) from exc
        except ValueError as exc:
            reason = f"Binance public API returned invalid JSON for {path}: {exc}"
            self._log_error(path=path, reason=reason, params=request_params)
            raise BinancePublicAPIError(reason) from exc

    def ping(self) -> bool:
        self._request("/api/v3/ping")
        return True

    def get_server_time(self) -> dict[str, Any]:
        return self._request("/api/v3/time")

    def get_exchange_info(self, symbol: str | None = None) -> dict[str, Any]:
        params = {"symbol": symbol.upper()} if symbol else None
        return self._request("/api/v3/exchangeInfo", params)

    def get_symbol_info(self, symbol: str) -> dict[str, Any]:
        payload = self.get_exchange_info(symbol)
        symbols = payload.get("symbols", [])
        if not symbols:
            reason = f"Binance public API returned no exchange info for symbol {symbol}"
            self._log_error(
                path="/api/v3/exchangeInfo",
                reason=reason,
                params={"symbol": symbol.upper()},
            )
            raise BinancePublicAPIError(reason)
        return symbols[0]

    def get_ticker_price(self, symbol: str) -> dict[str, Any]:
        return self._request("/api/v3/ticker/price", {"symbol": symbol.upper()})

    def get_klines(self, symbol: str, interval: str, limit: int = 500) -> list[list[Any]]:
        if interval not in SUPPORTED_KLINE_INTERVALS:
            reason = f"Unsupported Binance kline interval: {interval}"
            params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
            self._log_error(path="/api/v3/klines", reason=reason, params=params)
            raise BinancePublicAPIError(reason)
        return self._request(
            "/api/v3/klines",
            {"symbol": symbol.upper(), "interval": interval, "limit": limit},
        )
