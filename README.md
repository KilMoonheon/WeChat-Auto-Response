# 微信自动回复

基于 [wechatauto-replica](https://github.com/fanyuantaier/wechatauto-replica) 的 Windows 个人微信自动回复小工具。通过 YAML 配置**按联系人昵称**决定监听范围与回复内容，适用于微信 PC 客户端 **4.1.12+**。

> 仅供个人学习与交流，请勿用于营销、群发等用途。使用自动化可能违反《微信软件许可及服务协议》，风险自负。项目基于cursor开发

## 功能

- 按**联系人昵称 / 备注**配置是否自动回复
- 两种回复模式：`fixed`（固定话术）、`keyword`（关键词匹配）
- **冷却时间**：同一聊天在上一次自动回复后 N 秒内不再回复（默认 180 秒）
- **后台监听**：轮询本地会话数据库，监听阶段不主动弹窗
- 仅处理程序**启动之后**的新消息

## 环境要求

| 项目 | 要求 |
|------|------|
| 系统 | Windows 10 / 11 |
| Python | 3.9 – 3.13（推荐 3.13，不支持 3.14） |
| 微信 | PC 版 4.1.12+，已登录并保持运行 |

## 快速开始

### 1. 克隆仓库

```bash
git clone <你的仓库地址>
cd 微信自动回复
```

### 2. 安装依赖

```bash
py -3.13 -m pip install -r requirements.txt
```

### 3. 编辑配置

修改同目录下的 `config.yaml`，填入要监听的联系人昵称及回复规则（详见下文）。

### 4. 运行

```bash
py -3.13 main.py
```

Windows 也可双击 `run.bat`（会自动安装依赖并启动）。

按 `Ctrl+C` 退出。

## 配置说明

配置文件：`config.yaml`

### 全局设置 `settings`

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `poll_interval` | `1.0` | 后台检查新消息间隔（秒） |
| `reply_cooldown` | `180` | 同一聊天两次自动回复的最小间隔（秒），`0` 表示关闭 |
| `minimize_after_reply` | `true` | 发送成功后是否最小化微信窗口到任务栏 |
| `sync_mobile` | `false` | 检测到未读时是否打开会话以同步手机消息 |
| `sync_wait` | `0.8` | 打开会话后等待同步的秒数 |

### 联系人 `contacts`

键名即微信聊天列表中显示的**备注名或昵称**（须与界面完全一致），**不要填你自己的登录昵称**。

| 字段 | 必填 | 说明 |
|------|------|------|
| `enabled` | 否 | `true` / `false`，是否启用，默认 `true` |
| `reply_mode` | 是 | `fixed` 或 `keyword` |
| `default` | 视模式 | 固定回复内容，或关键词未命中时的默认回复 |
| `keywords` | 否 | `keyword` 模式下：关键词 → 回复内容 |
| `allow_self` | 否 | 是否回复自己发的消息；**文件传输助手**测试需设为 `true` |
| `cooldown_seconds` | 否 | 覆盖全局 `reply_cooldown` |
| `only_text` | 否 | 默认 `true`，仅对文字类摘要回复 |

### 配置示例

```yaml
settings:
  poll_interval: 1.0
  reply_cooldown: 180
  sync_mobile: false

contacts:
  文件传输助手:
    enabled: true
    allow_self: true
    reply_mode: keyword
    default: "收到，稍后回复您。"
    keywords:
      你好: "你好！有什么可以帮您？"
      在吗: "在的，请说。"

  张三:
    enabled: true
    reply_mode: fixed
    default: "您好，我现在不在电脑旁，看到消息后会尽快回复。"
```

## 项目结构

```
微信自动回复/
├── main.py           # 主程序
├── config.yaml       # 配置文件（建议勿提交含真实昵称的版本到公开仓库）
├── requirements.txt  # Python 依赖
├── run.bat           # Windows 一键启动
└── README.md
```

## 工作原理（简述）

1. **监听**：读取 PC 微信本地数据库中的会话摘要，后台轮询是否有新消息。
2. **回复**：匹配规则后，通过 UI 自动化打开对应聊天并发送消息。

因此：

- **监听**可以在后台进行（微信在任务栏即可）。
- **发送回复**时，微信窗口会**短暂置顶**约数秒（个人微信无官方静默发消息接口，4.x 界面为自绘，只能通过模拟操作发送）。

## 常见问题

**Q：启动报错「找不到联系人」？**  
A：检查 `config.yaml` 里的昵称是否与微信聊天列表显示完全一致（备注优先）。

**Q：为什么填自己的昵称不行？**  
A：那是当前登录账号，不是聊天对象。应填写**给你发消息的好友**的昵称。

**Q：程序启动前的消息会回复吗？**  
A：不会，只处理启动后的新消息。

**Q：手机发到文件传输助手没反应？**  
A：需 PC 微信已登录联网；手机消息需先同步到 PC。可尝试 `sync_mobile: true`，或先在 PC 微信中打开该会话确认消息已出现。

**Q：能否完全后台、不弹出微信窗口？**  
A：监听可以；**发送回复**目前无法做到完全不触达窗口。若需完全后台方案，请考虑企业微信或公众号等官方 API。

## 致谢

- [wechatauto-replica](https://github.com/fanyuantaier/wechatauto-replica) — 微信 4.x 自动化底层库
- 上游 [wxauto](https://github.com/cluic/wxauto) 项目

## 免责声明

本项目与腾讯、微信官方无关。自动化操作可能导致账号限制，请谨慎使用，作者不承担任何责任。
