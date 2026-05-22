---
name: pod-log-analysis
description: >
  连接到远程 POD 后，高效地搜索和分析 Java/Spring Boot 微服务日志。
  适用于通过 xshell-mcp 连接 POD 并需要排查问题的场景。
  触发词：查日志、看报错、traceId、日志分析、日志搜索、排查问题、定位异常。
---

# POD 日志分析

## 概述

连接到远程 POD 后，高效地搜索和分析 Java/Spring Boot 微服务日志。适用于通过 xshell-mcp 连接 POD 并需要排查问题的场景。

## 何时使用

- 用户说「帮我查下日志」「看看报错」「traceId 是 xxx」
- 分析代码后需要去 POD 确认执行流程
- 接口报错需要定位原因
- 需要确认某段代码是否被执行

## 前置条件

- 已通过 xshell-mcp 连接到远程 POD（`connect_session` 已完成）
- 项目配置了 `.xshell-log.json`（描述日志目录、格式等）

## 双路径工作流

根据是否有明确搜索关键字，选择不同路径：

### 路径 A：有明确关键字（traceId、具体异常名）

适用于：用户提供了 traceId、知道具体的异常类名、或从代码中找到了关键日志文本。

**1. extract 提取快照：**

```
search_logs(keyword="traceId_xxx", mode="extract")
```

- 一次性原子化搜索所有日志文件（含 .gz），结果缓存到临时文件
- 解决日志轮转竞态问题（文件可能在多次查询间轮转）
- 返回 cache_file 路径和总行数

**2. filter 二次筛选：**

```
search_logs(keyword="ERROR", mode="filter", cache_file="...")
```

- 在缓存文件上进一步过滤（如只看 ERROR、按时间缩小）
- extract 结果可能上万行，必须用 filter 缩小范围

**3. context 查看完整堆栈：**

```
search_logs(keyword="NullPointerException", mode="context", cache_file="...", after=80)
```

- 获取异常匹配处的前后上下文
- after 建议 50-100（Java 堆栈通常在后面）

**4. 结合代码分析根因**

### 路径 B：无明确关键字（探索性查询）

适用于：用户只知道大概时间、只说「有报错」、或需要先浏览日志发现问题。

**1. search 直接搜索：**

```
search_logs(keyword="ERROR", mode="search", time_range="14:30-14:35", max_lines=30)
```

- 用时间范围 + 日志级别直接在原文件上搜索
- 无需 extract（因为还不知道要提取什么）

**2. 发现线索：** 从搜索结果中识别 traceId、异常类名等

**3. 转入路径 A：** 用发现的关键字进行 extract，深入分析

## 输出管理策略

- **永远先评估数据量**：如果不确定关键字匹配多少行，先用 extract 看 total_lines
- **extract 有上限保护**：max_extract_lines（默认 10000）防止提取过多
- **search 直接搜索时**：max_lines 控制在 30-80 行
- **需要更多时用 offset 分页**：offset=50 跳过前 50 行
- **关键字精确度排序**：traceId > 异常类名 > ERROR > WARN

## 常见场景

### 场景 1：确认代码执行流程

```
1. 从代码中找到关键日志（如 logger.info("开始处理订单...")）
2. search_logs("开始处理订单", mode="search", max_lines=20)
3. 根据日志有无判断代码是否执行到该分支
```

### 场景 2：traceId 报错定位

```
1. search_logs("abc123", mode="extract") → 提取到缓存
2. search_logs("ERROR", mode="filter", cache_file="...") → 筛选错误
3. search_logs("Exception", mode="context", cache_file="...", after=80) → 完整堆栈
4. 分析堆栈，定位代码行
```

### 场景 3：按时间探索报错

```
1. search_logs("ERROR", mode="search", time_range="14:00-14:30", max_lines=30)
2. 从结果中发现 traceId
3. search_logs(traceId, mode="extract") → 提取该请求完整日志
4. search_logs("Exception", mode="filter", cache_file="...") → 筛选异常
```

## 注意事项

1. **压缩文件搜索较慢**：50 个 .gz 文件搜索可能需要 30-60 秒，timeout 设为 60-120
2. **日志轮转问题**：如果直接用 search 模式多次查询同一关键字结果不一致，说明发生了轮转，应改用 extract 模式
3. **缓存清理**：分析完成后，提醒用户缓存文件可用 `execute_command("rm /tmp/xshell_cache_*.txt")` 清理
4. **文件范围缩小**：如果知道大概时间，用 file_pattern 限定文件范围减少搜索时间
