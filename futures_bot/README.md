# Futures Bot

Independent Binance Futures bot module skeleton.

## Current Scope

- Runs separately from the existing spot bot.
- Reads only `config/futures_settings.yaml` and `config/futures_symbols.yaml`.
- Defaults to `paper` mode.
- Does not read API keys.
- Does not place real orders.
- Does not use spot `config/settings.yaml` or `config/symbols.yaml`.

## Start

```bash
python3 -m futures_bot.run_futures_bot
```

This phase only verifies the futures configuration boundary and package layout.
