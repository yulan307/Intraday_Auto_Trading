# 数据获取与本地保存链路执行方案

## Summary
- 建议将本计划保存为 `docs/market-data-pipeline-plan.md`。
- 执行时从 `main` 创建分支 `feat/market-data-pipeline`。
- 本次分支目标是搭出“真实 provider 骨架 + 单命令探测/同步 + SQLite 落库 + 能力报告”的主链，不要求一次性验证所有券商全部数据能力。
- `IBKR` 路线正式固定为 **IB Gateway**，不使用 TWS 作为默认接入基线；`Moomoo` 路线固定为 **OpenD**。
- 数据类型按三类实现：`bars/session metrics`、`opening imbalance`、`options`。允许部分成功并落库，失败或不支持项在汇总结果中明确标记。
- `15m` 第一版同时保留两种来源：provider direct 15m 与本地由 1m 聚合的 derived 15m。

## Key Changes
- 新增 provider 适配层与能力探测。
  - `IBKR` 适配器基于 **IB Gateway socket API** 实现，负责 `1m bars`、direct `15m bars`、`official_open/last_price/session_vwap` 所需原始数据，并尝试 `opening imbalance`。
  - `Moomoo` 适配器基于 **OpenD** 实现，负责 `option quotes / contract metadata`，并对 bars 与 opening imbalance 返回明确的 `unsupported` 或 `untested` 状态。
  - 两个 provider 都采用可选依赖 + 延迟导入；缺 SDK、本地服务未启动、端口不可连时，只影响对应 provider。
- 新增采集编排服务，统一执行“探测 + 抓取 + 标准化 + 落库 + 汇总”。
  - 外部输入统一使用 `symbols: list[str]`；CLI 可传单个或多个 symbol，但 service 内部一律按列表处理。
  - service 层优先利用券商 API 的多标的能力；若不支持则自动降级为逐标的请求。
  - bars 路径固定先拿 `1m` 并入库，再生成 derived `15m`；若 provider 还支持 direct `15m`，则额外拉取并保存。
  - direct 与 derived `15m` 用不同 `source` 标识区分，避免主键冲突并保留后续比对能力。
  - session metrics 优先使用 provider 原始值；若源只提供 bars，则由服务层计算 `official_open`、`last_price`、`session_vwap`。
  - opening imbalance、options 采用“拿到即落库，拿不到则记录能力/诊断结果”的策略。
- 扩展配置模型并固化认证方式。
  - `IBKR` 使用 **IB Gateway 本地已登录会话**；项目代码不做网页登录自动化，只负责连接检查、会话状态检查和错误提示。
  - `IBKR` 配置按双档案实现：`paper` 与 `live` 分开配置，并提供 `default_profile`。
  - `IBKR` 配置字段围绕本地连接：`host`、`port`、`client_id`、`account_id`、`readonly` 等；不引入 API key/secret 模型。
  - `Moomoo` 使用 **OpenD 本地已登录会话**；项目代码不负责网页登录，只连接本地 OpenD。
  - `Moomoo` 保持单账户配置，至少包含 `host`、`port`、`account_id`、`market`；若后续需要自动 relogin，再单独扩展 `login_account/login_pwd_md5`。
- 扩展 CLI 为单命令模式。
  - 新增 `intraday-auto-trading sync-market-data`，单次执行同时完成能力探测、数据抓取、落库和结果摘要。
  - CLI 支持 `--symbols`，可传 1 个或多个 symbol；不传时使用 `symbols.pool`。
  - CLI 支持 `--providers ibkr moomoo`。
  - CLI 支持 `--ibkr-profile paper|live` 覆盖默认配置；未指定时读取 `ibkr.default_profile`。
  - 命令输出按 `provider x data_type x symbol` 汇总 `success / unsupported / unavailable / failed`，并显示写入条数与错误摘要。
- 持久化层只做必要扩展。
  - 复用现有 `symbols`、`price_bars`、`session_metrics`、`opening_imbalance`、`option_contracts`、`option_quotes`。
  - 若现有仓储接口不足以表达多来源 15m 或同步摘要，则只补最小方法/返回对象。
  - 本次不改 broker、account state、scheduler、backtest 逻辑。
