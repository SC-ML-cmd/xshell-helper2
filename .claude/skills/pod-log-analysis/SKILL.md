---
name: pod-log-analysis
description: >
  连接到远程 POD 后，高效搜索和分析 Java/Spring Boot 微服务日志。
  适用于通过 xshell-mcp 连接 POD 并需要排查问题的场景。
  触发词：查日志、看报错、traceId、日志分析、日志搜索、排查问题、定位异常。
---

# POD 日志分析

## 概述

本技能用于在日志量大、轮转快、含大量 `.gz` 的场景下，稳定完成排障分析。
核心原则：**同一次分析尽量基于同一份缓存快照**，减少轮转带来的结果漂移。

## 前置条件

- 已通过 xshell-mcp 连接到远程 POD（`connect_session` 已完成）
- MCP 已在项目级配置好日志参数（`config.py` 默认值 + `XSH_SEARCH_*` 环境变量）

## 模式说明（search_logs）

- `search`：直接在原始日志中快速探索（适合先找线索）
- `estimate`：统计 + 生成缓存一体化（返回 `cache_file/total_lines/top_files`）
- `extract`：提取匹配行到缓存（适合 traceId 精准拉取）
- `extract_context`：提取匹配 + 上下文到缓存（适合异常堆栈）
- `filter`：在缓存上二次过滤
- `context`：查看单个文件/缓存中的上下文片段

## 推荐流程

### 路径 A：已知 traceId 或明确关键字（优先）

1. 先 `estimate` 冻结数据并判断规模
```python
search_logs(keyword="traceId_xxx", mode="estimate", fixed_string=True, timeout=90)
```

2. 规模可控时直接 `filter`
```python
search_logs(keyword="ERROR", mode="filter", cache_file="...", max_lines=50)
```

3. 需要堆栈时用 `context`
```python
search_logs(keyword="Exception", mode="context", cache_file="...", after=80)
```

### 路径 B：只知道“某时间段有报错”

1. 先 `search` 小窗口探索
```python
search_logs(keyword="ERROR", mode="search", time_range="14:30-14:35", max_lines=30)
```

2. 从结果提取 traceId/异常名

3. 转路径 A：用提取出的关键字执行 `estimate` 或 `extract_context`

### 路径 C：重点看异常堆栈

1. 直接 `extract_context`
```python
search_logs(keyword="NullPointerException", mode="extract_context", before=20, after=120, timeout=120)
```

2. 在缓存上 `filter` 收敛，再 `context` 精细查看

## 参数建议

- `fixed_string=True`：traceId、requestId、订单号等字面值标识优先开启
- `case_sensitive=False`：大小写不稳定时开启（如 error/ERROR 混用）
- `timeout=60-120`：搜索大量 `.gz` 时适当增大
- `max_lines=30-80`：探索阶段尽量小，减少无效输出
- `offset`：翻页查看更多结果

## 输出与动作规范

每一步分析后都应输出：

1. 当前结论（命中多少、是否截断、下一步风险）
2. 下一条建议调用（可直接执行）
3. 停止条件（何时可结束，何时要扩大范围）

## 注意事项

1. 日志轮转会改变“文件位置”，但不会改变已写入内容；同一次分析应尽量复用 `cache_file`
2. `estimate/extract/extract_context` 若返回 `truncated=true`，先 `filter` 收敛再看上下文
3. 清理缓存可执行：
   - `execute_command("rm /tmp/xshell_cache_*.txt")`

## 固定调用模板

可直接复用以下文档中的三套标准剧本：

- `references/log-triage-playbooks.md`
  - 模板 1：traceId 已知
  - 模板 2：只知道时间窗
  - 模板 3：异常堆栈优先
- `references/release-package-checklist.md`
  - 单独发布时的目录结构、依赖与验收检查清单
