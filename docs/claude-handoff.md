# Claude Handoff Workflow

## 目标

让 Codex 与 Claude 在同一个仓库里协同开发时，能快速知道：

- 上一个 agent 做到了哪里
- 哪些文件是当前事实来源
- 哪些任务已经被认领
- 接下来最安全的落点是什么

## 单一事实源

协作时始终以以下文件为准：

1. `handoff/current_status.md`
2. `docs/architecture.md`
3. `config/settings.example.toml`

其中 `handoff/current_status.md` 是 handoff 的主记录。

## 标准 handoff 流程

### 开始工作前

1. 阅读 `handoff/current_status.md`
2. 确认当前认领任务与阻塞项
3. 在修改前记录自己准备处理的范围

### 工作中

- 只改自己认领的模块
- 若必须跨模块变更，在 handoff 文件中先记录影响范围
- 若发现新风险，立即补充到 `风险 / 待确认` 区域

### 结束工作时

1. 更新 `已完成`
2. 更新 `进行中`
3. 更新 `下一步建议`
4. 写明改动文件、验证结果、未完成原因

## handoff 文件模板约定

- `任务状态` 使用 `TODO / IN_PROGRESS / BLOCKED / DONE`
- `负责人` 填 `Codex`、`Claude` 或具体人名
- `影响文件` 写绝对或仓库相对路径
- `验证` 明确写已跑的命令，未验证要写原因

## PR 约定

- PR 描述必须包含 handoff 摘要
- 若修改接口、配置、流程文档，PR 中必须点名对应文件
- 合并前确认 `handoff/current_status.md` 不是过期状态

