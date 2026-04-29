from __future__ import annotations

import hmac
import time
from hashlib import sha256
from typing import Any
from urllib.parse import urlencode

import requests

from config.secrets import (
    FuturesBinanceReadOnlyCredentials,
    load_futures_binance_readonly_credentials,
)
from futures_bot.config_loader import load_futures_config
from observability.event_logger import StructuredLogger


DEFAULT_BASE_URL = "https://fapi.binance.com"
DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_RECV_WINDOW = 15000
DEFAULT_FUTURES_LOG_FILE = "logs/futures.log"
SENSITIVE_PARAM_KEYS = {"signature"}


class BinanceFuturesPublicAPIError(RuntimeError):
    """Raised when Binance USD-M Futures public data cannot be fetched."""


class BinanceFuturesReadOnlyAPIError(RuntimeError):
    """Raised when Binance USD-M Futures signed read-only data cannot be fetched."""


class BinanceFuturesClient:
    """Binance USD-M Futures REST client for public and signed read-only data.

    This client intentionally does not support order placement, order cancellation,
    leverage changes, or margin type changes.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: int | None = None,
        recv_window: int = DEFAULT_RECV_WINDOW,
        credentials: FuturesBinanceReadOnlyCredentials | None = None,
        log_file: str = DEFAULT_FUTURES_LOG_FILE,
    ) -> None:
        config = load_futures_config()
        configured_base_url = base_url or config.futures.base_url or DEFAULT_BASE_URL
        configured_timeout = timeout or config.futures.request_timeout_seconds

        self.base_url = configured_base_url.rstrip("/")
        self.timeout = configured_timeout
        self.recv_window = max(int(recv_window), DEFAULT_RECV_WINDOW)
        self.credentials = credentials
        self.logger = StructuredLogger(log_file)
        self._server_time_offset_ms: int | None = None

    def _safe_params(self, params: dict[str, Any] | None) -> dict[str, Any]:
        if not params:
            return {}
        return {
            key: ("[redacted]" if key in SENSITIVE_PARAM_KEYS else value)
            for key, value in params.items()
        }

    def _log_warning(
        self,
        *,
        action: str,
        reason: str,
        path: str = "-",
        params: dict[str, Any] | None = None,
    ) -> None:
        self.logger.log(
            symbol=(params or {}).get("symbol", "-"),
            action=action,
            level="warning",
            reason=reason,
            endpoint=path,
            params=self._safe_params(params),
        )

    def _log_error(
        self,
        *,
        action: str,
        reason: str,
        path: str = "-",
        params: dict[str, Any] | None = None,
    ) -> None:
        self.logger.log(
            symbol=(params or {}).get("symbol", "-"),
            action=action,
            level="error",
            reason=reason,
            endpoint=path,
            params=self._safe_params(params),
        )

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
            self._log_error(
                action="futures_public_api_error",
                reason=reason,
                path=path,
                params=request_params,
            )
            raise BinanceFuturesPublicAPIError(reason) from exc
        except requests.exceptions.RequestException as exc:
            reason = (
                "Binance Futures public API request failed "
                f"for {path}: {type(exc).__name__}: {exc}"
            )
            self._log_error(
                action="futures_public_api_error",
                reason=reason,
                path=path,
                params=request_params,
            )
            raise BinanceFuturesPublicAPIError(reason) from exc
        except ValueError as exc:
            reason = f"Binance Futures public API returned invalid JSON for {path}: {exc}"
            self._log_error(
                action="futures_public_api_error",
                reason=reason,
                path=path,
                params=request_params,
            )
            raise BinanceFuturesPublicAPIError(reason) from exc

    def _load_credentials(self) -> FuturesBinanceReadOnlyCredentials:
        return self.credentials or load_futures_binance_readonly_credentials()

    def _missing_credentials_error(
        self,
        credentials: FuturesBinanceReadOnlyCredentials,
    ) -> dict[str, Any]:
        missing = []
        if not credentials.api_key_configured:
            missing.append("FUTURES_BINANCE_API_KEY")
        if not credentials.api_secret_configured:
            missing.append("FUTURES_BINANCE_API_SECRET")
        message = f"Futures Binance read-only API credentials missing: {', '.join(missing)}"
        self._log_error(
            action="futures_readonly_api_key_missing",
            reason=message,
        )
        return {
            "ok": False,
            "error": "futures_api_key_missing",
            "message": message,
            "missing": missing,
        }

    def _credentials_error_if_missing(self) -> dict[str, Any] | None:
        credentials = self._load_credentials()
        if credentials.configured:
            return None
        return self._missing_credentials_error(credentials)

    def _current_timestamp_ms(self) -> int:
        if self._server_time_offset_ms is not None:
            return int(time.time() * 1000) + self._server_time_offset_ms

        try:
            request_started_ms = int(time.time() * 1000)
            payload = self.get_server_time()
            request_finished_ms = int(time.time() * 1000)
            server_time_ms = int(payload.get("serverTime"))
            local_midpoint_ms = (request_started_ms + request_finished_ms) // 2
            self._server_time_offset_ms = server_time_ms - local_midpoint_ms
            return int(time.time() * 1000) + self._server_time_offset_ms
        except Exception as exc:
            self._server_time_offset_ms = 0
            self._log_warning(
                action="futures_time_sync_warning",
                reason=f"Futures server time unavailable; using local timestamp: {exc}",
                path="/fapi/v1/time",
            )
            return int(time.time() * 1000)

    def _signed_params(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        credentials = self._load_credentials()
        if not credentials.api_secret:
            raise BinanceFuturesReadOnlyAPIError("Futures API secret is not configured")

        signed_params = dict(params or {})
        signed_params.setdefault("recvWindow", self.recv_window)
        signed_params["timestamp"] = self._current_timestamp_ms()
        query = urlencode(signed_params, doseq=True)
        signed_params["signature"] = hmac.new(
            credentials.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            sha256,
        ).hexdigest()
        return signed_params

    def _signed_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        credentials = self._load_credentials()
        if not credentials.api_key:
            raise BinanceFuturesReadOnlyAPIError("Futures API key is not configured")

        request_params = self._signed_params(params)
        url = f"{self.base_url}{path}"
        headers = {"X-MBX-APIKEY": credentials.api_key}

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
            status_code = exc.response.status_code if exc.response is not None else "unknown"
            response_text = exc.response.text if exc.response is not None else ""
            reason = (
                "Binance Futures read-only API returned "
                f"HTTP {status_code} for {path}; body={response_text}"
            )
            self._log_error(
                action="futures_readonly_api_error",
                reason=reason,
                path=path,
                params=request_params,
            )
            raise BinanceFuturesReadOnlyAPIError(reason) from exc
        except requests.exceptions.RequestException as exc:
            reason = (
                "Binance Futures read-only API request failed "
                f"for {path}: {type(exc).__name__}: {exc}"
            )
            self._log_error(
                action="futures_readonly_api_error",
                reason=reason,
                path=path,
                params=request_params,
            )
            raise BinanceFuturesReadOnlyAPIError(reason) from exc
        except ValueError as exc:
            reason = f"Binance Futures read-only API returned invalid JSON for {path}: {exc}"
            self._log_error(
                action="futures_readonly_api_error",
                reason=reason,
                path=path,
                params=request_params,
            )
            raise BinanceFuturesReadOnlyAPIError(reason) from exc

    def _signed_get_with_fallback(
        self,
        *,
        primary_path: str,
        fallback_path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        try:
            return self._signed_get(primary_path, params=params)
        except BinanceFuturesReadOnlyAPIError as exc:
            self._log_warning(
                action="futures_readonly_api_v3_fallback",
                reason=f"{primary_path} unavailable; falling back to {fallback_path}: {exc}",
                path=primary_path,
                params=params,
            )
            return self._signed_get(fallback_path, params=params)

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

    def get_futures_balance(self) -> Any:
        missing_error = self._credentials_error_if_missing()
        if missing_error is not None:
            return missing_error
        return self._signed_get_with_fallback(
            primary_path="/fapi/v3/balance",
            fallback_path="/fapi/v2/balance",
        )

    def get_futures_account_info(self) -> Any:
        missing_error = self._credentials_error_if_missing()
        if missing_error is not None:
            return missing_error
        return self._signed_get_with_fallback(
            primary_path="/fapi/v3/account",
            fallback_path="/fapi/v2/account",
        )

    def get_futures_positions(self, symbol: str | None = None) -> Any:
        missing_error = self._credentials_error_if_missing()
        if missing_error is not None:
            return missing_error
        params = {"symbol": symbol} if symbol else None
        return self._signed_get_with_fallback(
            primary_path="/fapi/v3/positionRisk",
            fallback_path="/fapi/v2/positionRisk",
            params=params,
        )

    def get_futures_open_orders(self, symbol: str | None = None) -> Any:
        missing_error = self._credentials_error_if_missing()
        if missing_error is not None:
            return missing_error
        params = {"symbol": symbol} if symbol else None
        return self._signed_get("/fapi/v1/openOrders", params=params)
