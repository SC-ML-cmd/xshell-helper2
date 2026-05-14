# POD 配置映射

第一列 POD前缀 是 `kubectl get pods` 的 grep 关键词。

| POD前缀 | JAR前缀 | 工作目录 | 启动脚本 | 日志路径 | 下载基础URL |
|---------|---------|---------|---------|----------|-------------|
| dwscontroller | dws-controller-server | /opt/cloud/dws-controller-server/ | sh bin/start.sh &> /dev/null & | /logs/ossres-{namespace}.log | https://xxx.xxx.com:443/t30010290/wsc/ |

说明：
- `{namespace}` 会自动替换为实际的 k8s 命名空间
- JAR 文件名 = `{JAR前缀}-{version}.jar`
- 下载基础URL 末尾带 `/`，拼接 JAR 文件名即为完整下载地址

> 可在下方按格式新增其他服务配置。
