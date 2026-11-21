# Arknights 本机后端（CN 2.7.91 / Android）

把明日方舟客户端的登录、账号、配置、战斗等请求接到本机自建后端，不再依赖云手机、远程桌面或官方游戏服务器。

## 组成

```
Android 客户端
  │  系统代理 127.0.0.1:8080（adb reverse → 本机 mitmdump）
  │  HTTP 127.0.0.1:8443（adb reverse → 本机 Java 后端）
  ▼
mitmproxy 桥接层（bridge/local_redirect.py）
  ├─ as.hypergryph.com        登录 / basic / grant → 本机后端
  ├─ game-config.hypergryph.com  network_config / remote_config → 本机后端
  ├─ ak-conf.hypergryph.com     version / network_config → 本机后端
  └─ launcher / ak-webview     启动器与公告接口 → 本机后端
  ▼
Java 后端 hypergryph.jar（LocalArknights 0.5.3.0 构建）
  ├─ 账号、OAuth、syncData、主线/活动/基建/战斗等 REST 接口
  └─ MySQL 8.4（Docker 容器 arkmysql）
```

## 已验证

- 客户端：`com.hypergryph.arknights` 2.7.91（Android 10，arm64）
- `POST /account/login` → 200
- `POST /account/syncData` → 200，UID 10000000
- `POST /quest/battleStart` → 200，`stageId=guide_01`
- 客户端进入教学剧情并开始教学战斗

详见 `docs/验证记录.md`。

## 快速开始

### 1. 依赖

- Docker（MySQL 8.4）
- Java 17+
- Python 3.11+ 与 mitmproxy（当前使用 `/root/Knight/.venv`）
- adb，目标设备已 root（用于首次写入登录缓存和安装 CA）

### 2. 配置

```bash
cp .env.example .env
# 编辑 .env：数据库密码、设备序列号、账号 token
docker compose up -d
./server/start.sh
```

`server/start.sh` 会用 `.env` 生成 `server/config.json`，并启动 Java 后端（HTTP 8443 / 36650）。

### 3. 首次准备账号

```bash
./scripts/seed_account.sh
```

该脚本会：

- 在 MySQL 中创建/更新账号（默认 token `lab_manual_token`，手机号 `13800138000`）；
- 把账号缓存写入设备的 `HypergryphSdkPreferences.xml`，让客户端“开始唤醒”时直接使用该 token。

### 4. 连接设备

```bash
./scripts/connect_device.sh
```

脚本会建立：

- `adb reverse tcp:8080 tcp:8080`（mitmproxy）
- `adb reverse tcp:8443 tcp:8443`（本地后端）
- 设备全局 HTTP 代理 `127.0.0.1:8080`

首次连接还需把 mitmproxy CA 安装为系统 CA（脚本会检查并提示），否则 HTTPS 请求无法解密。

### 5. 启动客户端

启动明日方舟，标题页点击“开始唤醒”。首次会出现语音包选择，选择任意一项并确认；随后客户端会请求本机后端并进入教学。

### 6. 健康检查

```bash
./scripts/healthcheck.sh
```

检查后端、MySQL、mitm 桥接、ADB 设备与两个 reverse 端口。

## 目录

| 路径 | 用途 |
|------|------|
| `server/` | Java 后端、配置模板、启动脚本 |
| `bridge/` | mitmproxy 桥接脚本，把官方域名映射到本机后端 |
| `scripts/` | 数据库初始化、账号注入、设备连接、验收脚本 |
| `docs/` | 架构、协议端点、部署、验证记录 |
| `tmp/协议分析/` | 抓包、原始 flow、分析脚本（不提交） |
| `reference/` | 上游源码与参考资料（不提交） |

## 许可与来源

Java 后端来自开源项目 [loongling/LocalArknights](https://github.com/loongling/LocalArknights)（AGPL-3.0），本仓库对其进行本机化配置、桥接和验证。衍生分发需遵守 AGPL-3.0，详见 `NOTICE.md` 与 `server/LICENSE-LocalArknights`。

本项目仅用于个人学习、协议研究与自有设备测试。

