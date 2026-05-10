# OPERATIONS.md

日常运维速查。

## 目录

- 开发目录：`/Users/eason/traderbot_dev`
- 生产目录：`/Users/eason/traderbot_prod`
- 旧目录：`/Users/eason/traderbot`，不应再用于生产启动。

## 端口

- Dev 前端：`http://127.0.0.1:8001`
- Prod 前端：`http://127.0.0.1:8000`

## Dev 启动

```bash
cd /Users/eason/traderbot_dev
python3 -m uvicorn web_app:app --host 127.0.0.1 --port 8001
```

Spot/Futures 命令行状态检查：

```bash
cd /Users/eason/traderbot_dev
python3 status.py
python3 futures_bot/status_futures.py --strategy-signal BIOUSDT
```

Onchain 状态与 Paper 命令：

```bash
python3 onchain_bot/status_onchain.py --symbols
python3 onchain_bot/status_onchain.py --readiness
python3 onchain_bot/status_onchain.py --quote-cache
python3 onchain_bot/status_onchain.py --health
python3 onchain_bot/status_onchain.py --manual-live-health
python3 onchain_bot/status_onchain.py --live-preview GOOGLUSDT --direction buy --amount-usdt 20
python3 onchain_bot/status_onchain.py --live-preview GOOGLUSDT --direction sell --amount-token 0.1
python3 onchain_bot/run_onchain_paper_once.py
python3 onchain_bot/run_onchain_paper_loop.py
```

Onchain 当前只允许 quote + paper，不支持 approve/swap/sign/broadcast。
Onchain Live Preview 当前只是 dry run：不会签名，不会交易，不会生成 approve/swap 交易，真实 swap 仍未实现。

## 手动链上交易辅助流程

当前 Onchain 只提供人工手动链上交易辅助：

1. 在 Onchain 页面确认 mapping。
2. 点击测试报价，确认 quote 可用。
3. 查看 readiness、风控和执行时段。
4. 查看 Live Preview / Dry Run。
5. 查看 tx data preview；如果当前阶段显示未实现，不要进入真实交易。
6. 在 OKX Web3 Wallet 中手动执行。
7. 把 tx hash 记录回 Onchain 页面。
8. 点击刷新交易状态。

明确限制：

- 机器人不会保存私钥。
- 机器人不会签名。
- 机器人不会广播交易。
- 机器人不会自动真实 swap。

## Prod 启动 / 停止 / 状态

```bash
cd /Users/eason/traderbot_prod
bash scripts/start_prod.sh
bash scripts/stop_prod.sh
bash scripts/status_prod.sh
```

`status_prod.sh` 应显示：

- `prod web_app.py process: running`
- `prod web_app.py process path: /Users/eason/traderbot_prod/web_app.py`
- `prod run_bot.py process: running`
- `prod run_bot.py process path: /Users/eason/traderbot_prod/run_bot.py`

## 安全发布

从 dev 目录执行：

```bash
cd /Users/eason/traderbot_dev
bash scripts/safe_deploy_to_prod.sh
```

发布脚本应保留生产配置，不覆盖：

- `.env`
- `config/settings.yaml`
- `config/symbols.yaml`
- `config/futures_settings.yaml`
- `config/futures_symbols.yaml`
- `config/runtime_safety.yaml`
- `data/`
- `logs/`

## API key 配置

API key 放在生产或开发目录各自的 `.env` 中。

不要提交 `.env`，不要在文档或日志中粘贴真实 key。

## 8000 被旧 web_app.py 占用时

排查：

```bash
pgrep -af web_app.py
lsof -i :8000
ps -p PID -o pid=,command=
```

正确生产路径必须是：

```text
/Users/eason/traderbot_prod/web_app.py
```

如果看到旧路径：

```text
/Users/eason/traderbot/web_app.py
```

先停止生产并重新安装 launchd：

```bash
cd /Users/eason/traderbot_prod
bash scripts/stop_prod.sh
bash scripts/install_launchd.sh
bash scripts/status_prod.sh
```
