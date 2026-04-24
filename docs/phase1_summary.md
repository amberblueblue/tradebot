# Phase 1 Summary

## 已具备能力

- 轻量目录结构已经整理完成
- 配置集中到 `config/settings.yaml` 与 `config/symbols.yaml`
- 回测入口 `backtester.py` 保持可运行
- 已有 Binance 行情/账户读取封装
- 已有 `PaperBroker`，支持本地模拟下单、持仓与成交记录
- 已有执行引擎，能把策略信号转换成 paper 模式执行动作
- 已有基础执行风控：
  - 禁止重复开仓
  - 单 symbol 单仓位
  - 最大持仓数限制
  - 固定下单金额 / 资金比例
  - 简单止盈止损
  - 连续错误自动停新交易
- 已有机器人启动同步：
  - cash
  - positions
  - open orders
  - enabled symbols
- 已有机器人状态机：
  - `stopped`
  - `running`
  - `paused`
  - `error`
- 已有统一日志：
  - `logs/system.log`
  - `logs/trade.log`
  - `logs/error.log`
- 已有运行状态快照：
  - `runtime/status.json`

## 还不具备能力

- 真实 live 下单仍未接通
- 尚未做交易所精度、最小下单量、手续费、滑点处理
- 尚未做真实订单生命周期管理
- 尚未做断线恢复后的账户/订单全量对账
- 尚未做本地控制台交互式管理
- 尚未做多策略、多账户、多交易所扩展

## 下一阶段建议优先级

1. 先做本地控制台与状态命令
2. 再做 live 前置校验和只读检查
3. 然后补交易所精度、最小名义金额、下单约束
4. 最后再考虑受控接入 live broker
