# Futures Bot

Independent Binance Futures bot module.

## Current Scope

- Runs separately from the existing spot bot.
- Reads only `config/futures_settings.yaml` and `config/futures_symbols.yaml`.
- Defaults to `paper` mode.
- Does not read API keys.
- Does not place real orders.
- Does not use spot `config/settings.yaml` or `config/symbols.yaml`.

## Futures Strategy Phase

- The spot trend-following idea has been migrated into a Futures-only `trend_long` strategy.
- `trend_long` only supports LONG, HOLD, and CLOSE signals; SHORT is intentionally disabled.
- Strategies only generate signals and never call brokers, order validators, or Binance order endpoints.
- Real execution remains disabled.
- Any simulated execution goes through Futures risk checks first, then the Futures paper broker.
- Market inputs use Binance USD-M Futures Klines, mark price, and funding rate.

## Start

```bash
python3 -m futures_bot.run_futures_bot
```

This phase runs the Futures strategy loop in paper mode only.
