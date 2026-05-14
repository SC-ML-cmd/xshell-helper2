---
name: deploy-to-pod
description: >
  当用户需要将JAR包部署到K8s POD时使用此Skill。
  适用于：换包、部署、上线、发布到测试环境等场景。
  触发词：部署、换包、上线、发布、deploy、更新POD、重启服务。
  即使用户只说环境名+POD名（如"帮我把dwscontroller部署到曹彦环境"），也应触发此Skill。
---

# 部署到 K8s POD

## 概述

将本地编译的 JAR 上传 OBS 后，通过 Xshell 终端登录 K8s POD 完成换包重启。
全程 6 步，每步依赖上一步，任一步失败即终止并通知用户。

## 参数

| 参数 | 必填 | 说明 | 示例 |
|------|------|------|------|
| 环境 | 是 | 环境别名 或 命名空间 | 曹彦环境 / 测试环境 / caoyan |
| POD前缀 | 是 | `kubectl get pods` 的 grep 关键词 | dwscontroller |
| JAR版本 | 否 | 不提供则从 pom.xml 提取 | 5.0.9.1-SNAPSHOT |
| CSM节点 | 否 | k8sMaster 在 CSM 中的 ID，默认 `11.11` | 11.11 |

## 前置：读取配置

在执行任何步骤之前，先读取两个参考文件：

1. `references/env-mapping.md` — 环境别名 → 命名空间
2. `references/pod-config.md` — POD前缀 → JAR前缀、工作目录、启动脚本、日志路径、下载URL

解析用户的"环境"参数：如果是别名则查 env-mapping.md 转为命名空间。

## 工具使用规则

- **交互式操作**（菜单选择、登录）→ 用 `mcp__xshell__send_raw`，文本末尾必须带 `\r`
- **一次性命令**（kubectl、cd、curl 等）→ 用 `mcp__xshell__execute_command`
- `send_raw` 不会自动追加回车，`execute_command` 会自动追加

## 工作流

### Step 0: 参数解析

1. 解析"环境"参数 → 查 `references/env-mapping.md` 转为命名空间
2. 解析"POD前缀" → 查 `references/pod-config.md` 获取 JAR前缀、工作目录、启动脚本、日志路径
3. 若用户未提供 JAR 版本 → 从本地 `pom.xml` 提取 `<version>` 标签值
4. JAR 文件名 = `{JAR前缀}-{version}.jar`

任一查不到 → 终止，让用户补充信息。

### Step 1: 本地编译

执行：
```bash
mmvn clean install -DskipTests
```
使用 `Bash` 工具，设置较长超时（600s）。
检查输出：出现 `BUILD SUCCESS` → 继续。
否则 → 终止，输出编译失败信息。

### Step 2: 上传 OBS

执行：
```bash
sh /d/scripts/shell/direct_upload.sh 7
```
使用 `Bash` 工具，超时 120s。
检查输出：包含 `upload success` → 继续。
否则 → 终止。

### Step 3: 上下文检测

调用 `mcp__xshell__get_screen(lines=5)` 读取终端最后 5 行，判断当前位置：

| 提示符特征 | 当前位置 | 后续动作 |
|-----------|----------|----------|
| 包含 `Opt or ID>` | CSM 菜单 | 执行 Step 4a → 4b → 4c |
| 包含 `root@` | k8s Master 节点 | 跳过 4a，执行 Step 4b → 4c |
| 包含 `service@` | 已在 POD 内 | 跳过 4a,4b,4c，直接执行 Step 5 |

无法匹配任何特征 → 终止，告知用户当前终端状态不明，请手动确认位置。

### Step 4a: CSM 登录到 k8s Master

> 仅在 Step 3 检测到 CSM 菜单时执行。

用 `mcp__xshell__send_raw` 逐步通过 CSM 菜单：

```
# 第一步：选择 k8sMaster 节点组
send_raw("{csm_node}\r", wait_for="Opt or ID>", timeout=60)

# 第二步：选择主机（列表第一个）
send_raw("0\r", wait_for="ID>", timeout=60)

# 第三步：选择 root 账号
send_raw("2\r", wait_for="#", timeout=60)
```

每步 `\r` 不可省略。任一步 `timed_out=True` → 终止。

### Step 4b: 获取 POD 名称

> 仅在 k8s Master 节点上执行（Step 3 检测到 `root@` 或刚完成 Step 4a）。

用 `mcp__xshell__execute_command`：

```
kubectl get pods -n {namespace} | grep {pod_prefix}
```

超时 30s。从输出的 `output` 字段提取完整 POD 名（第一列，如 `dwscontroller-5b85b7bc99-gfkk6`）。
若有多个匹配 → 选状态为 `Running` 的第一个。
获取失败 → 终止。

### Step 4c: 进入 POD

> 在 k8s Master 节点上执行。

用 `mcp__xshell__send_raw`：

```
send_raw("kubectl exec -it {pod_name} -n {namespace} bash\r", wait_for="service@", timeout=30)
```

提示符变为 `[service@{pod_name} ...]$` 表示已进入 POD。
超时 → 终止。

### Step 5: 换包重启

> 已在 POD 内，用 `mcp__xshell__execute_command` 依次执行。

**5.1 进入工作目录并删除旧包：**
```
cd {work_dir} && rm -f {jar_name}
```
超时 15s。检查 `output` 无明显错误。

**5.2 下载新包：**
```
curl -kv -o {jar_name} {download_base_url}{jar_name}
```
超时 120s。检查 `output` 包含 `100`（curl 进度）或无明显错误。

**5.3 赋权：**
```
chmod +x {jar_name}
```
超时 10s。

**5.4 停止旧进程：**
```
ps -ef | grep java | grep -v grep | awk '{print $2}' | xargs -r kill -9
```
超时 15s。

**5.5 启动：**
```
sleep 3 && sh {start_script} &> /dev/null &
```
超时 10s。

每步若 `timed_out=True` 或 `output` 含明显错误 → 终止。

### Step 6: 等待启动就绪

**6.1 等待 5 分钟：**
```
mcp__xshell__execute_command("sleep 300", timeout=360)
```

**6.2 拉取最近 100 行日志：**
```
mcp__xshell__execute_command("tail -100 {log_path}", timeout=30)
```
其中 `{log_path}` 来自 pod-config.md（`{namespace}` 已替换）。

**6.3 在日志输出中检查 WebFlux 启动标志：**

- 主要标志：`Started` + 类名 + `in` + 秒数（如 `Started Application in 8.5 seconds`）
- 备用标志：`Netty started on port`

找到 → 部署成功。
找不到 → 报告"已等待 5 分钟，未在日志中检测到启动标志，请手动检查"。

## 错误处理

每一步失败时：
1. 输出 `[Step N] 失败：{具体原因}`
2. **不执行后续步骤**
3. 终端保持在当前位置，方便用户手动排查
4. 已完成的步骤不需要回滚

## 输出报告

部署全部完成后输出汇总：

```
========== 部署报告 ==========
环境: {环境别名} ({命名空间})
POD:  {pod_name}
JAR:  {jar_name}
-------------------------------
Step 1 编译:   ✓
Step 2 上传:   ✓
Step 3 上下文: ✓ (当前位置)
Step 4 登录:   ✓ (或 已跳过)
Step 5 换包:   ✓
Step 6 启动:   ✓ (或 ✗ 未检测到启动标志)
===============================
```

## 参考文件

| 文件 | 用途 | 何时读 |
|------|------|--------|
| `references/env-mapping.md` | 环境别名 → 命名空间映射 | Step 0 |
| `references/pod-config.md` | POD前缀 → 工作目录/JAR前缀/启动脚本/日志 | Step 0 |

用户可随时编辑这两个文件来扩展环境或服务配置。
