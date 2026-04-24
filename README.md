# TraderBot

个人交易者本地运行的交易执行工具。当前以 `paper` 模式和本地控制台为主，不支持实盘下单。

## Local Web Dashboard

本地控制台基于 FastAPI + Jinja2 + Bootstrap，当前支持查看状态、日志、配置，管理交易币种，以及修改本地机器人运行状态。

### Start

```bash
python3 web_app.py
```

### Address

```text
http://127.0.0.1:8000
```

### Current Scope

- 支持 `Dashboard`
- 支持 `Start / Pause / Stop` 状态切换
- 支持日志查看，并可按币种筛选 `system` / `trade` / `error` 日志
- 支持配置查看
- 支持币种管理页面 `/symbols`
- 支持实时持仓页面 `/positions`
- 支持收益看板 `/performance`
- 支持添加、删除、启用、禁用币种
- 支持编辑单币种参数：
  - `enabled`
  - `trend_timeframe`
  - `signal_timeframe`
  - `order_amount`
  - `max_loss_amount`
  - `paused_by_loss`
- 执行层会读取 `config/symbols.yaml` 中的单币种配置
- 每个币种可使用独立的信号周期、趋势周期、下单金额和最大亏损金额
- 当 `enabled=false` 或 `paused_by_loss=true` 时，该币种不会交易
- 当某个币种累计已实现亏损达到 `max_loss_amount` 时，会自动将该币种设置为 `paused_by_loss=true`，只暂停该币种，不停止整个机器人
- 使用本地 SQLite 保存 paper 交易结果、持仓快照、权益快照和单币收益快照
- `/positions` 优先读取当前 runtime / paper 状态，缺失时读取 SQLite 最近持仓快照
- `/performance` 支持总权益曲线和单币收益曲线
- 只支持 `paper` 模式
- 不支持实盘交易
- Dashboard 会显示当前 mode 和 live gate 状态
- 第三阶段加入配置热加载、重复信号保护、下单前规则校验、异常熔断和 live 硬保护

## Phase 2B Symbol Configuration

币种配置保存在 `config/symbols.yaml`，不使用数据库。默认结构示例：

```yaml
symbols:
  VIRTUALUSDT:
    enabled: true
    trend_timeframe: "4h"
    signal_timeframe: "15m"
    order_amount: 100
    max_loss_amount: 20
    paused_by_loss: false
```

`trend_timeframe` 和 `signal_timeframe` 当前只允许：

- `5m`
- `15m`
- `1h`
- `4h`
- `1d`

当前项目仍然只支持本地 `paper` 模式。`live` 实盘交易没有启用，控制台操作不会直接下实盘订单。

## Phase 2C SQLite Portfolio Data

本地 SQLite 数据库位于：

```text
data/tradebot.sqlite3
```

SQLite 只用于保存交易结果和图表展示所需的数据：

- `trades`
- `position_snapshots`
- `equity_snapshots`
- `symbol_pnl_snapshots`

K 线数据和指标数据不会写入 SQLite，仍然在运行时内存中计算。

当前仍然只支持 `paper` 模式。数据库写入失败时会记录到 error log，不会直接让机器人崩溃；实盘交易仍未启用。

## Live Trading Safety

当前 live 模式未启用，不支持实盘交易。默认只允许 `paper` 模式运行。

如果 `config/settings.yaml` 中配置了 `app.mode: live`，`run_bot.py` 会拒绝启动。未来只有同时满足以下条件，live gate 才会通过：

- `app.mode=live`
- `safety.allow_live_trading=true`
- 环境变量 `TRADEBOT_CONFIRM_LIVE=YES`

当前阶段即使 live gate 通过，也只会显示 `live broker not implemented`，不会真实下单，也不会实现 live broker。

API key 不应填写交易权限；如需填写测试或公共访问用途，也不要授予现货/合约交易权限。

## Phase 3 Safety Controls

第三阶段增加了运行时安全控制，仍然只服务于本地 `paper` 模式：

- 每次交易循环前重新读取 `config/settings.yaml` 和 `config/symbols.yaml`
- 配置读取失败时暂停开新仓，并写入 error log
- `enabled=false` 的币种不会新开仓
- `paused_by_loss=true` 的币种不会新开仓
- 同一个 symbol 在同一根信号 K 线内不会重复开同方向仓
- 已有未平仓 position 时不会重复开同方向仓
- 下单前会校验本地交易规则，包括最小交易额、价格、数量、最大持仓数量、最大亏损限制和 bot 状态
- 默认本地规则为 `min_notional=5 USDT`、金额精度 `2`、数量精度 `6`
- 连续错误达到 `safety.max_consecutive_errors` 后，bot 状态切换为 `error`，不再开新仓
- `app.mode=live` 默认拒绝启动，Dashboard 会显示当前 mode、`allow_live_trading`、`TRADEBOT_CONFIRM_LIVE` 和 live gate 状态

当前不需要 Binance API key，不支持实盘下单，也不会读取可交易 API key。

K 线和指标仍然在内存中计算；SQLite 只保存交易、持仓、收益和图表展示数据。
