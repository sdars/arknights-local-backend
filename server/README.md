# Java 后端目录

本目录中的以下文件不提交到 Git，但运行时必须存在：

| 文件/目录 | 说明 |
|-----------|------|
| `hypergryph.jar` | LocalArknights 构建产物。上游：https://github.com/loongling/LocalArknights |
| `data/` | 上游 `LocalArknights_rootlocation/data/`，包含游戏数据表与 `defaultSyncData.json` |
| `config.json` | 由 `start.sh` 根据 `.env` 和 `config.example.json` 生成 |

## 重新构建 jar

```bash
git clone https://github.com/loongling/LocalArknights.git
cd LocalArknights
mvn -DskipTests package
cp 'target/hypergryph-v 0.5.3.0 Beta.jar' /path/to/server/hypergryph.jar
```

## 复制数据

```bash
cp -r LocalArknights/LocalArknights_rootlocation/data /path/to/server/data
```

## 启动

```bash
./start.sh
```

`start.sh` 会生成 `config.json`，并执行：

```bash
java -jar hypergryph.jar --server.address=127.0.0.1
```

如果该参数没有生效（部分版本的自定义 Tomcat connector 会忽略它），请运行
`../scripts/firewall_local.sh` 限制 8443/36650 只允许本机访问。

