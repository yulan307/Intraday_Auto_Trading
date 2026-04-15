# Claude Collaboration Guide

在这个仓库中工作前，请先依次阅读：

1. `README.md`
2. `docs/architecture.md`
3. `docs/claude-handoff.md`
4. `handoff/current_status.md`

## 协作原则

- 不要覆盖 `handoff/current_status.md` 中未完成但已被其他 agent 认领的任务
- 任何跨模块修改前，先更新 handoff 文档中的“下一步建议”和“风险/阻塞项”
- 优先保持接口稳定，新增字段时同步更新文档与测试
- 完成工作后，必须回写 handoff 状态，便于 Codex/Claude 无缝接力

## 提交前检查

- 配置变更是否同步更新 `config/settings.example.toml`
- 模块职责变更是否同步更新 `docs/architecture.md`
- 是否补充/更新了相关测试
- 是否更新 `handoff/current_status.md`

