# 好游快爆游戏日历（本地Web + 每日自动抓取）

功能：
- 每天抓取 `https://www.3839.com/timeline.html` 的时间线条目
- 生成结构化“游戏日历”数据（SQLite 存储）
- 本地Web页面浏览/筛选（日期范围、地区、关键字）
- 一键导出 CSV
- 支持钉钉机器人每日群播报（近30天游戏，海外重点置顶）

## 1) 安装

建议使用 Python 3.10+（推荐 3.12）。

在项目目录下执行：

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## 2) 抓取一次（写入 SQLite）

```bash
python -m app.crawler run
```

默认会写入 `data/games.db`，并打印本次新增/更新数量。

## 3) 启动本地Web UI

```bash
python -m app.web run
```

浏览器打开 `http://127.0.0.1:8000`。

## 4) Windows 任务计划程序（每日自动执行）

目标：每天固定时间运行一次抓取。

### 4.1 创建任务
1. 打开“任务计划程序” → “创建基本任务…”（或“创建任务…”）
2. 触发器：每天（例如 08:00）
3. 操作：启动程序

### 4.2 程序与参数（推荐）
- **程序/脚本**：填写虚拟环境的 Python 路径，例如：
  - `C:\Users\11723\hykb_game_calendar\.venv\Scripts\python.exe`
- **添加参数**：
  - `-m app.crawler run`
- **起始于（可选但强烈推荐）**：
  - `C:\Users\11723\hykb_game_calendar`

### 4.3 日志输出（可选）
可在“添加参数”里追加重定向（需要用 cmd 包一层），更稳定的做法是：
- 程序/脚本：`cmd.exe`
- 参数：
  - `/c "C:\Users\11723\hykb_game_calendar\.venv\Scripts\python.exe -m app.crawler run >> C:\Users\11723\hykb_game_calendar\data\crawler.log 2>&1"`
- 起始于：`C:\Users\11723\hykb_game_calendar`

## 5) 钉钉机器人日报

### 5.1 本地预览内容（不发送）

```bash
python -m app.report_dingtalk run --dry-run
```

默认是老板简版（`--style brief`）。如需详细版：

```bash
python -m app.report_dingtalk run --dry-run --style full
```

### 5.2 发送到钉钉群

```bash
python -m app.report_dingtalk run --webhook "你的webhook"
```

如果机器人开启了“加签”，追加 `--secret`：

```bash
python -m app.report_dingtalk run --webhook "你的webhook" --secret "你的secret"
```

可选样式参数：
- `--style brief`：老板简版（默认，海外TOP10置顶）
- `--style full`：详细版（更多明细）

### 5.3 任务计划程序每日自动汇报（建议）

- 程序/脚本：
  - `C:\Users\11723\hykb_game_calendar\.venv\Scripts\python.exe`
- 添加参数：
  - `-m app.report_dingtalk run --webhook "你的webhook" --secret "你的secret"`
- 起始于：
  - `C:\Users\11723\hykb_game_calendar`

## 数据字段说明
- **游戏名称**：`game_name`
- **上线日期**：`event_date`（YYYY-MM-DD）
- **上线时间**：`event_time`（HH:MM，可为空）
- **地区**：`region`（domestic/overseas/unknown）
- **事件类型**：`event_type`（更新/上线/测试）
- **来源**：`source_url`（时间线条目链接或时间线页）

