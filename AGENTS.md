# AGENTS.md

给 Codex / 自动化助手的项目约束。

## 工作目录

- 只在 `/Users/eason/traderbot_dev` 修改代码。
- 不要直接修改 `/Users/eason/traderbot_prod` 的业务代码。
- 发布到生产必须走既有脚本，不要手工覆盖生产目录。

## 生产安全

- 不要默认开启 live。
- 不要修改 `.env`。
- 不要覆盖生产 `config/*.yaml`。
- 不要删除或绕过 safety / kill switch / live gate。
- 不要执行真实下单。
- 不要修改 broker 下单方式，除非用户明确要求并确认风险。

## 交易逻辑

- 交易逻辑改动必须先说明影响范围。
- 涉及买入、卖出、止盈、止损、风控、仓位、杠杆、实盘开关的改动，要明确标出风险点。
- 如果用户只要求前端、配置读写、部署脚本或文档，不要顺手改交易逻辑。

## 配置原则

- Spot symbol 主来源是 `config/symbols.yaml`。
- Futures symbol 主来源是 `config/futures_symbols.yaml`。
- 生产配置文件不应被部署脚本覆盖。
- symbol 级参数只作为特殊覆盖使用，默认应依赖全局配置。

