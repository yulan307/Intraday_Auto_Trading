# Intraday Auto Trading

基于两份需求文档构建的 Python 项目骨架，用于实现“每天定投，但尽量买在当日相对低位”的美股日内自动买入系统。

## 项目目标

- 在每个美东交易日开盘后 30 分钟内评估候选标的的当日趋势
- 按趋势分类结果和账户买入状态，选择当天最适合执行的标的
- 对强势标的立即买入，对震荡/弱势标的启用 15 分钟追踪限价机制
- 若全天未确认反弹，则在最后 15 分钟执行强制买入
- 兼容 IBKR 交易执行、IBKR/Moomoo 行情与期权数据

## 当前仓库内容

- `src/intraday_auto_trading/`: 项目源码
- `config/settings.example.toml`: 配置模板
- `docs/product-requirements.md`: 规范化产品需求文档
- `docs/trend-classification-spec.md`: 规范化趋势分类接口文档
- `docs/incremental-requirements-data-backtest.md`: 数据落库与回测增量需求
- `docs/implementation-plan-data-backtest.md`: 数据落库与回测具体落地方案
- `docs/architecture.md`: 模块拆分与责任说明
- `docs/claude-handoff.md`: Codex 与 Claude 的 handoff 机制
- `docs/market-data-pipeline-plan.md`: 市场数据 pipeline 落地方案
- `docs/market-data-capability-matrix.md`: IBKR/Moomoo 数据能力矩阵
- `docs/market-data-handoff.md`: 市场数据 pipeline 实现说明与 handoff
- `docs/manual/`: 原始需求文档归档
- `handoff/current_status.md`: 当前协作状态单一事实源
- `.github/workflows/ci.yml`: GitHub Actions 基础 CI

## 代码结构

```text
src/intraday_auto_trading/
├── app.py
├── cli.py
├── config.py
├── models.py
├── gateways/
│   ├── ibkr_market_data.py
│   └── moomoo_options.py
├── interfaces/
│   ├── brokers.py
│   └── repositories.py
├── persistence/
│   ├── market_data_repository.py
│   ├── schema.py
│   └── sqlite_base.py
└── services/
    ├── executor.py
    ├── market_data_sync.py
    ├── selector.py
    ├── tracker.py
    └── trend_classifier.py
```

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
copy config\settings.example.toml config\settings.toml
pytest
```

## 推荐开发顺序

1. ~~接入 IBKR 与 Moomoo 的真实/模拟数据网关~~ ✅ 已完成
2. 使 bars/session metrics 来源可在 IBKR 与 Moomoo 之间配置切换
3. 实现 `backtest.sqlite` 与模拟 Broker
4. 补齐开盘三分类的量化因子与评分逻辑
5. 增加回测与 paper 交易联调

## GitHub 使用建议

```bash
git add .
git commit -m "feat: bootstrap intraday auto trading project"
git branch -M main
git remote add origin <your-github-repo-url>
git push -u origin main
```

## 文档来源

- `docs/manual/初级开发文档说明.md`
- `docs/manual/当日趋势量化分析.md`

原始文档已整理为 `docs/` 下的规范开发文档，后续开发应优先引用整理后的版本。
