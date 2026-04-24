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
- 只支持 `paper` 模式
- 不支持实盘交易

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
