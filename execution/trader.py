from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

import feature_engine
import strategy.strategy as strategy
from config.loader import (
    ExecutionRuntimeConfig,
    SymbolTradingConfig,
    load_execution_runtime,
    load_project_config,
    load_symbols_config,
    save_symbols_config,
)
from execution.broker import Broker, Position
from execution.order_validator import validate_entry_order
from observability.event_logger import LogRouter
from strategy.config import StrategyConfig
from strategy.context import MarketContext
from strategy.position import PositionState
from strategy.risk import FULL_EXIT, PARTIAL_EXIT_30, PARTIAL_EXIT_50
from strategy.state import IDLE, IN_POSITION
from runtime.signal_guard import ATTEMPTED, ENTRY_SIGNAL, EXECUTED, FAILED, build_signal_record, is_same_signal
from storage.repository import StorageRepository


BUY_ACTION = "BUY"
RUNNING = "running"
ERROR_STOPPED = "error"


@dataclass(frozen=True)
class ExecutionContext:
    symbol: str
    current_price: float
    current_bar_timestamp: str
    market_context: MarketContext


class TraderEngine:
    def __init__(
        self,
        *,
        broker: Broker,
        market_client: Any,
        runtime_store: Any,
        strategy_config: StrategyConfig,
        feature_config: feature_engine.FeatureConfig,
        execution_config: ExecutionRuntimeConfig,
    ) -> None:
        self.broker = broker
        self.market_client = market_client
        self.runtime_store = runtime_store
        self.strategy_config = strategy_config
        self.feature_config = feature_config
        self.execution_config = execution_config
        self.symbol_configs = dict(execution_config.symbol_configs)
        self.logger = LogRouter(
            system_log=execution_config.system_log_file,
            trade_log=execution_config.trade_log_file,
            error_log=execution_config.error_log_file,
            mode=execution_config.mode,
        )
        self.storage: StorageRepository | None = self._build_storage()

    def run_once(self) -> None:
        if not self._reload_runtime_config():
            return

        positions = self._safe_get_positions()
        if positions is None:
            return

        had_error = False
        positions_by_symbol = {position.symbol: position for position in positions}
        self._sync_positions_with_runtime(positions_by_symbol)

        for symbol in self._active_symbols(positions_by_symbol):
            try:
                self._process_symbol(symbol, positions_by_symbol)
            except Exception as exc:
                had_error = True
                self._record_error(symbol, exc)
        snapshots_ok = self._record_snapshots(positions_by_symbol)
        if not had_error and snapshots_ok:
            self.runtime_store.reset_consecutive_errors()

    def _safe_get_positions(self) -> list[Position] | None:
        try:
            return self.broker.get_positions()
        except Exception as exc:
            self._record_error("SYSTEM", exc)
            return None

    def _process_symbol(self, symbol: str, positions_by_symbol: dict[str, Position]) -> None:
        symbol_config = self._get_symbol_config(symbol)
        current_position = positions_by_symbol.get(symbol)
        if current_position is None:
            if self._enforce_max_loss_pause(symbol, symbol_config):
                return
            if not self._is_symbol_tradable(symbol_config):
                self._log_event("symbol_skipped", symbol=symbol, reason="symbol_trading_disabled")
                return

        execution_context = self._build_execution_context(symbol)
        runtime_symbol = self.runtime_store.get_symbol_state(symbol)

        self._maybe_set_market_price(symbol, execution_context.current_price)
        self._sync_symbol_state(symbol, runtime_symbol, current_position, execution_context)
        if self._apply_exit_guards(symbol, current_position, execution_context, positions_by_symbol):
            self.runtime_store.set_symbol_state(
                symbol,
                last_bar_timestamp=execution_context.current_bar_timestamp,
            )
            return

        if self._enforce_max_loss_pause(symbol, symbol_config):
            return
        if not self._is_symbol_tradable(symbol_config):
            self._log_event("symbol_skipped", symbol=symbol, reason="symbol_trading_disabled")
            return

        runtime_symbol = self.runtime_store.get_symbol_state(symbol)
        if self._is_duplicate_entry_signal(symbol, execution_context.current_bar_timestamp):
            return
        if self._is_duplicate_bar(symbol, execution_context.current_bar_timestamp):
            self._log_event(
                "skip_duplicate_bar",
                symbol=symbol,
                price=execution_context.current_price,
                reason="bar_already_processed",
            )
            return

        position_state = self._build_position_state(runtime_symbol)
        if current_position is not None:
            position_state.update_mfe(execution_context.current_price)

        action, new_state, next_entry_price, decision = strategy.generate_signal(
            execution_context.market_context,
            runtime_symbol["strategy_state"],
            runtime_symbol["entry_price"],
            position_state=position_state,
            config=self.strategy_config,
        )

        self.runtime_store.set_symbol_state(
            symbol,
            strategy_state=new_state,
            entry_price=next_entry_price,
            entry_bar_index=position_state.entry_bar_index,
            partial1_done=position_state.partial1_done,
            partial2_done=position_state.partial2_done,
            max_unrealized_return=position_state.max_unrealized_return,
            cooldown_remaining=max(0, int(runtime_symbol["cooldown_remaining"])),
            last_bar_timestamp=execution_context.current_bar_timestamp,
        )

        if action == BUY_ACTION:
            self._handle_buy(symbol, execution_context, positions_by_symbol)
            return

        if action in {PARTIAL_EXIT_30, PARTIAL_EXIT_50, FULL_EXIT}:
            self._handle_sell(symbol, action, execution_context, positions_by_symbol, decision.exit_sell_pct)
            return

        self._log_event(
            "signal_hold",
            symbol=symbol,
            price=execution_context.current_price,
            state=new_state,
            reason=decision.reason_code,
        )

    def _build_execution_context(self, symbol: str) -> ExecutionContext:
        symbol_config = self._get_symbol_config(symbol)
        entry_interval = symbol_config.signal_timeframe
        trend_interval = symbol_config.trend_timeframe
        df_1h = self._klines_to_dataframe(
            self.market_client.get_klines(symbol, entry_interval, limit=300)
        )
        df_4h = self._klines_to_dataframe(
            self.market_client.get_klines(symbol, trend_interval, limit=300)
        )
        if df_1h.empty or df_4h.empty:
            raise ValueError(f"Missing Binance kline data for {symbol}")
        df_1h = feature_engine.add_features(df_1h, config=self.feature_config)
        df_4h = feature_engine.add_features(df_4h, config=self.feature_config)

        current_bar_index = len(df_1h) - 1
        current_bar = df_1h.iloc[-1]
        runtime_symbol = self.runtime_store.get_symbol_state(symbol)
        current_bar_timestamp = str(current_bar["timestamp"].isoformat())
        cooldown_remaining = int(runtime_symbol["cooldown_remaining"])
        if (
            runtime_symbol["last_bar_timestamp"] is not None
            and runtime_symbol["last_bar_timestamp"] != current_bar_timestamp
            and cooldown_remaining > 0
        ):
            cooldown_remaining -= 1
            self.runtime_store.set_symbol_state(symbol, cooldown_remaining=cooldown_remaining)
        market_context = MarketContext(
            df_1h=df_1h,
            df_4h=df_4h,
            current_bar_index=current_bar_index,
            cooldown_remaining=cooldown_remaining,
        )
        return ExecutionContext(
            symbol=symbol,
            current_price=float(current_bar["close"]),
            current_bar_timestamp=current_bar_timestamp,
            market_context=market_context,
        )

    def _klines_to_dataframe(self, klines: list[list[Any]]) -> pd.DataFrame:
        if not isinstance(klines, list) or not klines:
            raise ValueError("Binance returned no kline data")

        records = []
        try:
            for item in klines:
                records.append(
                    {
                        "timestamp": pd.to_datetime(int(item[0]), unit="ms", utc=True),
                        "open": float(item[1]),
                        "high": float(item[2]),
                        "low": float(item[3]),
                        "close": float(item[4]),
                        "volume": float(item[5]),
                    }
                )
        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid Binance kline payload: {exc}") from exc

        return pd.DataFrame.from_records(records).sort_values("timestamp").reset_index(drop=True)

    def _build_position_state(self, runtime_symbol: dict[str, Any]) -> PositionState:
        return PositionState(
            entry_price=runtime_symbol["entry_price"],
            entry_bar_index=runtime_symbol["entry_bar_index"],
            partial1_done=runtime_symbol["partial1_done"],
            partial2_done=runtime_symbol["partial2_done"],
            max_unrealized_return=runtime_symbol["max_unrealized_return"],
        )

    def _sync_positions_with_runtime(self, positions_by_symbol: dict[str, Position]) -> None:
        for symbol in self.symbol_configs:
            position = positions_by_symbol.get(symbol)
            runtime_symbol = self.runtime_store.get_symbol_state(symbol)
            if position is None and runtime_symbol["strategy_state"] == IN_POSITION:
                self.runtime_store.set_symbol_state(
                    symbol,
                    strategy_state=IDLE,
                    entry_price=None,
                    entry_bar_index=None,
                    partial1_done=False,
                    partial2_done=False,
                    max_unrealized_return=0.0,
                )

    def _sync_symbol_state(
        self,
        symbol: str,
        runtime_symbol: dict[str, Any],
        current_position: Position | None,
        execution_context: ExecutionContext,
    ) -> None:
        if current_position is None:
            return
        if runtime_symbol["entry_price"] is None:
            self.runtime_store.set_symbol_state(
                symbol,
                strategy_state=IN_POSITION,
                entry_price=current_position.avg_price,
                entry_bar_index=execution_context.market_context.current_bar_index,
            )

    def _apply_exit_guards(
        self,
        symbol: str,
        current_position: Position | None,
        execution_context: ExecutionContext,
        positions_by_symbol: dict[str, Position],
    ) -> bool:
        if current_position is None or current_position.avg_price <= 0:
            return False

        current_return = (execution_context.current_price - current_position.avg_price) / current_position.avg_price
        if current_return <= -(self.execution_config.stop_loss_pct / 100):
            self._log_event(
                "risk_stop_loss_triggered",
                symbol=symbol,
                price=execution_context.current_price,
                current_return=round(current_return * 100, 4),
            )
            self._execute_sell(
                symbol,
                current_position.qty,
                execution_context.current_price,
                "stop_loss",
                execution_context.current_bar_timestamp,
                FULL_EXIT,
                positions_by_symbol,
            )
            self._enforce_max_loss_pause(symbol, self._get_symbol_config(symbol))
            return symbol not in positions_by_symbol

        if current_return >= self.execution_config.take_profit_pct / 100:
            self._log_event(
                "risk_take_profit_triggered",
                symbol=symbol,
                price=execution_context.current_price,
                current_return=round(current_return * 100, 4),
            )
            self._execute_sell(
                symbol,
                current_position.qty,
                execution_context.current_price,
                "take_profit",
                execution_context.current_bar_timestamp,
                FULL_EXIT,
                positions_by_symbol,
            )
            self._enforce_max_loss_pause(symbol, self._get_symbol_config(symbol))
            return symbol not in positions_by_symbol
        return False

    def _handle_buy(
        self,
        symbol: str,
        execution_context: ExecutionContext,
        positions_by_symbol: dict[str, Position],
    ) -> None:
        symbol_config = self._get_symbol_config(symbol)
        if self.runtime_store.is_conservative_mode():
            self._log_event("order_blocked", symbol=symbol, reason="conservative_mode_enabled")
            return
        if self.runtime_store.is_error_limit_reached():
            self._log_event("order_blocked", symbol=symbol, reason="error_limit_reached")
            return
        if symbol in positions_by_symbol:
            self._log_event("order_blocked", symbol=symbol, reason="position_already_exists")
            return
        if self._is_duplicate_action(symbol, execution_context.current_bar_timestamp):
            self._log_event("order_blocked", symbol=symbol, reason="duplicate_action_bar")
            return
        if self._is_duplicate_entry_signal(symbol, execution_context.current_bar_timestamp):
            return

        try:
            qty = self._calculate_buy_qty(symbol_config, execution_context.current_price)
        except Exception as exc:
            self._record_error(symbol, exc)
            return

        validation = validate_entry_order(
            symbol_config=symbol_config,
            quantity=qty,
            price=execution_context.current_price,
            realized_pnl=self._get_symbol_realized_pnl(symbol),
            current_position_count=len(positions_by_symbol),
            max_positions=self.execution_config.max_positions,
            bot_status=self.runtime_store.get_robot_status(),
        )
        if not validation.ok:
            self._log_event(
                "order_blocked",
                symbol=symbol,
                reason=validation.reason,
                raw_quantity=validation.raw_quantity,
                normalized_quantity=validation.normalized_quantity,
                raw_price=validation.raw_price,
                normalized_price=validation.normalized_price,
                notional=validation.notional,
                normalized_amount=validation.normalized_amount,
            )
            return
        qty = validation.normalized_quantity
        if not self._refresh_paper_market_price(symbol):
            self._log_event("order_blocked", symbol=symbol, reason="ticker_price_unavailable")
            return

        self._record_entry_signal(
            symbol,
            execution_context.current_bar_timestamp,
            status=ATTEMPTED,
        )
        try:
            result = self.broker.place_market_buy(symbol, qty)
        except Exception as exc:
            self._record_entry_signal(
                symbol,
                execution_context.current_bar_timestamp,
                status=FAILED,
                error=str(exc),
            )
            self._record_error(symbol, exc)
            return

        self.runtime_store.set_symbol_state(
            symbol,
            strategy_state=IN_POSITION,
            entry_price=result.average_price,
            entry_bar_index=execution_context.market_context.current_bar_index,
            partial1_done=False,
            partial2_done=False,
            max_unrealized_return=0.0,
            last_action_bar_timestamp=execution_context.current_bar_timestamp,
            cooldown_remaining=0,
        )
        self._record_entry_signal(
            symbol,
            execution_context.current_bar_timestamp,
            status=EXECUTED,
        )
        positions_by_symbol[symbol] = Position(
            symbol=symbol,
            qty=result.filled_qty,
            avg_price=result.average_price,
            realized_pnl=0.0,
        )
        self._log_event(
            "order_filled",
            symbol=symbol,
            side="BUY",
            qty=result.filled_qty,
            price=result.average_price,
            order_id=result.order_id,
        )

    def _handle_sell(
        self,
        symbol: str,
        action: str,
        execution_context: ExecutionContext,
        positions_by_symbol: dict[str, Position],
        sell_pct: float,
    ) -> None:
        position = positions_by_symbol.get(symbol)
        if position is None or position.qty <= 0:
            self._log_event("order_blocked", symbol=symbol, reason="no_position_to_sell")
            return

        qty = position.qty if action == FULL_EXIT else position.qty * max(sell_pct, 0.0)
        if qty <= 0:
            self._log_event("order_blocked", symbol=symbol, reason="sell_qty_not_positive")
            return

        reason = "strategy_full_exit" if action == FULL_EXIT else "strategy_partial_exit"
        self._execute_sell(
            symbol,
            qty,
            execution_context.current_price,
            reason,
            execution_context.current_bar_timestamp,
            action,
            positions_by_symbol,
        )
        self._enforce_max_loss_pause(symbol, self._get_symbol_config(symbol))

    def _execute_sell(
        self,
        symbol: str,
        qty: float,
        current_price: float,
        reason: str,
        bar_timestamp: str | None = None,
        action: str = FULL_EXIT,
        positions_by_symbol: dict[str, Position] | None = None,
    ) -> None:
        if not self._refresh_paper_market_price(symbol):
            self._log_event("order_blocked", symbol=symbol, reason="ticker_price_unavailable")
            return

        try:
            result = self.broker.place_market_sell(symbol, qty)
        except Exception as exc:
            self._record_error(symbol, exc)
            return

        current_bar_timestamp = bar_timestamp or datetime.now(timezone.utc).isoformat()
        runtime_symbol = self.runtime_store.get_symbol_state(symbol)
        partial1_done = runtime_symbol["partial1_done"] or action == PARTIAL_EXIT_30
        partial2_done = runtime_symbol["partial2_done"] or action == PARTIAL_EXIT_50
        next_state = runtime_symbol["strategy_state"]
        next_entry_price = runtime_symbol["entry_price"]
        next_entry_bar_index = runtime_symbol["entry_bar_index"]
        max_unrealized_return = runtime_symbol["max_unrealized_return"]
        cooldown_remaining = runtime_symbol["cooldown_remaining"]

        remaining_position = next((item for item in self.broker.get_positions() if item.symbol == symbol), None)
        if remaining_position is None:
            next_state = IDLE
            next_entry_price = None
            next_entry_bar_index = None
            partial1_done = False
            partial2_done = False
            max_unrealized_return = 0.0
            cooldown_remaining = self.strategy_config.entry_cooldown_bars
            if positions_by_symbol is not None:
                positions_by_symbol.pop(symbol, None)
        else:
            next_state = IN_POSITION
            next_entry_price = remaining_position.avg_price
            if positions_by_symbol is not None:
                positions_by_symbol[symbol] = remaining_position

        self.runtime_store.set_symbol_state(
            symbol,
            strategy_state=next_state,
            entry_price=next_entry_price,
            entry_bar_index=next_entry_bar_index,
            partial1_done=partial1_done,
            partial2_done=partial2_done,
            max_unrealized_return=max_unrealized_return,
            last_action_bar_timestamp=current_bar_timestamp,
            cooldown_remaining=cooldown_remaining,
        )
        self._log_event(
            "order_filled",
            symbol=symbol,
            side="SELL",
            qty=result.filled_qty,
            price=result.average_price,
            order_id=result.order_id,
            reason=reason,
            trigger_price=current_price,
        )

    def _calculate_buy_qty(self, symbol_config: SymbolTradingConfig, current_price: float) -> float:
        cash_balance = self.broker.get_cash_balance()
        quote_amount = symbol_config.order_amount
        if quote_amount <= 0:
            quote_amount = cash_balance * self.execution_config.cash_usage_pct
        quote_amount = min(quote_amount, cash_balance)
        if quote_amount <= 0 or current_price <= 0:
            return 0.0
        return quote_amount / current_price

    def _build_storage(self) -> StorageRepository | None:
        try:
            return StorageRepository()
        except Exception as exc:
            self._log_storage_error("SYSTEM", "storage_init_failed", exc)
            return None

    def _record_snapshots(self, positions_by_symbol: dict[str, Position]) -> bool:
        if self.execution_config.mode != "paper" or self.storage is None:
            return True

        try:
            cash = float(self.broker.get_cash_balance())
            position_value = 0.0
            realized_pnl_total = 0.0
            unrealized_pnl_total = 0.0
            snapshot_symbols = set(self.symbol_configs) | set(positions_by_symbol)

            for symbol in sorted(snapshot_symbols):
                position = positions_by_symbol.get(symbol)
                realized_pnl = self._get_symbol_realized_pnl(symbol)
                realized_pnl_total += realized_pnl
                unrealized_pnl = 0.0

                if position is not None and position.qty > 0:
                    current_price = self._get_snapshot_price(symbol, position.avg_price)
                    market_value = position.qty * current_price
                    unrealized_pnl = (current_price - position.avg_price) * position.qty
                    position_value += market_value
                    unrealized_pnl_total += unrealized_pnl
                    self.storage.record_position_snapshot(
                        symbol=symbol,
                        quantity=position.qty,
                        avg_price=position.avg_price,
                        current_price=current_price,
                        market_value=market_value,
                        unrealized_pnl=unrealized_pnl,
                        mode=self.execution_config.mode,
                    )

                self.storage.record_symbol_pnl_snapshot(
                    symbol=symbol,
                    realized_pnl=realized_pnl,
                    unrealized_pnl=unrealized_pnl,
                    total_pnl=realized_pnl + unrealized_pnl,
                    mode=self.execution_config.mode,
                )

            self.storage.record_equity_snapshot(
                total_equity=cash + position_value,
                cash=cash,
                position_value=position_value,
                realized_pnl=realized_pnl_total,
                unrealized_pnl=unrealized_pnl_total,
                mode=self.execution_config.mode,
            )
            return True
        except Exception as exc:
            self._log_storage_error("SYSTEM", "record_snapshots_failed", exc)
            return False

    def _get_snapshot_price(self, symbol: str, fallback_price: float) -> float:
        if hasattr(self.broker, "get_market_price"):
            current_price = self.broker.get_market_price(symbol)
            if current_price is not None and current_price > 0:
                return float(current_price)
        return float(fallback_price)

    def _reload_runtime_config(self) -> bool:
        try:
            settings = load_project_config()
            execution_config = load_execution_runtime(settings)
            if execution_config.mode != "paper":
                raise ValueError("runtime hot reload supports paper mode only")
            self.execution_config = execution_config
            self.strategy_config = StrategyConfig.from_settings(settings)
            self.feature_config = feature_engine.FeatureConfig.from_dict(settings.get("feature_engine", {}))
            self.symbol_configs = dict(execution_config.symbol_configs)
            self.logger = LogRouter(
                system_log=execution_config.system_log_file,
                trade_log=execution_config.trade_log_file,
                error_log=execution_config.error_log_file,
                mode=execution_config.mode,
            )
            return True
        except Exception as exc:
            self._handle_config_reload_error(exc)
            return False

    def _handle_config_reload_error(self, exc: Exception) -> None:
        self.runtime_store.set_conservative_mode(True)
        self.runtime_store.set_robot_status("paused")
        consecutive_errors = self.runtime_store.increment_error(str(exc))
        self.logger.log_error(
            symbol="SYSTEM",
            action="config_reload_failed",
            reason="invalid_runtime_config",
            error=str(exc),
            consecutive_errors=consecutive_errors,
        )
        self._trip_error_circuit_if_needed(
            "SYSTEM",
            consecutive_errors,
            trigger="config_reload_failed",
        )
        print(f"[config_reload_failed] SYSTEM {exc}")

    def _refresh_symbol_configs(self) -> None:
        try:
            symbols_config = load_symbols_config()
        except Exception as exc:
            self._record_error("SYSTEM", exc)
            return
        symbols = symbols_config.get("symbols", {})
        if not isinstance(symbols, dict):
            self.symbol_configs = {}
            return
        refreshed_configs: dict[str, SymbolTradingConfig] = {}
        for symbol, symbol_config in symbols.items():
            refreshed_configs[symbol] = SymbolTradingConfig(
                symbol=symbol,
                enabled=bool(symbol_config["enabled"]),
                trend_timeframe=str(symbol_config["trend_timeframe"]),
                signal_timeframe=str(symbol_config["signal_timeframe"]),
                order_amount=float(symbol_config["order_amount"]),
                max_loss_amount=float(symbol_config["max_loss_amount"]),
                paused_by_loss=bool(symbol_config["paused_by_loss"]),
            )
        self.symbol_configs = refreshed_configs

    def _get_symbol_config(self, symbol: str) -> SymbolTradingConfig:
        if symbol in self.symbol_configs:
            return self.symbol_configs[symbol]
        return SymbolTradingConfig(
            symbol=symbol,
            enabled=False,
            trend_timeframe="4h",
            signal_timeframe="15m",
            order_amount=0.0,
            max_loss_amount=float("inf"),
            paused_by_loss=False,
        )

    def _tradable_symbols(self) -> tuple[str, ...]:
        return tuple(
            symbol
            for symbol, symbol_config in self.symbol_configs.items()
            if self._is_symbol_tradable(symbol_config)
        )

    def _active_symbols(self, positions_by_symbol: dict[str, Position]) -> tuple[str, ...]:
        return tuple(sorted(set(self.symbol_configs) | set(positions_by_symbol)))

    def _is_symbol_tradable(self, symbol_config: SymbolTradingConfig) -> bool:
        return symbol_config.enabled and not symbol_config.paused_by_loss

    def _get_symbol_realized_pnl(self, symbol: str) -> float:
        if hasattr(self.broker, "get_realized_pnl"):
            return float(self.broker.get_realized_pnl(symbol))
        runtime_symbol = self.runtime_store.get_symbol_state(symbol)
        return float(runtime_symbol.get("realized_pnl", 0.0))

    def _enforce_max_loss_pause(self, symbol: str, symbol_config: SymbolTradingConfig) -> bool:
        realized_pnl = self._get_symbol_realized_pnl(symbol)
        self.runtime_store.set_symbol_state(symbol, realized_pnl=realized_pnl)
        if realized_pnl > -symbol_config.max_loss_amount:
            return False

        self._pause_symbol_by_loss(symbol, symbol_config, realized_pnl)
        return True

    def _pause_symbol_by_loss(
        self,
        symbol: str,
        symbol_config: SymbolTradingConfig,
        realized_pnl: float,
    ) -> None:
        if symbol_config.paused_by_loss:
            return

        symbols_config = load_symbols_config()
        symbols = symbols_config.get("symbols", {})
        if not isinstance(symbols, dict) or symbol not in symbols:
            raise ValueError(f"Missing symbol config for {symbol}")
        symbols[symbol]["paused_by_loss"] = True
        save_symbols_config(symbols_config)

        paused_config = SymbolTradingConfig(
            symbol=symbol_config.symbol,
            enabled=symbol_config.enabled,
            trend_timeframe=symbol_config.trend_timeframe,
            signal_timeframe=symbol_config.signal_timeframe,
            order_amount=symbol_config.order_amount,
            max_loss_amount=symbol_config.max_loss_amount,
            paused_by_loss=True,
        )
        self.symbol_configs[symbol] = paused_config
        self.runtime_store.set_symbol_state(symbol, paused_by_loss=True, realized_pnl=realized_pnl)
        payload = {
            "realized_pnl": realized_pnl,
            "max_loss_amount": symbol_config.max_loss_amount,
        }
        self.logger.log_system(
            symbol=symbol,
            action="symbol_loss_paused",
            reason="max_loss_amount_reached",
            **payload,
        )
        self.logger.log_trade(
            symbol=symbol,
            action="symbol_loss_paused",
            reason="max_loss_amount_reached",
            **payload,
        )
        print(f"[symbol_loss_paused] {symbol} max_loss_amount_reached")

    def _is_duplicate_bar(self, symbol: str, current_bar_timestamp: str) -> bool:
        runtime_symbol = self.runtime_store.get_symbol_state(symbol)
        return runtime_symbol["last_bar_timestamp"] == current_bar_timestamp

    def _is_duplicate_action(self, symbol: str, current_bar_timestamp: str) -> bool:
        runtime_symbol = self.runtime_store.get_symbol_state(symbol)
        return runtime_symbol["last_action_bar_timestamp"] == current_bar_timestamp

    def _is_duplicate_entry_signal(self, symbol: str, signal_time: str) -> bool:
        runtime_symbol = self.runtime_store.get_symbol_state(symbol)
        record = runtime_symbol.get("last_signal")
        if not is_same_signal(
            record,
            symbol=symbol,
            signal_type=ENTRY_SIGNAL,
            signal_time=signal_time,
            action=BUY_ACTION,
        ):
            return False

        self._log_event(
            "order_blocked",
            symbol=symbol,
            reason="duplicate_entry_signal",
            signal_type=ENTRY_SIGNAL,
            signal_time=signal_time,
            action=BUY_ACTION,
            signal_status=record.get("status"),
            last_executed_at=record.get("last_executed_at"),
        )
        return True

    def _record_entry_signal(
        self,
        symbol: str,
        signal_time: str,
        *,
        status: str,
        error: str | None = None,
    ) -> None:
        self.runtime_store.set_symbol_state(
            symbol,
            last_signal=build_signal_record(
                symbol=symbol,
                signal_type=ENTRY_SIGNAL,
                signal_time=signal_time,
                action=BUY_ACTION,
                status=status,
                error=error,
            ),
        )

    def _maybe_set_market_price(self, symbol: str, price: float) -> None:
        if hasattr(self.broker, "set_market_price"):
            self.broker.set_market_price(symbol, price)

    def _refresh_paper_market_price(self, symbol: str) -> bool:
        if self.execution_config.mode != "paper" or not hasattr(self.broker, "set_market_price"):
            return True
        try:
            payload = self.market_client.get_ticker_price(symbol)
            price = float(payload.get("price", 0.0))
            if price <= 0:
                raise ValueError(f"Invalid Binance ticker price for {symbol}: {payload}")
            self.broker.set_market_price(symbol, price)
            return True
        except Exception as exc:
            self.logger.log_error(
                symbol=symbol,
                action="ticker_price_unavailable",
                reason="market_price_fetch_failed",
                error=str(exc),
            )
            return False

    def _record_error(self, symbol: str, exc: Exception) -> None:
        consecutive_errors = self.runtime_store.increment_error(str(exc))
        self._log_event(
            "execution_error",
            symbol=symbol,
            error=str(exc),
            consecutive_errors=consecutive_errors,
        )
        self._trip_error_circuit_if_needed(symbol, consecutive_errors, trigger="execution_error")

    def _log_storage_error(self, symbol: str, action: str, exc: Exception) -> None:
        consecutive_errors = self.runtime_store.increment_error(str(exc))
        self.logger.log_error(
            symbol=symbol,
            action=action,
            reason="sqlite_write_failed",
            error=str(exc),
            consecutive_errors=consecutive_errors,
        )
        self._trip_error_circuit_if_needed(symbol, consecutive_errors, trigger=action)

    def _trip_error_circuit_if_needed(self, symbol: str, consecutive_errors: int, *, trigger: str) -> None:
        if consecutive_errors < self.execution_config.max_consecutive_errors:
            return
        self.runtime_store.set_robot_status(ERROR_STOPPED)
        self.runtime_store.set_conservative_mode(True)
        self.logger.log_error(
            symbol=symbol,
            action="robot_status_changed",
            reason="max_consecutive_errors_reached",
            status=ERROR_STOPPED,
            trigger=trigger,
            consecutive_errors=consecutive_errors,
            max_consecutive_errors=self.execution_config.max_consecutive_errors,
        )
        print(f"[robot_status_changed] {symbol} max_consecutive_errors_reached")

    def _log_event(self, event_type: str, **payload: Any) -> None:
        symbol = payload.get("symbol", "-")
        reason = str(payload.get("reason") or payload.get("status") or "")
        action = event_type
        log_payload = {key: value for key, value in payload.items() if key not in {"symbol", "reason"}}
        if event_type == "execution_error":
            self.logger.log_error(
                symbol=symbol,
                action=action,
                reason=reason or "execution_error",
                **log_payload,
            )
        elif event_type == "order_filled":
            self.logger.log_trade(
                symbol=symbol,
                action=action,
                reason=reason or "trade_executed",
                **log_payload,
            )
        else:
            self.logger.log_system(
                symbol=symbol,
                action=action,
                reason=reason or event_type,
                **log_payload,
            )

        reason = payload.get("reason") or payload.get("status") or ""
        print(f"[{event_type}] {symbol} {reason}".strip())
