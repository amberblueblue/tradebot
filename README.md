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

## Futures Bot Public Data Phase

`futures_bot/` 是独立于现货机器人的 Binance USD-M Futures 模块。它使用独立配置文件：

- `config/futures_settings.yaml`
- `config/futures_symbols.yaml`

当前阶段只做 Binance USD-M Futures 公共数据读取：

- 不接 Binance API key
- 不读取 futures 账户
- 不读取余额、仓位或订单
- 不调用 signed endpoint
- 不下单
- 不影响现货机器人运行和现货交易逻辑

可用命令：

```bash
python3 futures_bot/run_futures_bot.py
python3 futures_bot/status_futures.py
python3 futures_bot/status_futures.py --market-data BTCUSDT
```

Web 控制台提供只读 Futures 页面：

```text
http://127.0.0.1:8000/futures
```

该页面只展示 `public-data-only` 状态、enabled futures symbols、ticker price、mark price、funding rate 和交易规则摘要。没有启用 futures symbol 时会显示 `No futures symbols enabled`，不会报错。

合约交易风险高于现货交易。后续阶段会单独实现 futures 风控，包括杠杆限制、保证金限制、强平距离、资金费率过滤和独立运行时安全控制；在这些风控完成前，futures bot 仍保持公共数据只读模式。

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

第六阶段增加了安全 live execution framework，但默认仍不真实下单，当前仍不支持真实实盘下单。

- `LiveBroker` 当前只做 simulation，只输出 `[LIVE_ORDER_SIMULATION]`
- 当前不调用真实 Binance order API
- live 路径需要多重开关：`app.mode=live`、`safety.allow_live_trading=true`、`safety.live_execute_enabled=true`、`TRADEBOT_CONFIRM_LIVE=YES`、`TRADEBOT_EXECUTE_REAL=YES`
- 即使多重开关全部满足，当前阶段仍然只使用 `LiveBroker` simulation
- 已有 `risk.max_single_order_usdt` 单笔金额上限校验
- 已有 `insufficient_balance` 余额不足校验
- 当前仍不支持真实实盘下单

## Phase 7 Pre-Trade Exchange Validation

第七阶段开始进入真实下单前验证，但仍然不接入自动实盘交易。

- 已支持 Binance `POST /api/v3/order/test`，用于让交易所验证订单参数，不产生真实订单
- 已封装 `BinanceClient.create_order()` 对应真实 `POST /api/v3/order`
- `create_order()` 默认不可用，必须同时满足 `safety.real_order_method_enabled=true` 和 `TRADEBOT_FINAL_REAL_ORDER=YES`
- 如果条件不满足，`create_order()` 会直接返回 `real_order_method_blocked`
- trader 自动流程当前不会调用 `create_order()`
- `LiveBroker` 仍然保持 simulation，只输出 `[LIVE_ORDER_SIMULATION]`
- 当前阶段不实现自动真实下单
- 第七阶段真实下单仅限 `status.py --real-market-buy` 手动命令；自动策略仍然只使用 simulation，不允许调用真实 Binance order API
- `status.py --exchange-test-order SYMBOL --side buy --amount AMOUNT` 会先跑本地 validator，再调用 Binance test order，且 `real_order_sent=false`
- `status.py --real-market-buy SYMBOL --amount AMOUNT` 默认 blocked；只有所有 live 和 final-real-order 开关都打开才会继续
- 手动真实 MARKET BUY 必须满足 `app.mode=live`、`safety.allow_live_trading=true`、`safety.live_execute_enabled=true`、`safety.real_order_method_enabled=true`、`TRADEBOT_CONFIRM_LIVE=YES`、`TRADEBOT_EXECUTE_REAL=YES`、`TRADEBOT_FINAL_REAL_ORDER=YES`
- 强烈建议首次真实手动买入 `amount <= 6 USDT`

## 从 dev 发布到 prod

开发环境目录为 `/Users/eason/traderbot_dev`，生产环境目录为 `/Users/eason/traderbot_prod`。生产环境由 launchd 管理，并保留自己的 `.env`、`logs/` 和 `data/tradebot.sqlite3`。

从开发环境发布到生产环境：

```bash
cd /Users/eason/traderbot_dev
bash scripts/deploy_to_prod.sh
```

发布脚本会按顺序执行：

- `checking syntax`：先对核心 Python 文件运行 `py_compile`，失败会立刻停止发布
- `stopping prod`：调用 `/Users/eason/traderbot_prod/scripts/stop_prod.sh`
- `syncing files`：使用 `rsync` 从 dev 同步代码到 prod
- `starting prod`：调用 `/Users/eason/traderbot_prod/scripts/install_launchd.sh`
- `checking status`：调用 `/Users/eason/traderbot_prod/scripts/status_prod.sh`

同步时会排除 `.env`、`logs/`、`data/tradebot.sqlite3`、`.venv/`、`__pycache__/`、`.git/`、`.DS_Store`、生产 launchd plist、生产启动脚本、pid 文件和 `runtime/*.json`，不会覆盖生产 API key、生产日志、生产数据库、生产虚拟环境、生产运行态或生产自启动管理文件。

## Health Check

控制台提供统一健康检查页面和 JSON API：

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/api/health
```

健康检查会显示 web app、bot runtime、`app.mode`、broker、real trading 状态、Binance public API ping、Binance read-only account API、SQLite 写入能力、最近 bot loop 时间、最近 error log、8000 端口说明、启用币种、live gate 状态和 API key 是否已配置。

页面和 API 不会显示真实 API key 或 secret。Account API 未配置时显示 `missing`，不会当作程序错误。健康检查不会调用真实 Binance 下单接口。

## Account-Level Consecutive Loss Risk Control

账户级连续亏损风控会从 SQLite `trades` 表读取最近已平仓成交，按时间倒序统计连续亏损次数。默认阈值：

```yaml
risk:
  max_consecutive_losing_trades: 4
```

当连续亏损次数达到阈值时，系统会设置 `account_risk_blocked=true`，原因记录为 `consecutive_losses`，并写入 `logs/system.log`。封控后自动策略禁止新开仓，但仍允许已有持仓按策略平仓；单币 `paused_by_loss`、live gate 和 broker 逻辑不受影响。

账户级封控不会自动恢复，必须手动解除：

- Dashboard 点击 `Reset Account Risk`
- 或调用 `POST /api/account_risk/reset`

命令行查看状态：

```bash
python3 status.py --account-risk-status
```

输出包括 `consecutive_losing_trades`、`account_risk_blocked` 和 `blocked_reason`。
