# Current Handoff Status

## 上下文

- 日期: 2026-04-15
- 当前负责人: Codex
- 阶段: 项目初始化完成，等待接入真实行情与交易接口

## 任务状态

| 任务 | 状态 | 负责人 | 影响文件 | 说明 |
| --- | --- | --- | --- | --- |
| 建立项目骨架 | DONE | Codex | `README.md`, `src/`, `tests/` | 已完成基础目录、模型与服务骨架 |
| 建立 GitHub 工作流 | DONE | Codex | `.github/workflows/ci.yml`, `.github/pull_request_template.md` | 已补基础 CI 与 PR 模板 |
| 建立 Claude handoff 机制 | DONE | Codex | `CLAUDE.md`, `docs/claude-handoff.md`, `handoff/` | 已提供协作流程与状态模板 |
| 整理原始需求文档 | DONE | Codex | `docs/product-requirements.md`, `docs/trend-classification-spec.md` | 已将两份原始文档整理为规范开发文档 |
| 细化数据落库与回测落地方案 | DONE | Codex | `docs/implementation-plan-data-backtest.md`, `README.md` | 已补具体模块、表结构、接口与分阶段实施方案 |
| 实现市场数据 SQLite 底座 | DONE | Codex | `src/intraday_auto_trading/persistence/`, `src/intraday_auto_trading/interfaces/repositories.py`, `tests/test_market_data_repository.py` | 已实现 schema、repository 和基础入库/读取测试 |
| 接入 IBKR 行情/交易实现 | TODO | Unassigned | `src/intraday_auto_trading/interfaces/`, `src/intraday_auto_trading/app.py` | 需要真实 API 适配器 |
| 接入 Moomoo 期权快照实现 | TODO | Unassigned | `src/intraday_auto_trading/interfaces/` | 需要补数据采集 |
| 完善三分类量化规则 | TODO | Unassigned | `src/intraday_auto_trading/services/trend_classifier.py` | 当前为启发式占位实现 |
| 实现回测账户库与模拟 Broker | TODO | Unassigned | `src/intraday_auto_trading/backtest/`, `src/intraday_auto_trading/interfaces/brokers.py` | 下一阶段建议实现回测订单生命周期 |

## 已完成

- 按需求文档拆出趋势判定、选股、追踪下单、执行编排四个核心层
- 增加配置模板、测试占位与 GitHub Actions
- 加入 Claude/Codex handoff 规则与状态管理文件
- 将两份原始草稿整理为 `docs/` 下的正式开发文档，统一后续引用入口
- 将数据落库和回测需求进一步拆成可开发的模块设计、SQLite 表结构和实施顺序
- 已完成市场数据 SQLite schema、repository 抽象和基础入库/读取测试

## 进行中

- 无

## 风险 / 待确认

- IBKR 与 Moomoo 的字段命名、权限和节流限制尚未确认
- “立即买入”究竟使用市价单还是保护性限价单，仍需业务确认
- 15 分钟追踪里“连续 N 根 bar 未创新低”的默认 N 现设为 2，需要策略回测确认
- 期权合约唯一标识当前支持由 `contract_id` 显式传入或使用规则拼装，后续接真实行情时需统一正式映射规则

## 验证

- 已执行: `pytest`
- 未执行: 真实 API 联调；原因是当前仓库尚无 IBKR/Moomoo 凭据与网关实现

## 下一步建议

1. 优先实现 `MarketDataGateway` 的 paper/mock 版本，先打通本地流程
2. 实现 `backtest.sqlite`、回测订单状态机和 `BacktestBrokerGateway`
3. 用历史 1m/15m 数据补齐三分类回放测试
