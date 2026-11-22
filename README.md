# 明日方舟本机后端（CN 2.7.91 / Android）

把 ADB 设备上的明日方舟客户端接到本机自建后端：登录、账号、配置、同步、战斗等请求全部由本机 Python 服务响应，不再依赖云手机、官方游戏服务器或第三方服务端运行时。

后端为**纯自研实现**（FastAPI + SQLite + Jinja2），自带 Web 管理后台；`reference/LocalArknights` 仅作为协议与数据结构的参考，不参与运行。

## 架构

```
Android 客户端（com.hypergryph.arknights 2.7.91）
  │
  ├─ 游戏 API http://127.0.0.1:8443 ──adb reverse──▶ Python 后端 8443
  │     └─ /account/login、/account/syncData、/quest/*、/user/* …
  │
  └─ Hypergryph SDK https://as.hypergryph.com:443
        └─ 设备 /system/etc/hosts 指向 127.0.0.1
           → 设备 443 nc → 127.0.0.1:8444
           → adb reverse 8444 → 主机 127.0.0.1:4443 mitmdump reverse
           → Python 后端 8443
```

- **Python 后端**：`backend/`，FastAPI + Uvicorn + SQLite，生产监听 `0.0.0.0:8443`。
- **TLS 反向层**：mitmdump `--mode reverse`，监听 `127.0.0.1:4443`，把设备 8444 的 HTTPS 请求转给 Python 8443。
- **设备侧**：hosts 把 Hypergryph SDK 域名指向本机，443 由 `nc` 转发到 8444；系统 HTTP 代理保持关闭。
- **资源**：`network_config.hu` 可在官方 CDN 与本机 `/assetbundle/official` 间切换；本地根目录命中时直接下发，缺失时 302 到独立回源地址，避免自跳转。
- **Web 管理后台**：同端口 `/admin`，账号 `sdars`，密码 `123456`（可用环境变量覆盖）。

## 已实现能力

- SDK 启动链路：`/app/v1/config`、`/user/info/v1/basic`、`/user/oauth2/v2/grant`、`/u8/user/v1/getToken`、`/u8/pay/getAllProductList`
- 游戏登录：`/account/login`、`/account/syncData`、`/account/syncStatus`、`/account/syncPushMessage`
- 战斗：`/quest/battleStart`、`/quest/battleFinish`
- 签到：`/user/checkIn` 与累计签到 `/openServerV3/getCheckInReward`；累计签到按 `open_server_table.json` 发奖，写 `checkIn.history` 的 0/1 领取状态，重复请求不重复发放，并兼容 `/activity/getOpenServerCheckInReward`
- 商店：13 个 `/shop/get*GoodList`、`/shop/getGoodPurchaseState`，覆盖现金、组合包、时装、家具、社交与经典商店
- 支付：`/pay/createOrder`、`/pay/confirmOrder`、`/pay/order/v1/state`、`/pay/order/v2/check/{payment}`、`/u8/pay/confirmOrderState`、`/user/pay/order/v2/create/app_product`、`/pay/createOrderAlipay`、`/pay/confirmOrderAlipay`、`/pay/createOrderWechat`；支付宝 page pay 由 mitm 重定向到本地 `/pay/success`，不依赖真实商户号；源石商品按 `CashGoodList.json` 的 `doubleCount` / `diamondNum + plusNum` 发放，旧 `CS_*_r3~r6` 商品 ID 映射到当前档位；月卡激活 30 天订阅并发放当日每日奖励；所有订单用 `granted_items_json` 防止重复
- 配置与资源：动态 `network_config` / `remote_config`、客户端与资源版本、客户端强制更新、维护模式、APK 更新地址、`/assetbundle/official/...` 本地文件与回源、Web ZIP/单文件上传
- 邮件：模板中心、单账号/多 UID/全服发送、有效期与附件、邮件箱、单封/批量领取、防重复发奖、已读删除；`pushFlags.hasGifts` 与邮箱同步
- Web UI：仪表盘、账号列表搜索、用户控制台（资料、货币、体力、主线进度、库存、皮肤、头像、干员、编队、订单、邮件、Token、JSON 导出）、邮件中心、请求日志、服务与更新设置

## 开发模式

自 2026-11-22 起默认切换到「静态逆向 + 无头协议测试」：

- APK / 设备内存提取 `libil2cpp.so` 与 `global-metadata.dat`，用 Il2CppDumper 导出 `dump.cs`、`stringliteral.json`、`script.json`；
- pytest 直接模拟客户端请求，验证鉴权 Header、响应结构、SQLite 状态变化；
- 截图不再作为探索或验收手段；真机只在阶段末做一次日志级联调（`request_logs` + `adb logcat`）。

Web UI 新增「研发状态」页：`http://127.0.0.1:8443/admin/development`，同步显示门禁、逆向产物、协议测试矩阵与日志；新增「协议索引」页 `http://127.0.0.1:8443/admin/protocol`，支持路由搜索、Request/Response 字段偏移与方法 RVA、JSON API 和从内存 metadata 重建 catalog。全量 `dump.cs` 可在 `http://127.0.0.1:8443/admin/protocol/dump` 搜索或下载。详细流程见 `docs/静态逆向与无头测试.md`。

当前逆向产物：`tmp/逆向分析/2026-11-22/global-metadata.decrypted.dat`（运行内存 dump，v29）、`tmp/逆向分析/2026-11-22/dump-static8/dump.cs`（50,548 类型 / 307,867 个方法 RVA）、`stringliteral.json`（37,294 条）、`script.json`（413,106 个方法）、`il2cpp.h`（447,552 个结构体）和 `backend/data/protocol_catalog.json`（507 路由 / 7,197 协议类型 / 1,500 个已回填字段偏移与 RVA 的 Request/Response 类型）。

## 已验证

- `GET /healthz` → 200
- `GET /general/v1/server_time` → 200
- `GET /user/info/v1/basic?token=lab_manual_token` → 200
- `POST /user/oauth2/v2/grant` → 200
- `POST /u8/user/v1/getToken` → 200，`result=0`
- `POST /account/login` → 200，`uid=10000000`
- `POST /account/syncData` → 200，返回完整 `user`（约 605 KB）
- `POST /quest/battleStart` → 200，返回 `battleId`
- Web UI 登录 `sdars / 123456` → 303 → `/admin` → 200；用户控制台 `/admin/accounts/10000000`、邮件中心 `/admin/mail`、服务与更新 `/admin/settings` 均 200
- 无头协议测试：`56 passed`，覆盖用户控制台写回、源石首购/复购与旧 ID 映射、月卡订阅与每日奖励、未配置奖励拦截、累计签到发奖与幂等、邮件领取幂等、编队协议、强制更新、维护模式、资源本地命中/HEAD 与 302 回源，以及 ADB 应用 UID 的 launcher TLS 本地路由和反向层 HGSDK 信封 / 过期 token 自愈
- 客户端侧：登录链路已走到 `syncData`，并曾进入游戏主界面 / 签到页 / 干员页

> 2026-11-22 修复：`/account/syncData` 现在同时刷新 `status.lastOnlineTs` 和 `status.lastRefreshTs`。此前只刷新前者，客户端会用旧的刷新时间做身份同步校验并弹出“同步身份信息失败 / 错误号 200”。修复后真实客户端登录直接进入签到页，未再弹窗。

> 2026-11-23 二次修复：错误号 200 也是 HGSDK 的 `RESPONSE_ERROR`。mitm reverse 模式下按路径拦截 `basic` / `grant`，统一返回 `data` 字符串信封；缓存 token 过期时自动回退到 `.env` 的本地账号，真机冷启动登录链全部 200。

> 2026-11-23 三次修复：`/openServerV3/getCheckInReward` 原先落入空 delta 兜底，客户端收到 200 但 UI 不刷新。现在按 `index` 读取累计签到表、发放 `items` / `reward`、把 `history[index]` 置 0、同步 `status` 与 `openServerV3` 增量并落库；重复领取保持幂等。

## 快速开始

### 1. 依赖

- Python 3.11+（统一虚拟环境：`/root/Knight/.venv`）
- mitmproxy（同一虚拟环境）
- adb，设备已 root（写入登录缓存、安装 CA）

```bash
/root/Knight/.venv/bin/pip install -r backend/requirements.txt
/root/Knight/.venv/bin/pip install -r backend/requirements-dev.txt
/root/Knight/.venv/bin/pip install mitmproxy
```

### 2. 启动后端

```bash
cd backend
ARK_HOST=0.0.0.0 ARK_PORT=8443 ./start.sh
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

- `adb reverse tcp:8443 tcp:8443`（Python 后端）
- `adb reverse tcp:8444 tcp:4443`（SDK HTTPS 反向）
- 设备 hosts：Hypergryph SDK 域名 → `127.0.0.1`
- 设备 443 → `nc` → 8444
- 设备系统代理 `:0`（关闭）

> `adb reverse` 在设备重启或 adb 重连后会丢失；如果客户端报“资源载入异常 1103”或“获取网络配置失败”，运行 `scripts/watchdog.sh` 或 systemd 的 `ark-watchdog` 会自动恢复。

### 5. 启动 TLS 反向层

```bash
cd bridge
nohup setsid /root/Knight/.venv/bin/mitmdump \
  --mode reverse:http://127.0.0.1:8443 \
  --listen-host 127.0.0.1 --listen-port 4443 \
  -w flows/reverse-$(date +%Y%m%d-%H%M%S).mitm \
  > reverse.log 2>&1 < /dev/null &
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

推荐直接安装 systemd 守护（backend + TLS reverse + watchdog）：

```bash
./scripts/install_services.sh
systemctl status ark-backend ark-mitm ark-watchdog
```

`ark-watchdog` 每 15 秒检查 ADB、`adb reverse`、设备 hosts、443→8444 转发、系统代理、后端和 TLS 反向层；任一环节掉线会自动恢复，专门消除 1103。

## 目录

| 路径 | 用途 |
|------|------|
| `backend/app/` | FastAPI 应用、路由、SQLite、Web UI 模板 |
| `backend/data/` | SQLite 数据库与玩家模板（数据库不提交） |
| `backend/fixtures/` | 协议响应模板（商品列表、配置、战斗） |
| `bridge/` | mitmproxy 桥接脚本 |
| `deploy/` | systemd unit（backend / mitm / watchdog） |
| `scripts/` | 账号注入、设备连接、watchdog、健康检查、停止清理 |
| `docs/` | 架构、协议、部署、验证记录 |
| `reference/` | 仅作协议参考的上游资料（不提交、不运行） |
| `server/` | 旧 Java 运行时（legacy，不参与当前部署） |

## 来源与许可

本仓库的 Python 后端、Web UI、桥接与脚本为自研实现。`reference/LocalArknights`（AGPL-3.0）仅用于协议与数据结构参考；旧 `server/hypergryph.jar` 不再作为运行时使用。详见 `NOTICE.md`。

本项目仅用于个人学习、协议研究与自有设备测试。
