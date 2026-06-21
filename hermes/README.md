# Miloco ↔ Hermes Agent 集成

将米家智能家居（Xiaomi Miloco）接入 Hermes AI Agent，实现自然语言设备控制、家庭感知、主动通知等能力。

## 架构

```
miloco-backend (:1810)  ←→  miloco-bridge (:1811)  ←→  Hermes Agent → 微信/QQ
       ↓ 设备通信                                ↓ 消息注入
  米家摄像头+设备                           Hermes Cron（定时任务）
```

## 目录结构

```
hermes/
├── README.md                  # 本文件
├── config.template.json       # 配置模板（复制为 ~/.hermes/miloco/config.json 后填写）
├── personality.md             # Miloco 管家人格注入（可选，导入 Hermes personality）
├── scripts/
│   ├── miloco.sh              # 一键管理：start / stop / restart / status
│   ├── miloco-bridge.py       # HTTP 桥接 :1811，接收 webhook → 注入 Hermes
│   ├── miloco-service.py      # 后端启停管理器（备用，通常用 miloco.sh）
│   ├── miloco-catalog.py      # 设备目录快速缓存（供 agent prompt 注入）
│   └── miloco-habit-tool.py   # 习惯建议状态机（pending → asked → created/rejected）
└── skills/ → ../plugins/skills/   # 技能文件（16 个 SKILL.md + 引用文件）
```

## 快速开始

### 1. 安装 miloco 后端

```bash
curl -LsSf https://github.com/XiaoMi/xiaomi-miloco/releases/latest/download/install.sh | bash
```

配置米家账号、添加摄像头等。

### 2. 配置 Hermes 集成

```bash
# 创建 miloco 数据目录
mkdir -p ~/.hermes/miloco/log ~/.hermes/miloco/home-profile

# 复制配置模板并填写
cp hermes/config.template.json ~/.hermes/miloco/config.json
# 编辑 config.json，填入：
#   - server.token: miloco 后端 token
#   - model.omni.api_key: 多模态模型 API key
#   - notify.weixin_user_id: 微信用户 ID

# (可选) 导入 Miloco 管家人格
# 将 hermes/personality.md 内容复制到 Hermes 的 personalities 配置中
```

### 3. 安装技能

将 `plugins/skills/miloco-*/` 下的 16 个技能目录复制到 Hermes 技能目录：

```bash
cp -r plugins/skills/miloco-* ~/.hermes/skills/smart-home/miloco/
```

### 4. 启动服务

```bash
# 一键启动（backend + bridge）
hermes/scripts/miloco.sh start

# 查看状态
hermes/scripts/miloco.sh status
```

### 5. 配置 Hermes Cron 定时任务

在 Hermes 中创建以下定时任务（可通过 `hermes cron create` 或直接对话触发）：

| 任务 | 周期 | 技能 |
|------|------|------|
| 感知摘要 | */15 | miloco-perception-digest |
| 家庭巡检 | */30 | miloco-home-patrol |
| 每日做梦 | 0:00 | miloco-home-observe → promote → prune |
| 习惯建议 | 10:00 | miloco-habit-suggest |

## 技能列表

| 技能 | 功能 |
|------|------|
| miloco-create-task | 创建/管理家庭任务（rule/schedule/record） |
| miloco-devices | 查询与控制米家智能家居设备 |
| miloco-habit-suggest | 每日习惯洞察与推荐 |
| miloco-home-observe | 自动观察家庭状态 |
| miloco-home-patrol | 家庭巡检（异常检测） |
| miloco-home-profile | 家庭档案管理 |
| miloco-home-promote | 自动化规则升级 |
| miloco-home-prune | 过期数据清理 |
| miloco-miot-admin | 系统运维管理 |
| miloco-miot-identity | 家庭成员 CRUD |
| miloco-miot-identity-register | 家庭成员注册（录脸/录身形） |
| miloco-miot-scope | 感知范围控制 |
| miloco-notify | 主动通知（感知告警/定时播报） |
| miloco-perception | 主动感知（看画面/听声音） |
| miloco-perception-digest | 感知事件摘要 |
| miloco-terminate-task | 任务终止与级联清理 |

## 端口

| 端口 | 服务 | 说明 |
|------|------|------|
| 1810 | miloco-backend | REST API + Web UI |
| 1811 | miloco-bridge | miloco ↔ Hermes 消息桥接 |

## 运维命令

```bash
# 服务管理
hermes/scripts/miloco.sh start|stop|restart|status

# 单独管理后端
python3 hermes/scripts/miloco-service.py start|stop|restart|status

# 查看日志
tail -f ~/.hermes/miloco/log/miloco-backend.log
tail -f ~/.hermes/miloco/log/miloco-bridge.log

# 设备目录
python3 hermes/scripts/miloco-catalog.py
```

## 许可

本项目遵循小米米家开源许可协议，详见仓库根目录 LICENSE.md。仅限非商业用途。
