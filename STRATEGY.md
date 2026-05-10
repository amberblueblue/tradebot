# STRATEGY.md

本项目的策略说明。这里描述当前机器人实际承担的交易框架、已启用逻辑和参数含义。

## 投资框架

整体分为三层：

- 近端：几天到一两周的趋势交易，主要抓主升段和近端延续。
- 中端：更长周期的波段或趋势配置，未来再单独设计。
- 远端：长期仓位和关键资产配置，人工操作，不交给机器人自动处理。

当前机器人主要负责近端交易。

中端策略暂不做自动化。远端交易保留人工判断，不由 Spot/Futures bot 自动执行。

## 当前策略定位

当前 Spot 和 Futures 的主线都是 near-term swing，即近端趋势交易。

核心思想：

- 用趋势周期判断大方向。
- 用信号周期寻找较近的入场和退出信号。
- 对没涨起来、占用资金太久的仓位做时间止损。
- 对已经进入较大盈利状态的仓位，不因为时间到期强行卖出，而交给技术止盈、利润保护和趋势破坏退出。

`max_hold_bars` 当前按 `trend_timeframe` 计算，不按 `signal_timeframe` 计算。

示例：

```text
trend_timeframe = 4h
max_hold_bars = 60
```

含义是最多持仓约 60 根 4h K 线，约 10 天。

## Spot 策略

Spot 当前使用 `strategy/` 下的状态机和风控逻辑。

### Spot 入场条件

Spot 入场分两层：

1. 趋势潜力

趋势周期数据需要满足：

- EMA44 > EMA144
- EMA44 相比回看周期继续上行
- 收盘价 > EMA44
- MACD line > MACD signal
- MACD histogram >= 0

2. 信号确认

信号周期数据需要满足：

- 没有 MACD 顶背离
- 没有 head chop 风险
- MACD line > MACD signal，或 MACD histogram 比上一根增强
- RSI 未达到过热阈值

当状态从 `TREND_OK` 进入 `IN_POSITION` 时，生成 `BUY`。

### Spot 出场条件

Spot 出场由 `evaluate_exit` 管理，主要包括：

- 硬止损
- 大阴线跌破 EMA144
- 确认跌破 EMA144
- RSI 过热分批止盈
- MACD 顶背离分批止盈
- 时间止损
- 利润回吐

### Spot 硬止损

当当前收益率小于等于 `-stop_loss_pct` 时触发全平。

参数：

- `stop_loss_pct`

### Spot EMA144 跌破退出

当前启用两类 EMA144 风险退出：

- 大 K 线跌破 EMA144：实体显著大于历史均值，且收盘跌破 EMA144。
- 确认跌破 EMA144：上一根收盘低于 EMA144，当前开盘和收盘也低于 EMA144。

相关参数：

- `big_candle_multiplier`
- `big_candle_body_lookback`

### Spot 分批止盈

当前启用两类分批止盈：

- RSI 超过 `rsi_overheat`，且第一档未执行时，卖出 `partial1_sell_pct`。
- 出现 MACD 顶背离，且第二档未执行时，卖出 `partial2_sell_pct`。

相关参数：

- `rsi_overheat`
- `partial1_sell_pct`
- `partial2_sell_pct`

### Spot 利润回吐

当最大浮盈达到 `profit_protection_trigger_pct` 后，如果当前收益回撤到：

```text
current_return <= max_unrealized_return * (1 - profit_giveback_ratio)
```

则触发全平。

相关参数：

- `profit_protection_trigger_pct`
- `profit_giveback_ratio`

### Spot 时间止损

当持仓时间超过 `max_hold_bars` 根趋势 K 线时，检查当前收益。

- 如果 `current_return < time_stop_profit_exempt_pct`，触发时间止损。
- 如果 `current_return >= time_stop_profit_exempt_pct`，不触发时间止损，继续由技术止盈、利润回吐和趋势破坏管理。

相关参数：

- `max_hold_bars`
- `time_stop_profit_exempt_pct`

## Futures trend_long 策略

Futures 当前主策略是 `trend_long`。

`trend_long_test` 是测试策略，不是当前主线说明重点。

### Futures 入场条件

Futures 入场需要先通过趋势过滤：

- EMA fast > EMA slow
- EMA fast 相比回看位置继续上行
- 收盘价 > EMA fast
- MACD line > MACD signal
- MACD histogram >= 0

然后信号周期触发 long：

- 收盘价 > EMA44
- EMA44 > EMA144
- MACD line > MACD signal
- MACD histogram >= previous MACD histogram
- RSI < `max_rsi`
- RSI >= `min_rsi`
- mark price >= EMA fast * 0.995
- 当前没有 MACD 顶背离
- funding rate 不能超过 `max_funding_rate_abs`

满足后输出 `LONG`，reason 为 `trend_long_entry`。

### Futures 出场条件

Futures 持仓后的退出顺序主要包括：

- 硬止损
- 大 K 线跌破 EMA
- 确认跌破 EMA
- RSI 过热分批止盈
- MACD 顶背离分批止盈
- 利润回吐
- 时间止损
- 趋势变弱退出

### Futures 硬止损

当当前收益率小于等于 `-stop_loss_pct` 时，全平。

reason：

```text
FUTURES_HARD_STOP
```

### Futures EMA144 跌破退出

当前启用：

- `FUTURES_BIG_CANDLE_EMA_BREAK`
- `FUTURES_CONFIRMED_EMA_BREAK`

它们用于处理趋势被明显破坏的情况。

相关参数：

- `ema_slow`
- `big_candle_multiplier`
- `big_candle_body_lookback`

### Futures 分批止盈

当前启用：

- RSI > `rsi_overheat` 且第一档未完成时，执行 `CLOSE_PARTIAL_30`。
- MACD 顶背离且第二档未完成时，执行 `CLOSE_PARTIAL_50`。

reason：

```text
FUTURES_RSI_OVERHEAT_PARTIAL
FUTURES_MACD_BEAR_DIV_PARTIAL
```

## Onchain Bot 当前阶段

Onchain Bot 目前只处于 quote + paper 阶段，用来验证 Binance Futures 信号和链上 token 映射是否能形成可执行的模拟流程。

当前支持：

- OKX DEX quote-only 查询。
- Onchain token mapping 手动配置。
- 使用 Futures signal 作为信号源。
- Onchain Paper open/close 模拟。
- Quote cache、readiness、health 状态检查。
- 美股常规交易时段过滤，`us_regular` 只允许在 America/New_York 09:30-16:00 执行 paper。
- Onchain 专属 quote/risk/trade-limit/safety 检查。

当前不支持：

- 不支持真实 swap。
- 不支持 approve。
- 不保存私钥。
- 不读取 seed phrase。
- 不做钱包签名。
- 不广播链上交易。

真实链上交易必须进入后续阶段后再设计和验收。当前所有 Onchain 执行都必须保持 paper-only。

相关参数：

- `rsi_overheat`
- `partial1_sell_pct`
- `partial2_sell_pct`

### Futures 利润回吐

当最大浮盈达到 `profit_protection_trigger_pct` 后，如果当前收益回吐到保护线，则全平。

reason：

```text
FUTURES_PROFIT_GIVEBACK_EXIT
```

相关参数：

- `profit_protection_trigger_pct`
- `profit_giveback_ratio`

### Futures 时间止损

`holding_bars` 当前基于 `trend_timeframe`。

当：

```text
holding_bars > max_hold_bars
```

如果：

```text
current_return < time_stop_profit_exempt_pct
```

触发：

```text
FUTURES_TIME_STOP_EXIT
```

如果：

```text
current_return >= time_stop_profit_exempt_pct
```

则设置 `time_stop_exempted = true`，不按时间止损退出。

这用于释放“持仓很久但没涨起来”的仓位，同时让已经进入主升浪的仓位继续运行。

### Futures 趋势变弱退出

如果趋势不再 bullish，或信号周期出现动能变弱，会触发全平。

reason：

```text
FUTURES_TREND_WEAK_EXIT
```

## 参数说明

### 通用策略参数

- `ema_fast`：快速 EMA 周期，Futures trend_long 已启用。
- `ema_slow`：慢速 EMA 周期，Futures trend_long 已启用。
- `macd_fast`：MACD 快线周期，Futures trend_long 已启用。
- `macd_slow`：MACD 慢线周期，Futures trend_long 已启用。
- `macd_signal`：MACD 信号线周期，Futures trend_long 已启用。
- `rsi_period`：RSI 周期，Futures trend_long 已启用。
- `min_rsi`：Futures 入场最低 RSI，已启用。
- `max_rsi`：Futures 入场最高 RSI，已启用。
- `rsi_overheat`：RSI 过热阈值，Spot/Futures 分批止盈已启用。
- `ema_slope_lookback`：Spot 趋势 EMA 斜率回看，已启用。
- `macd_decay_bars`：Spot head chop / MACD 衰减检查，已启用。
- `entry_cooldown_bars`：Spot 入场冷却，已启用。
- `max_hold_bars`：最大持仓趋势 K 线数，Spot/Futures 时间止损已启用。
- `time_stop_profit_exempt_pct`：时间止损盈利豁免百分比，Spot/Futures 已启用。
- `min_expected_return`：历史遗留参数，当前时间止损不再用它判断，暂未启用。

### 风控和止盈止损参数

- `stop_loss_pct`：硬止损百分比，Spot/Futures 已启用。
- `take_profit_pct`：Spot 执行层止盈参数，是否生效取决于执行层。
- `partial1_sell_pct`：第一档分批止盈比例，Spot/Futures 已启用。
- `partial2_sell_pct`：第二档分批止盈比例，Spot/Futures 已启用。
- `big_candle_multiplier`：大 K 线实体倍数，Spot/Futures 已启用。
- `big_candle_body_lookback`：大 K 线实体均值回看数量，Spot/Futures 已启用。
- `profit_giveback_ratio`：利润回吐比例，Spot/Futures 已启用。
- `profit_protection_trigger_pct`：利润保护触发百分比，Spot/Futures 已启用。
- `max_single_order_usdt`：单笔最大下单金额，配置和风控使用。
- `max_loss_amount`：Spot 单标的最大亏损暂停使用。
- `max_leverage`：Futures 最大杠杆硬限制，已启用。
- `max_margin_per_trade_usdt`：Futures 单笔最大保证金硬限制，已启用。
- `max_position_ratio`：Futures 最大仓位占比，已启用。
- `max_funding_rate_abs`：Futures 最大资金费率绝对值，已启用。

## 配置来源

全局默认：

- Spot：`config/settings.yaml`
- Futures：`config/futures_settings.yaml`

单标的覆盖：

- Spot：`config/symbols.yaml`
- Futures：`config/futures_symbols.yaml`

优先级：

```text
symbol 级参数 > 全局参数 > 代码默认值
```

symbol override 只建议用于特殊标的。常规策略调参应优先改全局配置。
