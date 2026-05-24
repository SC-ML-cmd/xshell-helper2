# 日志排障调用模板（XShell MCP）

适用目标：在测试环境或线上 POD 中，快速完成日志定位，并尽量减少轮转导致的重复查询。

## 模板 1：traceId 已知

### Step 1：冻结并统计
```python
search_logs(
    keyword="traceId_xxx",
    mode="estimate",
    fixed_string=True,
    timeout=90,
)
```

判断：
- `total_lines == 0`：traceId 可能错误，或时间范围不在当前日志集合中
- `truncated == true`：命中太多，先筛再看上下文
- 得到 `cache_file` 后，后续尽量都基于这个缓存

### Step 2：先筛错误级别
```python
search_logs(
    keyword="ERROR",
    mode="filter",
    cache_file="上一步返回的 cache_file",
    max_lines=80,
)
```

判断：
- 若无 ERROR，再尝试 `WARN` 或业务异常关键字

### Step 3：看堆栈上下文
```python
search_logs(
    keyword="Exception",
    mode="context",
    cache_file="同一个 cache_file",
    after=120,
)
```

结束条件：
- 已定位到异常类、调用链和可疑代码位置

## 模板 2：只知道时间窗

### Step 1：小窗口探索
```python
search_logs(
    keyword="ERROR",
    mode="search",
    time_range="14:30-14:35",
    max_lines=50,
    timeout=90,
)
```

判断：
- 从结果提取 traceId / requestId / 异常类名
- 无结果时扩大时间窗（例如 14:20-14:40）

### Step 2：转为冻结分析
```python
search_logs(
    keyword="提取出的 traceId 或异常名",
    mode="estimate",
    fixed_string=True,
    timeout=90,
)
```

### Step 3：筛选 + 上下文
```python
search_logs(keyword="ERROR", mode="filter", cache_file="...", max_lines=80)
search_logs(keyword="Exception", mode="context", cache_file="...", after=120)
```

结束条件：
- 找到对应请求的完整错误路径（入口日志 -> 错误日志 -> 堆栈）

## 模板 3：异常堆栈优先

### Step 1：直接提取上下文
```python
search_logs(
    keyword="NullPointerException",
    mode="extract_context",
    before=20,
    after=120,
    timeout=120,
)
```

判断：
- 如果 `truncated == true`，说明结果过大，先 `filter` 收敛

### Step 2：缓存内收敛
```python
search_logs(
    keyword="关键业务类名或 ERROR",
    mode="filter",
    cache_file="上一步 cache_file",
    max_lines=80,
)
```

### Step 3：精细上下文
```python
search_logs(
    keyword="Exception",
    mode="context",
    cache_file="同一个 cache_file",
    occurrence=1,
    after=120,
)
```

结束条件：
- 堆栈首个业务代码位置明确，且可回溯到触发请求

## 通用参数建议

- traceId / requestId 场景：`fixed_string=true`
- 大小写不稳定：`case_sensitive=false`
- 大量 `.gz` 场景：`timeout=60-120`
- 探索阶段：`max_lines=30-80`
- 多页查看：`offset=50/100/...`

## 失败重试策略

1. 超时：先缩小 `time_range` 或改更精确关键字，再加大 `timeout`
2. 无结果：检查关键字拼写、大小写，必要时关掉大小写敏感
3. 结果漂移：同一轮分析尽量复用同一个 `cache_file`
