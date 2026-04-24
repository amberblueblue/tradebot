from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_BASE_URL = "https://api.binance.com"


@dataclass(frozen=True)
class BinanceCredentials:
    api_key: str = ""
    api_secret: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_secret)


class BinanceClient:
    """Small Binance REST client for market/account reads only."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = "",
        api_secret: str = "",
        recv_window: int = 5000,
        timeout: int = 15,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.credentials = BinanceCredentials(api_key=api_key, api_secret=api_secret)
        self.recv_window = recv_window
        self.timeout = timeout

    def _request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        signed: bool = False,
    ) -> Any:
        request_params = dict(params or {})
        headers: dict[str, str] = {}

        if signed:
            if not self.credentials.is_configured:
                raise PermissionError("Binance API key/secret not configured")
            request_params["timestamp"] = int(time.time() * 1000)
            request_params["recvWindow"] = self.recv_window

        query_string = urllib.parse.urlencode(request_params, doseq=True)
        if signed:
            signature = hmac.new(
                self.credentials.api_secret.encode("utf-8"),
                query_string.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            query_string = f"{query_string}&signature={signature}"
            headers["X-MBX-APIKEY"] = self.credentials.api_key

        url = f"{self.base_url}{path}"
        if query_string:
            url = f"{url}?{query_string}"

        request = urllib.request.Request(url=url, headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _graceful_private_response(
        self,
        endpoint: str,
        fallback: Any,
    ) -> dict[str, Any]:
        return {
            "available": False,
            "endpoint": endpoint,
            "reason": "missing_api_credentials",
            "data": fallback,
        }

    def get_klines(self, symbol: str, interval: str, limit: int = 200) -> list[list[Any]]:
        return self._request(
            "/api/v3/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )

    def get_account_balance(self) -> dict[str, Any]:
        if not self.credentials.is_configured:
            return self._graceful_private_response("account", [])

        try:
            payload = self._request("/api/v3/account", signed=True)
        except (urllib.error.URLError, PermissionError) as exc:
            return {
                "available": False,
                "endpoint": "account",
                "reason": str(exc),
                "data": [],
            }

        balances = [
            balance
            for balance in payload.get("balances", [])
            if float(balance.get("free", 0.0)) > 0 or float(balance.get("locked", 0.0)) > 0
        ]
        return {"available": True, "endpoint": "account", "data": balances}

    def get_symbol_info(self, symbol: str) -> dict[str, Any]:
        return self._request("/api/v3/exchangeInfo", {"symbol": symbol})

    def get_open_orders(self, symbol: str | None = None) -> dict[str, Any]:
        if not self.credentials.is_configured:
            return self._graceful_private_response("openOrders", [])

        params = {"symbol": symbol} if symbol else {}
        try:
            payload = self._request("/api/v3/openOrders", params, signed=True)
        except (urllib.error.URLError, PermissionError) as exc:
            return {
                "available": False,
                "endpoint": "openOrders",
                "reason": str(exc),
                "data": [],
            }
        return {"available": True, "endpoint": "openOrders", "data": payload}

    def get_position_info(self, symbol: str | None = None) -> dict[str, Any]:
        # Spot API has balances rather than futures-style position objects.
        # We return an empty structure for now and keep the method for future derivatives support.
        return {
            "available": True,
            "endpoint": "positionInfo",
            "symbol": symbol,
            "data": [],
            "note": "Spot trading has no standalone position endpoint; use balances/open orders instead.",
        }
