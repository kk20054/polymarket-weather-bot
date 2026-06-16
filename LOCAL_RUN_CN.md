# WeatherBot 本地运行说明

## 当前部署位置

项目目录：

```powershell
C:\Users\Administrator\Documents\polymarket\weatherbot
```

已创建本地 Python 虚拟环境：

```powershell
C:\Users\Administrator\Documents\polymarket\weatherbot\.venv
```

## 运行前准备

进入项目目录：

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
```

激活虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

如果 PowerShell 阻止激活脚本，可改用不激活环境的写法：

```powershell
.\.venv\Scripts\python.exe weatherbet.py status
```

## 常用命令

查看状态：

```powershell
python weatherbet.py status
```

查看报告：

```powershell
python weatherbet.py report
```

启动 bot：

```powershell
python weatherbet.py
```

停止 bot：

```text
按 Ctrl+C
```

## 配置文件

配置在：

```powershell
config.json
```

重点参数：

```json
{
  "balance": 10000.0,
  "max_bet": 20.0,
  "min_ev": 0.1,
  "max_price": 0.45,
  "min_volume": 500,
  "kelly_fraction": 0.25,
  "scan_interval": 3600,
  "vc_key": "YOUR_KEY_HERE",
  "max_slippage": 0.03
}
```

`vc_key` 是 Visual Crossing 的 API key，用于市场结束后查询历史最高温并做结算/校准。没有这个 key 也可以扫描和模拟开仓，但后续自动结算能力会受限。

## 重要说明

当前仓库没有真实链上下单代码，也没有钱包私钥、Polymarket CLOB 客户端或订单签名逻辑。它会读取公开天气和 Polymarket 市场数据，然后在本地 `data/` 目录里记录模拟持仓和模拟余额。

如果要变成真正的 Polymarket 自动交易系统，还需要额外接入 Polymarket CLOB 下单流程，并自行处理钱包、资金、API 凭证、风控和合规风险。建议先长期模拟运行并核对数据质量，再考虑真实资金。
