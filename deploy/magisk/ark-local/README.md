# Arknights Local Backend Bridge

这是一个 Magisk / KernelSU / APatch 兼容模块，用于把**官方客户端**接到本机后端而不改 APK。

它做四件事：

1. 用 systemless `system/etc/hosts` 把 Hypergryph SDK / 配置 / 启动器域名指到 `127.0.0.1`；
2. 把 mitmproxy CA 放进系统信任库（构建时从主机 `~/.mitmproxy/mitmproxy-ca-cert.pem` 注入）；
3. 开机启动设备侧 `443 -> 127.0.0.1:8444` relay；
4. 对游戏 UID 维护 `ARK_LOCAL` nat 链，把已知 Hypergryph 真实 IP 的 443 引到本地 relay。

它不改 APK、不注入进程，也不依赖游戏内的任何开关。

## 前提

- 设备已 root，并能加载 Magisk 模块。APatch 可直接使用本仓库的目录安装方式；
  `apd module install` 在部分 APatch 版本会卡在 `/proc/mounts` 扫描，安装脚本默认走兼容的目录安装。
- 主机侧后端监听 `8443`，mitm 反向层监听 `127.0.0.1:4443`。
- 主机侧 `adb reverse tcp:8443 tcp:8443` 与 `adb reverse tcp:8444 tcp:4443` 仍需由 `scripts/watchdog.sh`（或 `scripts/connect_device.sh`）维持。模块负责设备侧持久化，无法从设备反向创建 adb reverse。

## 构建 / 安装

```bash
./scripts/build_ark_module.sh
ADB_SERIAL=... ./scripts/install_ark_module.sh --activate
```

`--activate` 会立即执行模块的 `post-fs-data.sh` 和 `service.sh`，不要求重启；正式开机时模块脚本也会自动执行。

## 状态

```bash
./scripts/ark_module_status.sh
```

设备内日志：`/data/adb/ark-local/service.log`。
