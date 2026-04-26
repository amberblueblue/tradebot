from __future__ import annotations

import hmac
import os
import time
from hashlib import sha256
from typing import Any
from urllib.parse import urlencode

import requests

from config.loader import load_project_config
from config.secrets import BinanceReadOnlyCredentials, load_binance_readonly_credentials
from observability.event_logger import StructuredLogger


DEFAULT_BASE_URL = "https://api.binance.com"
DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_ERROR_LOG_FILE = "logs/error.log"
DEFAULT_RECV_WINDOW = 5000
FINAL_REAL_ORDER_ENV_VAR = "TRADEBOT_FINAL_REAL_ORDER"
FINAL_REAL_ORDER_ENV_VALUE = "YES"
SUPPORTED_KLINE_INTERVALS = {"5m", "15m", "1h", "4h", "1d"}
SENSITIVE_PARAM_KEYS = {"signature"}


class BinancePublicAPIError(RuntimeError):
    """Raised when Binance public market data cannot be fetched."""


class BinancePrivateReadOnlyAPIError(RuntimeError):
    """Raised when Binance signed read-only account data cannot be fetched."""


class BinanceTestOrderAPIError(RuntimeError):
    """Raised when Binance signed test order validation cannot be completed."""


class BinanceRealOrderAPIError(RuntimeError):
    """Raised when Binance real order submission cannot be completed."""


class BinanceClient:
    """Binance Spot REST client for public data and signed read-only account queries."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        error_log_file: str = DEFAULT_ERROR_LOG_FILE,
        recv_window: int = DEFAULT_RECV_WINDOW,
        credentials: BinanceReadOnlyCredentials | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.recv_window = max(int(recv_window), 10000)
        self.credentials = credentials
        self.error_logger = StructuredLogger(error_log_file)
        self._server_time_offset_ms: int | None = None

    def _safe_params(self, params: dict[str, Any] | None) -> dict[str, Any]:
        if not params:
            return {}
        return {
            key: ("[redacted]" if key in SENSITIVE_PARAM_KEYS else value)
            for key, value in params.items()
        }

    def _log_error(
        self,
        *,
        path: str,
        reason: str,
        params: dict[str, Any] | None = None,
        action: str = "binance_public_api_error",
    ) -> None:
        self.error_logger.log(
            symbol=(params or {}).get("symbol", "-"),
            action=action,
            reason=reason,
            endpoint=path,
            params=self._safe_params(params),
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

    def _load_credentials(self) -> BinanceReadOnlyCredentials:
        return self.credentials or load_binance_readonly_credentials()

    def _current_timestamp_ms(self) -> int:
        local_time_ms = int(time.time() * 1000)
        if self._server_time_offset_ms is not None:
            return local_time_ms + self._server_time_offset_ms

        try:
            payload = self.get_server_time()
            server_time_ms = int(payload.get("serverTime"))
            self._server_time_offset_ms = server_time_ms - local_time_ms
            return server_time_ms
        except Exception as exc:
            self._log_error(
                path="/api/v3/time",
                reason=f"Binance server time offset unavailable; using local timestamp: {exc}",
                action="binance_time_sync_warning",
            )
            self._server_time_offset_ms = 0
            return local_time_ms

    def _require_readonly_credentials(self) -> BinanceReadOnlyCredentials:
        credentials = self._load_credentials()
        missing = []
        if not credentials.api_key_configured:
            missing.append("BINANCE_API_KEY")
        if not credentials.api_secret_configured:
            missing.append("BINANCE_API_SECRET")
        if missing:
            reason = f"Binance read-only API credentials missing: {', '.join(missing)}"
            self._log_error(
                path="-",
                reason=reason,
                action="binance_readonly_api_error",
            )
            raise BinancePrivateReadOnlyAPIError(reason)
        return credentials

    def _signed_params(
        self,
        params: dict[str, Any] | None,
        credentials: BinanceReadOnlyCredentials,
    ) -> dict[str, Any]:
        if not credentials.api_secret:
            reason = "Binance read-only API signing failed: BINANCE_API_SECRET is empty"
            self._log_error(
                path="-",
                reason=reason,
                action="binance_readonly_api_error",
            )
            raise BinancePrivateReadOnlyAPIError(reason)

        signed_params = dict(params or {})
        signed_params.setdefault("recvWindow", self.recv_window)
        signed_params["timestamp"] = self._current_timestamp_ms()
        try:
            query = urlencode(signed_params, doseq=True)
            signed_params["signature"] = hmac.new(
                credentials.api_secret.encode("utf-8"),
                query.encode("utf-8"),
                sha256,
            ).hexdigest()
        except Exception as exc:
            reason = f"Binance read-only API signing failed: {exc}"
            self._log_error(
                path="-",
                reason=reason,
                params=signed_params,
                action="binance_readonly_api_error",
            )
            raise BinancePrivateReadOnlyAPIError(reason) from exc
        return signed_params

    def _signed_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        credentials = self._require_readonly_credentials()
        request_params = self._signed_params(params, credentials)
        url = f"{self.base_url}{path}"
        headers = {"X-MBX-APIKEY": credentials.api_key or ""}

        try:
            response = requests.get(
                url,
                params=request_params,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            response_text = exc.response.text if exc.response is not None else ""
            status_code = exc.response.status_code if exc.response is not None else "unknown"
            reason = f"Binance read-only API returned HTTP {status_code} for {path}; body={response_text}"
            self._log_error(
                path=path,
                reason=reason,
                params=request_params,
                action="binance_readonly_api_error",
            )
            raise BinancePrivateReadOnlyAPIError(reason) from exc
        except requests.exceptions.RequestException as exc:
            reason = f"Binance read-only API request failed for {path}: {type(exc).__name__}"
            self._log_error(
                path=path,
                reason=reason,
                params=request_params,
                action="binance_readonly_api_error",
            )
            raise BinancePrivateReadOnlyAPIError(reason) from exc
        except ValueError as exc:
            reason = f"Binance read-only API returned invalid JSON for {path}: {exc}"
            self._log_error(
                path=path,
                reason=reason,
                params=request_params,
                action="binance_readonly_api_error",
            )
            raise BinancePrivateReadOnlyAPIError(reason) from exc

    def _signed_post_test_order(self, params: dict[str, Any]) -> Any:
        path = "/api/v3/order/test"
        credentials = self._require_readonly_credentials()
        request_params = self._signed_params(params, credentials)
        url = f"{self.base_url}{path}"
        headers = {"X-MBX-APIKEY": credentials.api_key or ""}

        try:
            response = requests.post(
                url,
                params=request_params,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            if not response.text.strip():
                return {}
            return response.json()
        except requests.exceptions.HTTPError as exc:
            response_text = exc.response.text if exc.response is not None else ""
            status_code = exc.response.status_code if exc.response is not None else "unknown"
            reason = f"Binance test order API returned HTTP {status_code} for {path}; body={response_text}"
            self._log_error(
                path=path,
                reason=reason,
                params=request_params,
                action="binance_test_order_error",
            )
            raise BinanceTestOrderAPIError(reason) from exc
        except requests.exceptions.RequestException as exc:
            reason = f"Binance test order API request failed for {path}: {type(exc).__name__}"
            self._log_error(
                path=path,
                reason=reason,
                params=request_params,
                action="binance_test_order_error",
            )
            raise BinanceTestOrderAPIError(reason) from exc
        except ValueError as exc:
            reason = f"Binance test order API returned invalid JSON for {path}: {exc}"
            self._log_error(
                path=path,
                reason=reason,
                params=request_params,
                action="binance_test_order_error",
            )
            raise BinanceTestOrderAPIError(reason) from exc

    def _signed_post_real_order(self, params: dict[str, Any]) -> Any:
        path = "/api/v3/order"
        credentials = self._require_readonly_credentials()
        request_params = self._signed_params(params, credentials)
        url = f"{self.base_url}{path}"
        headers = {"X-MBX-APIKEY": credentials.api_key or ""}

        try:
            response = requests.post(
                url,
                params=request_params,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            response_text = exc.response.text if exc.response is not None else ""
            status_code = exc.response.status_code if exc.response is not None else "unknown"
            reason = f"Binance real order API returned HTTP {status_code} for {path}; body={response_text}"
            self._log_error(
                path=path,
                reason=reason,
                params=request_params,
                action="binance_real_order_error",
            )
            raise BinanceRealOrderAPIError(reason) from exc
        except requests.exceptions.RequestException as exc:
            reason = f"Binance real order API request failed for {path}: {type(exc).__name__}"
            self._log_error(
                path=path,
                reason=reason,
                params=request_params,
                action="binance_real_order_error",
            )
            raise BinanceRealOrderAPIError(reason) from exc
        except ValueError as exc:
            reason = f"Binance real order API returned invalid JSON for {path}: {exc}"
            self._log_error(
                path=path,
                reason=reason,
                params=request_params,
                action="binance_real_order_error",
            )
            raise BinanceRealOrderAPIError(reason) from exc

    def _real_order_method_enabled(self) -> bool:
        try:
            settings = load_project_config()
        except Exception as exc:
            self._log_error(
                path="/api/v3/order",
                reason=f"real_order_method_blocked: config load failed: {exc}",
                action="binance_real_order_blocked",
            )
            return False
        safety = settings.get("safety", {})
        return (
            bool(safety.get("real_order_method_enabled", False))
            and os.environ.get(FINAL_REAL_ORDER_ENV_VAR) == FINAL_REAL_ORDER_ENV_VALUE
        )

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

    def create_test_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float | None = None,
        quote_order_qty: float | None = None,
    ) -> dict[str, Any]:
        normalized_symbol = symbol.upper()
        normalized_side = side.upper()
        normalized_type = order_type.upper()
        result = {
            "ok": False,
            "symbol": normalized_symbol,
            "side": normalized_side,
            "type": normalized_type,
            "dry_run_exchange_validated": False,
            "raw_response": None,
            "error": None,
        }

        params: dict[str, Any] = {
            "symbol": normalized_symbol,
            "side": normalized_side,
            "type": normalized_type,
        }
        if normalized_type == "MARKET" and normalized_side == "BUY":
            if quote_order_qty is None or quote_order_qty <= 0:
                result["error"] = "MARKET BUY test order requires positive quoteOrderQty"
                self._log_error(
                    path="/api/v3/order/test",
                    reason=str(result["error"]),
                    params=params,
                    action="binance_test_order_error",
                )
                return result
            params["quoteOrderQty"] = quote_order_qty
        elif normalized_type == "MARKET" and normalized_side == "SELL":
            if quantity is None or quantity <= 0:
                result["error"] = "MARKET SELL test order requires positive quantity"
                self._log_error(
                    path="/api/v3/order/test",
                    reason=str(result["error"]),
                    params=params,
                    action="binance_test_order_error",
                )
                return result
            params["quantity"] = quantity
        else:
            if quantity is not None:
                params["quantity"] = quantity
            if quote_order_qty is not None:
                params["quoteOrderQty"] = quote_order_qty

        try:
            raw_response = self._signed_post_test_order(params)
        except Exception as exc:
            result["error"] = str(exc)
            return result

        result.update(
            {
                "ok": True,
                "dry_run_exchange_validated": True,
                "raw_response": raw_response,
                "error": None,
            }
        )
        return result

    def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float | None = None,
        quote_order_qty: float | None = None,
    ) -> dict[str, Any]:
        normalized_symbol = symbol.upper()
        normalized_side = side.upper()
        normalized_type = order_type.upper()
        result = {
            "ok": False,
            "symbol": normalized_symbol,
            "side": normalized_side,
            "type": normalized_type,
            "real_order_sent": False,
            "raw_response": None,
            "error": None,
        }

        if not self._real_order_method_enabled():
            result["error"] = "real_order_method_blocked"
            self._log_error(
                path="/api/v3/order",
                reason="real_order_method_blocked",
                params={
                    "symbol": normalized_symbol,
                    "side": normalized_side,
                    "type": normalized_type,
                },
                action="binance_real_order_blocked",
            )
            return result

        params: dict[str, Any] = {
            "symbol": normalized_symbol,
            "side": normalized_side,
            "type": normalized_type,
        }
        if quantity is not None:
            params["quantity"] = quantity
        if quote_order_qty is not None:
            params["quoteOrderQty"] = quote_order_qty

        try:
            raw_response = self._signed_post_real_order(params)
        except Exception as exc:
            result["error"] = str(exc)
            return result

        result.update(
            {
                "ok": True,
                "real_order_sent": True,
                "raw_response": raw_response,
                "error": None,
            }
        )
        return result

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

    def get_account_info(self) -> dict[str, Any]:
        return self._signed_get("/api/v3/account")

    def get_account_balances(self) -> list[dict[str, Any]]:
        account_info = self.get_account_info()
        balances = account_info.get("balances", [])
        if not isinstance(balances, list):
            reason = "Binance read-only API returned invalid account balances payload"
            self._log_error(
                path="/api/v3/account",
                reason=reason,
                action="binance_readonly_api_error",
            )
            raise BinancePrivateReadOnlyAPIError(reason)
        return balances

    def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol.upper()} if symbol else None
        payload = self._signed_get("/api/v3/openOrders", params)
        if not isinstance(payload, list):
            reason = "Binance read-only API returned invalid open orders payload"
            self._log_error(
                path="/api/v3/openOrders",
                reason=reason,
                params=params,
                action="binance_readonly_api_error",
            )
            raise BinancePrivateReadOnlyAPIError(reason)
        return payload

    def get_my_trades(self, symbol: str, limit: int = 100) -> list[dict[str, Any]]:
        if limit <= 0:
            reason = "Binance read-only API myTrades limit must be greater than 0"
            params = {"symbol": symbol.upper(), "limit": limit}
            self._log_error(
                path="/api/v3/myTrades",
                reason=reason,
                params=params,
                action="binance_readonly_api_error",
            )
            raise BinancePrivateReadOnlyAPIError(reason)
        params = {"symbol": symbol.upper(), "limit": limit}
        payload = self._signed_get("/api/v3/myTrades", params)
        if not isinstance(payload, list):
            reason = "Binance read-only API returned invalid myTrades payload"
            self._log_error(
                path="/api/v3/myTrades",
                reason=reason,
                params=params,
                action="binance_readonly_api_error",
            )
            raise BinancePrivateReadOnlyAPIError(reason)
        return payload
