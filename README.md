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
- 支持只读账户页面 `/account`
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

默认仍然只使用 `paper`，不启用实盘交易。第六阶段加入了安全 live execution framework，但当前 `live` 路径仍然只会进入 simulation，不会调用 Binance 真实下单接口。

进入 live simulation 需要同时满足：

- `app.mode=live`
- `safety.allow_live_trading=true`
- `safety.live_execute_enabled=true`
- 环境变量 `TRADEBOT_CONFIRM_LIVE=YES`

如果任何条件不满足，系统会回落到 `PaperBroker`。

真实交易状态还需要额外满足：

- `safety.require_manual_confirm=true`
- 环境变量 `TRADEBOT_EXECUTE_REAL=YES`

当前阶段即使显示 `REAL TRADING ENABLED`，代码仍然只返回 simulation broker，不实现真实下单，也不接入 Binance 下单 API。

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

## Phase 4 Binance Public Market Data

第四阶段只接入 Binance Spot 公共 API，用于本地 `paper` 模式的真实行情和交易规则校验。

- 不需要 Binance API key
- 不读取私钥
- 不读取账户信息
- 不支持实盘下单
- 不实现 live broker
- K 线数据来自 Binance 公共 API `/api/v3/klines`
- ticker 当前价来自 Binance 公共 API `/api/v3/ticker/price`
- 下单规则校验使用 Binance `exchangeInfo` 中的 `PRICE_FILTER`、`LOT_SIZE`、`MARKET_LOT_SIZE`、`MIN_NOTIONAL` 和 `NOTIONAL`
- 交易规则只做进程内缓存，默认有效期 1 小时，可通过 `binance.rules_cache_ttl_seconds` 配置
- SQLite 仍只保存交易、持仓、权益和收益数据，不保存 K 线，不保存交易规则

Dashboard 会显示 Binance public API 状态，包括 `base_url`、ping、server time、主要 symbol、ticker price 和 exchangeInfo 获取状态。API 失败时会在页面显示错误原因，并写入 `logs/error.log`。

## Phase 5 Read-only Binance Account Integration

第五阶段接入 Binance 私有 signed endpoint 的只读账户查询能力，只用于本地安全对账和余额查看。

### API Key Configuration

API key 不写入 `config/settings.yaml`，只允许通过本地 `.env` 或环境变量提供：

```bash
BINANCE_API_KEY=your_read_only_api_key_here
BINANCE_API_SECRET=your_read_only_api_secret_here
```

仓库提供 `.env.example` 作为占位示例；真实 `.env` 已被 `.gitignore` 忽略，不应提交到 GitHub。

创建 Binance API key 时必须只开启读取权限：

- 允许：只读账户查询
- 禁止：现货/合约交易权限
- 禁止：提现权限
- 禁止：任何下单、撤单或资金转出权限

### Read-only Scope

当前只实现以下只读查询：

- `GET /api/v3/account`
- `GET /api/v3/openOrders`
- `GET /api/v3/myTrades`

本地控制台 `/account` 会显示 API key 是否已配置、账户查询状态、USDT 余额、非零资产余额和更新时间，但不会显示 API key 或 secret 内容。

机器人启动时，如果检测到只读 API key，会读取账户余额和 open orders 做安全对账，并将结果写入 `logs/system.log`。如果发现真实账户存在非零资产或 open orders，只提示 warning，不会自动操作。

如果未配置 API key，系统仍可正常以 `paper` 模式运行；Dashboard 和 `/account` 会显示未配置提示。

## Phase 6 Safe Live Execution Framework

第六阶段增加了 live broker 骨架和多重安全保护，但仍然不支持真实下单。

### Broker Modes

Dashboard 会显示当前 broker：

- `paper`：默认模式；本地 paper broker
- `live_simulation`：live gate 通过，但未设置真实执行二次确认
- `live_enabled`：所有 live 与真实执行确认条件都满足；当前仍然只返回 `LiveBroker` simulation

Dashboard 同时显示 live gate 状态和 `REAL TRADING ENABLED / DISABLED`。

重要：第六阶段没有真实交易 broker。`runtime/state.py:create_broker()` 在当前阶段只会返回 `PaperBroker` 或 simulation-only `LiveBroker`，不会返回真实 Binance 下单 broker。

### Safety Gates

live 路径至少需要：

- `app.mode=live`
- `safety.allow_live_trading=true`
- `safety.live_execute_enabled=true`
- `TRADEBOT_CONFIRM_LIVE=YES`

真实执行状态还需要：

- `safety.require_manual_confirm=true`
- `TRADEBOT_EXECUTE_REAL=YES`

任一 live gate 条件不满足时，系统使用 `PaperBroker`。即使所有条件满足，当前阶段也只调用 `LiveBroker` simulation。

`TRADEBOT_EXECUTE_REAL=YES` 只影响 Dashboard 和启动日志里的真实交易状态显示，不会开启真实 Binance 下单 API。

### Order Risk Checks

下单前会执行额外风控：

- `risk.max_single_order_usdt` 限制单笔最大 USDT 金额，默认 `20`
- `order_amount > max_single_order_usdt` 会拒绝，reason 为 `max_single_order_usdt_exceeded`
- live simulation 下会通过 Binance 只读 API `get_account_balances()` 检查 USDT 可用余额
- `USDT free < order_amount` 会拒绝，reason 为 `insufficient_balance`
- 余额查询失败时按保守失败处理，不自动借贷

### Explicit Non-Goals

当前阶段不会：

- 调用 Binance 真实下单 API
- 调用 Binance 真实撤单 API
- 自动借贷
- 通过 Dashboard 启用实盘下单

即使配置了 Binance API key，当前项目也只会使用只读账户查询和 simulation broker。
