# TraderBot

个人交易者本地运行的交易执行工具。当前以 `paper` 模式和本地控制台为主，不支持实盘下单。

## Local Web Dashboard

本地控制台基于 FastAPI + Jinja2 + Bootstrap，当前只支持查看状态、日志、配置，以及修改本地机器人运行状态。

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
- 支持日志查看
- 支持配置查看
- 只支持 `paper` 模式
- 不支持实盘交易
