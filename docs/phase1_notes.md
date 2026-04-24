# Phase 1 Step 1 Notes

## 本次重构目标

- 保留现有 `strategy/` 与 `observability/` 逻辑
- 新增基础目录骨架，先整理项目结构
- 将运行参数从代码常量迁移到统一配置文件
- 保证 `backtester.py` 入口仍可直接运行

## 当前目录职责

- `config/`: 统一配置文件与加载逻辑
- `execution/`: broker 抽象、paper 模拟交易、后续 live broker 接口
- `exchange/`: 交易所 API 封装，例如 Binance REST client
- `logs/`: 本地运行日志输出目录
- `runtime/`: 运行模式控制、broker 选择、最小 demo
- `strategy/`: 策略信号、状态机、风控
- `observability/`: 回测事件、指标、报表

## 配置文件说明

- `config/settings.yaml`
  - 运行模式
  - 交易所名称
  - Binance API 访问配置
  - 默认时间周期
  - 轮询间隔
  - paper 模式状态文件与日志文件
  - 策略参数
  - 风控参数
  - 指标参数
  - 日志级别
- `config/symbols.yaml`
  - 默认 symbol 列表
  - 不同 symbol 在回测模式下的数据文件路径

## 兼容性说明

- `StrategyConfig()` 仍然可以像以前一样直接无参构造
- `StrategyConfig.from_settings()` 支持从配置文件加载
- `feature_engine.add_features(df)` 仍然保留原始调用方式
- `backtester.py` 仍然保留原有主入口，只是改为优先读取配置

## 最小运行方式

```bash
./.venv/bin/python backtester.py
```

## Paper 模式说明

- `app.mode: paper` 时，只允许创建 `PaperBroker`
- `live` 交易本阶段仅保留接口，不会真正下单
- 模拟成交会写入 `logs/paper_trades.jsonl`
- 模拟盘状态会保存在 `runtime/paper_state.json`
