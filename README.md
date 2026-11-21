# 明日方舟本机后端（CN 2.7.91 / Android）

把 ADB 设备上的明日方舟客户端接到本机自建后端：登录、账号、配置、同步、战斗等请求全部由本机 Python 服务响应，不再依赖云手机、官方游戏服务器或第三方服务端运行时。

后端为**纯自研实现**（FastAPI + SQLite + Jinja2），自带 Web 管理后台；`reference/LocalArknights` 仅作为协议与数据结构的参考，不参与运行。

## 架构

```
Android 客户端（com.hypergryph.arknights 2.7.91）
  │
  ├─ 系统代理 127.0.0.1:8080 ──adb reverse──▶ mitmdump bridge
  │     └─ as.hypergryph.com 的 basic / grant / update_agreement
  │        转发到本机 127.0.0.1:8443
  │
  └─ 游戏 API http://127.0.0.1:8443 ──adb reverse──▶ Python 后端
        /account/login、/account/syncData、/quest/*、/user/* …
```

- **Python 后端**：`backend/`，FastAPI + Uvicorn + SQLite，默认监听 `127.0.0.1:8443`。
- **桥接层**：`bridge/local_redirect.py`，只接管 SDK 启动阶段的 Hypergryph 域名请求，其余流量按原样放行。
- **Web 管理后台**：同端口 `/admin`，账号 `sdars`，密码 `123456`（可用环境变量覆盖）。

## 已实现能力

- SDK 启动链路：`/app/v1/config`、`/user/info/v1/basic`、`/user/oauth2/v2/grant`、`/u8/user/v1/getToken`、`/u8/pay/getAllProductList`
- 游戏登录：`/account/login`、`/account/syncData`、`/account/syncStatus`、`/account/syncPushMessage`
- 战斗：`/quest/battleStart`、`/quest/battleFinish`
- 支付占位：`/pay/getUnconfirmedOrderIdList`、`/u8/pay/getAllProductList`
- 配置与资源：`network_config`、`remote_config`、版本接口、`/assetbundle/official/...`（302 回源到官方 CDN）
- Web UI：仪表盘、账号管理（新建 / 编辑 / 封禁 / 重置 Token / 删除）、请求日志、服务设置

## 已验证

- `GET /healthz` → 200
- `GET /general/v1/server_time` → 200
- `GET /user/info/v1/basic?token=lab_manual_token` → 200
- `POST /user/oauth2/v2/grant` → 200
- `POST /u8/user/v1/getToken` → 200，`result=0`
- `POST /account/login` → 200，`uid=10000000`
- `POST /account/syncData` → 200，返回完整 `user`（约 605 KB）
- `POST /quest/battleStart` → 200，返回 `battleId`
- Web UI 登录 `sdars / 123456` → 303 → `/admin` → 200
- 客户端侧：登录链路已走到 `syncData`，并曾进入游戏主界面 / 签到页 / 干员页

> 客户端 U8/HG SDK 在伪造 Token 下可能弹出“同步身份信息失败（错误号 200）”。这是客户端的 SDK 身份校验提示，不影响本机后端的登录与 `syncData`；重试 / 重新唤醒后可继续进入游戏。详细现象与排查见 `docs/验证记录.md`。

## 快速开始

### 1. 依赖

- Python 3.11+（统一虚拟环境：`/root/Knight/.venv`）
- mitmproxy（同一虚拟环境）
- adb，设备已 root（写入登录缓存、安装 CA）

```bash
/root/Knight/.venv/bin/pip install -r backend/requirements.txt
/root/Knight/.venv/bin/pip install mitmproxy
```

### 2. 启动后端

```bash
cd backend
./start.sh          # 等价于 ARK_PORT=8443 /root/Knight/.venv/bin/python run.py
```

首次启动会自动创建 `backend/data/arknights.sqlite3`。

### 3. 创建账号并写入设备缓存

```bash
ADB_SERIAL=<设备序列号> ./scripts/seed_account.sh
```

默认创建手机号 `13800138000`、Token `lab_manual_token` 的账号，并写入设备 `HypergryphSdkPreferences.xml`。

### 4. 连接设备

```bash
ADB_SERIAL=<设备序列号> ./scripts/connect_device.sh
```

脚本会建立：

- `adb reverse tcp:8080 tcp:8080`（mitmproxy）
- `adb reverse tcp:8443 tcp:8443`（Python 后端）
- 设备全局代理 `127.0.0.1:8080`

> `adb reverse` 在设备重启或 adb 重连后会丢失；如果客户端报“资源载入异常 1103”或“获取网络配置失败”，先执行 `adb reverse --list` 检查并重新运行连接脚本。

### 5. 启动 mitm 桥接

```bash
cd bridge
nohup setsid /root/Knight/.venv/bin/mitmdump \
  --listen-host 127.0.0.1 --listen-port 8080 \
  -s local_redirect.py -w flows/bridge-$(date +%Y%m%d-%H%M%S).mitm \
  > bridge.log 2>&1 < /dev/null &
```

### 6. Web 管理后台

浏览器打开 `http://127.0.0.1:8443/admin/login`：

- 用户名：`sdars`
- 密码：`123456`

### 7. 健康检查与停止

```bash
ADB_SERIAL=<设备序列号> ./scripts/healthcheck.sh
ADB_SERIAL=<设备序列号> ./scripts/stop_all.sh
```

## 目录

| 路径 | 用途 |
|------|------|
| `backend/app/` | FastAPI 应用、路由、SQLite、Web UI 模板 |
| `backend/data/` | SQLite 数据库与玩家模板（数据库不提交） |
| `backend/fixtures/` | 协议响应模板（商品列表、配置、战斗） |
| `bridge/` | mitmproxy 桥接脚本 |
| `scripts/` | 账号注入、设备连接、健康检查、停止清理 |
| `docs/` | 架构、协议、部署、验证记录 |
| `reference/` | 仅作协议参考的上游资料（不提交、不运行） |
| `server/` | 旧 Java 运行时（legacy，不参与当前部署） |

## 来源与许可

本仓库的 Python 后端、Web UI、桥接与脚本为自研实现。`reference/LocalArknights`（AGPL-3.0）仅用于协议与数据结构参考；旧 `server/hypergryph.jar` 不再作为运行时使用。详见 `NOTICE.md`。

本项目仅用于个人学习、协议研究与自有设备测试。
