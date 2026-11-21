# 来源与许可

## 自研运行时

`backend/`（FastAPI + SQLite + Jinja2 Web UI）、`bridge/local_redirect.py` 与 `scripts/` 为本仓库自研实现，运行时不依赖 LocalArknights、`hypergryph.jar`、MySQL 或 Docker。

## LocalArknights（仅参考）

- 上游：https://github.com/loongling/LocalArknights
- 许可：GNU Affero General Public License v3.0（AGPL-3.0）
- 本仓库仅在 `reference/LocalArknights` 中保留其源码，用于对照协议端点、响应结构与游戏数据字段；该目录不参与构建、不启动、不对外提供服务。
- 旧 `server/hypergryph.jar` 及 `server/` 下的启动脚本为历史遗留，不再作为运行时；保留仅为便于协议对照。

## 游戏数据

`backend/data/player_template.json`、`backend/fixtures/*.json` 中的游戏数据结构与静态数据用于本机服务端协议兼容，版权归鹰角网络所有。本仓库不包含游戏客户端安装包。

## 使用范围

本项目仅用于个人学习、协议研究与自有设备测试。
