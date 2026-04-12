# 🤖 Teletask — Telegram Bot 自动化管理系统

一个基于 Python 的 Telegram 机器人管理平台，支持关键词自动回复、定时消息推送，配备 Web 管理后台，无需修改代码即可完成所有配置。

---

## ✨ 功能特性

### 🔑 关键词自动回复
- 支持**包含 / 精确 / 正则**三种匹配方式
- 一个关键词可绑定**多条不同类型的回复**
- 触发模式：**随机一条** 或 **全部发送**
- 支持回复类型：文本、图片、视频、音频、文件、动图、语音、贴纸
- 群组/频道中的文本回复默认 **10 秒后自动删除**（可自定义）
- 同一关键词触发存在 **5 秒冷却**，防止高频刷屏（不同群组冷却互不影响）
- 支持设置**自动到期时间**，到期后自动停用并通知管理员
- 支持**批量启用 / 停用 / 删除**（勾选 + 一键操作）

### ⏰ 定时推送任务
- 支持 **Cron 表达式**（周期执行）
- 支持**一次性定时任务**（指定具体时间，执行后自动停用）
- 支持设置**发送后自动删除**（自定义延迟时长）
- 任务执行日志记录（完成 / 执行中 / 失败）

### 🚫 自动封禁规则
- 可配置触发阈值：用户在指定时间窗口内触发关键词次数超限后**自动封禁**
- 封禁后立即通过 Bot 通知所有管理员
- 可在统计页面手动解封

### 🖥 Web 管理后台
- 浏览器操作，无需改代码
- 关键词和定时任务均支持**增删改查 + 批量操作**
- 弹窗式编辑，操作流畅
- 内置 **HTML 富文本工具栏**（加粗、斜体、下划线、超链接、代码等），实时预览
- 实时显示当前时间，内置 Cron 快填示例
- 概览统计卡片（触发次数、任务完成数等）

### 📁 文件库
- 管理员向 Bot 发送媒体文件，自动记录 file_id 及文件信息
- 记录上传者姓名、用户名、ID
- 支持**在线编辑文件名**、**软删除**文件
- 删除前自动检查引用，被删除文件若仍被关键词或任务使用，Bot 跳过发送并告警管理员
- 支持关键词搜索，点击 file_id 一键复制

### 📊 统计信息
- 关键词触发记录：用户、群组、触发时间、关键词
- 定时任务执行情况：完成 / 执行中 / 失败状态
- 封禁用户管理：一键 Ban / Unban
- 数据概览卡片（9 项指标）

### 🔐 权限与安全
- `.env` 配置多个管理员账号（Telegram user_id）
- 仅管理员可上传文件获取 file_id
- Cookie 使用 **HMAC-SHA256** 签名，防止伪造
- 所有密码比较使用**常量时间函数**，防止时序攻击
- 统计页面用户数据全面 **XSS 转义**
- 正则匹配带**超时保护**，防止 ReDoS 攻击
- 数据库启用 **WAL 模式 + 写锁**，防止多线程并发写入冲突

### 🤖 Bot 命令（管理员专属）
| 命令 | 说明 |
|------|------|
| `/start` | 查看 Bot 状态和管理员信息 |
| `/keywords` | 查看所有关键词规则列表 |
| `/task_status` | 查看定时任务状态和最近执行记录 |
| 直接发送媒体文件 | 获取 file_id 并入库 |

---

## 🗂 项目结构

```
teletask/
├── main.py           # 启动入口，同时运行 Bot 和 Web 后台
├── bot.py            # Bot 核心逻辑（消息处理、定时任务）
├── bot_helpers.py    # 辅助模块（延迟删除、冷却、触发计数）
├── app.py            # Flask Web 管理后台
├── database.py       # SQLite 数据库操作
├── requirements.txt  # Python 依赖
├── .env              # 环境变量配置（不上传 Git）
├── tgbot.db          # SQLite 数据库文件（自动生成）
└── templates/
    ├── index.html        # 主管理后台
    ├── login.html        # 登录页
    ├── files.html        # 文件库
    ├── files_login.html  # 文件库登录页
    ├── stats.html        # 统计信息
    └── stats_login.html  # 统计登录页
```

---

## 🚀 快速部署

### 环境要求
- Python 3.10+
- Ubuntu 20.04 / 22.04 / 24.04

### 第一步：克隆项目

```bash
cd /opt
git clone https://github.com/Swzh53/teletask.git
cd teletask
```

### 第二步：安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

可选安装 `regex` 库获得正则 ReDoS 防护：

```bash
pip install regex
```

### 第三步：配置环境变量

```bash
nano .env
```

```env
# Telegram Bot Token（从 @BotFather 获取）
BOT_TOKEN=123456789:ABCdefGHI...

# Web 管理后台密码（必填）
ADMIN_PASSWORD=your_strong_password

# Session 密钥（必填）
# 生成方法：python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=your_random_secret_key

# 代理
PROXY=socks5://127.0.0.1:7890

# Web 后台端口
WEB_PORT=5000

# 管理员 Telegram user_id
ADMIN_IDS=123456789,987654321
```

> ⚠️ `SECRET_KEY` 和 `ADMIN_PASSWORD` 为必填项，任一未设置服务将拒绝启动。
>
> 生成 SECRET_KEY：
> ```bash
> python -c "import secrets; print(secrets.token_hex(32))"
> ```

### 第四步：测试运行

```bash
python main.py
```

看到以下输出说明成功：
```
Web 管理后台启动 → http://0.0.0.0:5000
Bot 启动完成
```

### 第五步：配置后台服务

```bash
sudo nano /etc/systemd/system/teletask.service
```

```ini
[Unit]
Description=Teletask Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/teletask
ExecStart=/opt/teletask/.venv/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable teletask
sudo systemctl start teletask
```

### 第六步：开放端口

```bash
sudo ufw allow 22
sudo ufw allow 5000
sudo ufw enable
```

### 访问管理后台

```
http://你的服务器IP:5000
```

---

## 📖 使用指南

### 添加关键词回复

1. 登录管理后台
2. 在「关键词自动回复」区域填写关键词和匹配方式
3. 选择触发模式（随机一条 / 全部发送）
4. 可选：设置自动删除回复时长、到期时间、开始生效时间
5. 添加一条或多条回复内容（支持 HTML 富文本格式）
6. 点击「添加关键词」

### 添加媒体类型回复

1. 在 Telegram 中私聊你的 Bot，发送图片/视频/文件
2. Bot 自动回复该文件的 `file_id` 并记录到文件库
3. 在管理后台选择对应媒体类型，粘贴 `file_id`

### 批量操作

1. 在关键词或定时任务列表中，勾选需要操作的条目（表头可全选）
2. 点击批量操作栏中的「✅ 启用」「⏸ 停用」或「🗑 删除」按钮

### 添加定时任务

1. 填写任务名称和目标 Chat ID
2. 选择「周期执行」填写 Cron 表达式，或选择「一次性」选择具体时间
3. 可选：设置发送后自动删除时长
4. 选择消息类型，填写内容
5. 点击「添加定时任务」

**Cron 表达式速查：**

| 表达式 | 含义 |
|--------|------|
| `0 9 * * *` | 每天早上 9 点 |
| `0 9 * * 1-5` | 工作日早上 9 点 |
| `*/30 * * * *` | 每 30 分钟 |
| `0 8,12,20 * * *` | 每天 8点、12点、20点 |
| `0 0 * * 1` | 每周一 0 点 |
| `0 10 1 * *` | 每月 1 日 10 点 |

### 自动封禁规则

1. 在管理后台「自动封禁规则」区域填写触发次数和时间窗口
2. 点击「添加规则」
3. 用户在指定时间内触发次数超限后自动封禁，Bot 通知管理员

### 文字格式说明

回复文本支持 HTML 格式，工具栏可一键插入：

| 效果 | HTML 写法 |
|------|-----------|
| **加粗** | `<b>文字</b>` |
| _斜体_ | `<i>文字</i>` |
| 下划线 | `<u>文字</u>` |
| ~~删除线~~ | `<s>文字</s>` |
| `行内代码` | `<code>文字</code>` |
| 超链接 | `<a href="URL">文字</a>` |

### 获取 Chat ID

向 [@get_id_bot](https://t.me/get_id_bot) 发消息，或将其拉入群组发送 `/id` 获取。

---

## 🔧 运维命令

```bash
# 查看运行状态
systemctl status teletask

# 重启服务
systemctl restart teletask

# 查看实时日志
journalctl -u teletask -f

# 查看最近 100 行日志
journalctl -u teletask -n 100

# 停止服务
systemctl stop teletask
```

### 更新代码

```bash
cd /opt/teletask
git pull
systemctl restart teletask
```

> 数据库迁移自动执行，无需手动操作。旧数据库升级后新字段自动补全，数据不丢失。

---

## 🛠 技术栈

| 组件 | 技术 |
|------|------|
| Bot 框架 | [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) v21+ |
| Web 后台 | [Flask](https://flask.palletsprojects.com/) |
| 定时任务 | [APScheduler](https://apscheduler.readthedocs.io/) |
| 数据库 | SQLite（内置，无需安装） |
| 环境配置 | python-dotenv |

---

## ⚠️ 注意事项

- `tgbot.db` 是数据库文件，建议定期备份
- `SECRET_KEY` 和 `ADMIN_PASSWORD` 为必填项，未设置时服务拒绝启动
- 管理后台默认监听所有 IP，建议配置防火墙或 Nginx 反代 + HTTPS

---

## 📄 License

MIT License
