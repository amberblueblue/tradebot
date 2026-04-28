from __future__ import annotations

from typing import Any

import requests

from futures_bot.config_loader import load_futures_config


DEFAULT_BASE_URL = "https://fapi.binance.com"
DEFAULT_TIMEOUT_SECONDS = 10


class BinanceFuturesPublicAPIError(RuntimeError):
    """Raised when Binance USD-M Futures public data cannot be fetched."""


class BinanceFuturesClient:
    """Public-only Binance USD-M Futures REST client.

    This client intentionally does not support signed endpoints, API keys, account
    reads, or order placement.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: int | None = None,
    ) -> None:
        config = load_futures_config()
        configured_base_url = base_url or config.futures.base_url or DEFAULT_BASE_URL
        configured_timeout = timeout or config.futures.request_timeout_seconds

        self.base_url = configured_base_url.rstrip("/")
        self.timeout = configured_timeout

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        request_params = params or {}

        try:
            response = requests.get(url, params=request_params, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else "unknown"
            response_text = exc.response.text if exc.response is not None else ""
            reason = (
                "Binance Futures public API returned "
                f"HTTP {status_code} for {path}; body={response_text}"
            )
            raise BinanceFuturesPublicAPIError(reason) from exc
        except requests.exceptions.RequestException as exc:
            reason = (
                "Binance Futures public API request failed "
                f"for {path}: {type(exc).__name__}: {exc}"
            )
            raise BinanceFuturesPublicAPIError(reason) from exc
        except ValueError as exc:
            reason = f"Binance Futures public API returned invalid JSON for {path}: {exc}"
            raise BinanceFuturesPublicAPIError(reason) from exc

    def ping(self) -> Any:
        return self._get("/fapi/v1/ping")

    def get_server_time(self) -> Any:
        return self._get("/fapi/v1/time")

    def get_exchange_info(self, symbol: str | None = None) -> Any:
        params = {"symbol": symbol} if symbol else None
        return self._get("/fapi/v1/exchangeInfo", params=params)

    def get_symbol_info(self, symbol: str) -> dict[str, Any]:
        exchange_info = self.get_exchange_info()
        symbols = exchange_info.get("symbols", [])
        for symbol_info in symbols:
            if symbol_info.get("symbol") == symbol:
                return symbol_info
        raise BinanceFuturesPublicAPIError(
            f"Binance Futures symbol not found in exchangeInfo: {symbol}"
        )

    def get_ticker_price(self, symbol: str) -> Any:
        return self._get("/fapi/v1/ticker/price", params={"symbol": symbol})

    def get_mark_price(self, symbol: str) -> Any:
        return self._get("/fapi/v1/premiumIndex", params={"symbol": symbol})

    def get_klines(self, symbol: str, interval: str, limit: int = 500) -> Any:
        return self._get(
            "/fapi/v1/klines",
            params={
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
            },
        )

    def get_funding_rate(self, symbol: str, limit: int = 1) -> Any:
        return self._get(
            "/fapi/v1/fundingRate",
            params={
                "symbol": symbol,
                "limit": limit,
            },
        )
