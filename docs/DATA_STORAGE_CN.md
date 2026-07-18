# WeatherBot 数据存储位置

## 当前布局

- 项目代码：`C:\Users\Administrator\Documents\polymarket\weatherbot`
- 代码使用的逻辑目录：`C:\Users\Administrator\Documents\polymarket\weatherbot\data`
- 数据实际物理目录：`D:\WeatherBot\data`
- 运行日志：`D:\WeatherBot\runtime-logs`

项目内的 `data` 是 Windows 目录联接（Junction），目标为 `D:\WeatherBot\data`。代码、CLI 和测试继续使用原来的相对路径 `data/...`，但数据库、原始响应、历史回填和本地快照实际写入 D 盘。不要把生产代码改成散落的 D 盘绝对路径。

## 常用检查

```powershell
Get-Item C:\Users\Administrator\Documents\polymarket\weatherbot\data |
  Select-Object FullName, LinkType, Target

Get-ChildItem D:\WeatherBot\data -Force
Get-PSDrive C, D | Select-Object Name, Used, Free
```

预期结果：`LinkType=Junction`，`Target=D:\WeatherBot\data`。

## 重要边界

- 不要删除 `D:\WeatherBot\data`；这里是唯一生产数据副本。
- 不要在 Junction 和物理目录之间做双向复制或同时运行两个数据库副本。
- Git 仍忽略 `data/`、数据库、密钥、原始抓取和构建产物。
- `V3_DB_PATH` 未显式设置时仍使用 `data/weatherbot_v3.db`，会自动落到 D 盘。
- 迁移、备份或恢复前先停止 uvicorn、scheduler 和所有 WeatherBot Python 进程。

## 重新建立联接

仅当项目内 `data` 联接丢失、且已确认 `D:\WeatherBot\data` 完整存在时执行：

```powershell
New-Item -ItemType Junction `
  -Path C:\Users\Administrator\Documents\polymarket\weatherbot\data `
  -Target D:\WeatherBot\data
```

迁移日期：2026-07-18。迁移时逐文件校验总字节数，主库 SHA256 一致，迁移前后 `PRAGMA quick_check` 均为 `ok`。
