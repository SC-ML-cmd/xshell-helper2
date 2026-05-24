# pod-log-analysis 单独发布包结构清单

## 1. 发布目标

将 `pod-log-analysis` skill 作为独立包复制到其他项目或其他机器后，仍可直接触发并执行日志排障流程。

## 2. 最小可发布结构（必带）

```text
pod-log-analysis/
├─ SKILL.md
└─ references/
   ├─ log-triage-playbooks.md
   └─ release-package-checklist.md
```

## 3. 路径与引用检查

1. `SKILL.md` 中引用模板路径必须是相对路径：
   - `references/log-triage-playbooks.md`
2. 发布后目录层级不要改变，避免相对路径失效。
3. 如需重命名目录，请同步修改 `SKILL.md` 的引用路径。

## 4. 运行依赖（MCP 能力）

本 skill 依赖 `xshell-mcp` 提供以下工具能力：

1. `connect_session`
2. `search_logs`
3. `execute_command`（用于清理缓存等辅助动作）

`search_logs` 需支持以下模式与参数：

1. 模式：`search / estimate / extract / extract_context / filter / context`
2. 参数：`fixed_string`、`case_sensitive`、`timeout`、`offset`
3. 返回字段至少包含：`cache_file`、`total_lines`、`truncated`、`timed_out`

## 5. 项目级配置约定

建议在 MCP 启动环境中按需配置：

1. `XSH_SEARCH_LOG_DIR`
2. `XSH_SEARCH_FILE_PATTERN`
3. `XSH_SEARCH_COMPRESSED_EXTENSIONS`
4. `XSH_SEARCH_MAX_EXTRACT_LINES`

其余 `XSH_SEARCH_*` 参数可保持默认。

## 6. 发布前检查（Checklist）

1. 文件完整：
   - `SKILL.md` 存在
   - `references/log-triage-playbooks.md` 存在
   - `references/release-package-checklist.md` 存在
2. 引用有效：
   - `SKILL.md` 中 `references/log-triage-playbooks.md` 可访问
3. 能力兼容：
   - 目标环境的 `search_logs` 支持 `estimate` 与 `extract_context`
4. 配置可用：
   - 至少确认 `XSH_SEARCH_LOG_DIR` 与 `XSH_SEARCH_FILE_PATTERN` 正确
5. 基础连通：
   - `connect_session` 成功

## 7. 发布后验收（Smoke Test）

按顺序执行以下三步（任意测试环境）：

1. `search` 探索
```python
search_logs(keyword="ERROR", mode="search", max_lines=20, timeout=60)
```

2. `estimate` 冻结+统计
```python
search_logs(keyword="traceId_xxx", mode="estimate", fixed_string=True, timeout=90)
```

3. `filter/context` 深入
```python
search_logs(keyword="ERROR", mode="filter", cache_file="...", max_lines=50)
search_logs(keyword="Exception", mode="context", cache_file="...", after=80)
```

验收通过标准：

1. 三步均有稳定返回（非工具异常）
2. `estimate` 返回可用 `cache_file`
3. `filter/context` 能基于同一 `cache_file` 连续分析

## 8. 版本建议

建议在发布说明中记录：

1. skill 版本（例如 `pod-log-analysis v2`）
2. 适配 MCP 版本（例如 `xshell-mcp >= 0.2.x`）
3. 本次更新要点（例如“引入 estimate 冻结统计与 extract_context”）

## 9. 常见问题

1. 问：复制后能触发 skill，但执行失败？
   - 检查目标环境 MCP 是否已升级到支持 `estimate/extract_context` 的版本。
2. 问：路径引用报错？
   - 检查 `references/` 是否和 `SKILL.md` 同级目录结构一致。
3. 问：结果仍然漂移？
   - 同一次分析应复用同一个 `cache_file`，避免反复回源扫描。
