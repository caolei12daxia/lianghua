# MACD 企业微信告警

本目录提供 A 股日线 MACD 监控脚本。行情由 AkShare 的 `stock_zh_a_hist` 获取，默认使用前复权日线；通知通过企业微信群机器人 webhook 发送 Markdown 消息。

## 安装

```powershell
cd C:\Users\DELL\PyCharmMiscProject\lianghua
C:\Users\DELL\PyCharmMiscProject\.venv\Scripts\python.exe -m pip install -r C:\Users\DELL\PyCharmMiscProject\lianghua\requirements.txt
```

## 配置企业微信

1. 在企业微信群中添加群机器人并复制 webhook 地址。
2. 在运行脚本的终端设置 webhook 环境变量：

```powershell
$env:WECOM_WEBHOOK_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=你的key"
```

不要把真实 webhook 写入代码或提交到版本控制。

## 使用

分析多个 A 股代码。默认只在最新交易日出现 MACD 金叉或死叉时推送，重复运行不会重复通知同一信号：

```powershell
C:\Users\DELL\PyCharmMiscProject\.venv\Scripts\python.exe C:\Users\DELL\PyCharmMiscProject\lianghua\macd_alert.py 000001 600519
```

第一次配置微信时，可以无论是否交叉都发送一次报告以验证 webhook：

```powershell
C:\Users\DELL\PyCharmMiscProject\.venv\Scripts\python.exe C:\Users\DELL\PyCharmMiscProject\lianghua\macd_alert.py 000001 --notify all
```

仅预览推送内容、不请求微信：

```powershell
C:\Users\DELL\PyCharmMiscProject\.venv\Scripts\python.exe C:\Users\DELL\PyCharmMiscProject\lianghua\macd_alert.py 000001 --notify all --dry-run
```

使用本地 CSV 验证指标逻辑，CSV 需包含 `date,close` 或 `日期,收盘` 列：

```powershell
C:\Users\DELL\PyCharmMiscProject\.venv\Scripts\python.exe C:\Users\DELL\PyCharmMiscProject\lianghua\macd_alert.py DEMO --csv C:\Users\DELL\PyCharmMiscProject\lianghua\prices.csv --notify all --dry-run
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--days 180` | 拉取最近多少自然日行情 |
| `--adjust qfq` | 复权方式：`qfq`、`hfq` 或空字符串 |
| `--notify signal` | `signal` 只推交叉，`all` 推送当前状态，`off` 不推送 |
| `--state-file` | 已发送信号的去重状态文件，默认 `.macd_alert_state.json` |
| `--no-dedupe` | 允许重复推送 |

## 定时执行

可用 Windows 任务计划程序在收盘后执行：

```powershell
powershell.exe -NoProfile -Command "$env:WECOM_WEBHOOK_URL='你的webhook'; & 'C:\Users\DELL\PyCharmMiscProject\.venv\Scripts\python.exe' 'C:\Users\DELL\PyCharmMiscProject\lianghua\macd_alert.py' 000001 600519"
```

MACD 只是技术指标信号，不构成交易建议。行情接口按日频更新，应在收盘后运行日线告警。

## 30分钟股票池监控

`symbols.txt` 是日线和30分钟脚本共同使用的股票池。A股填写六位代码，港股填写 `HK:五位代码`，美股填写 `US:Ticker`；持仓品种可在后面增加成本价：

```text
688563 68.9
001391 5.20
HK:00386
US:CMCSA
```

报告会自动查询股票名称；填写成本价的品种显示最新价格相对成本的浮动盈亏，未填写成本价的品种仅显示技术分析。

通过 `--markets` 控制分析市场：`CN` 表示 A 股、`HK` 表示港股、`US` 表示美股、`ALL` 表示全部市场。白天只分析 A 股和港股时使用 `--markets CN,HK`，美股单独使用 `--markets US`，完整汇总使用 `--markets ALL`。

盘中每小时执行以下命令。脚本只在最新30分钟K线出现新的 MACD 金叉或死叉时发送微信提醒，并自动去重。提醒内容包含 BOLL(20,2)、RSI(14) 和 VOL 相对近5根均量的分析：

```powershell
C:\Users\DELL\PyCharmMiscProject\.venv\Scripts\python.exe C:\Users\DELL\PyCharmMiscProject\lianghua\intraday_macd_alert.py --mode hourly --webhook-url "你的企业微信webhook"
```

收盘后执行以下命令，发送当天中文汇总分析，包含 MACD、BOLL、RSI、VOL 与一句话技术面操作观察：

```powershell
C:\Users\DELL\PyCharmMiscProject\.venv\Scripts\python.exe C:\Users\DELL\PyCharmMiscProject\lianghua\intraday_macd_alert.py --mode close --webhook-url "你的企业微信webhook"
```

Windows 任务计划程序中，交易时段按每小时创建 `--mode hourly` 任务，并在 `15:05` 单独创建 `--mode close` 任务。

脚本中的“可关注买入机会”“关注减仓或止损”等内容仅为指标规则产生的技术面观察，不构成投资建议或自动交易指令。

## 日线技术分析

日线脚本使用相同的 `symbols.txt` 成本配置，输出股票名称、相对成本盈亏、MACD、BOLL、RSI、VOL 和一句话结论。

收盘后发送日线汇总：

```powershell
C:\Users\DELL\PyCharmMiscProject\.venv\Scripts\python.exe C:\Users\DELL\PyCharmMiscProject\lianghua\daily_macd_alert.py --mode report --webhook-url "你的企业微信webhook"
```

仅在最新日线出现新的 MACD 金叉或死叉时发送提醒：

```powershell
C:\Users\DELL\PyCharmMiscProject\.venv\Scripts\python.exe C:\Users\DELL\PyCharmMiscProject\lianghua\daily_macd_alert.py --mode signal --webhook-url "你的企业微信webhook"
```
