# Tasks

> **维护规则**：
> - **任务内容**由人工维护，仅在人工明确要求时修改
> - **任务完成状态**由 AI 在对应 openspec 变更归档后自动勾选
>
> 每个任务对应 `openspec/changes/<task-name>/` 下的一个变更提案。

---

## 命名约定

- 任务名使用 **kebab-case**：`add-user-auth`、`implement-payment-flow`
- 粒度：**一个提案能做完的事情**（通常 1~3 天工作量）
- 尽量独立：减少任务间依赖，便于并行推进

---

## 版本 v0.1

> 单机本地量化工作台 MVP。目标：在现有 `dex/` 交易核心之上加 FastAPI + Vue 3 Web 界面，覆盖策略管理 / 回测可视化 / 实盘监控 / 参数调优四个模块。

- [x] **setup-backend-scaffold** — 初始化 FastAPI 后端骨架（路由结构、SQLite + SQLAlchemy、CORS、统一错误处理、配置加载），打通 `/health` 接口
- [x] **setup-frontend-scaffold** — 初始化 Vue 3 + Vite + TypeScript + Naive UI 前端骨架（路由、布局、API client、开发代理），打通首页
- [x] **strategy-management-api** — 后端扫描 `dex/strategies/`，提供策略列表、参数定义、运行状态接口
- [x] **strategy-management-ui** — 前端策略管理页（策略列表 + 详情抽屉），只读展示
- [x] **backtest-api** — 后端扫描 `data/crypto/`，提供交易对列表 + 回测执行接口（调 `BaseStrategy.simulate`），返回收益曲线 / 交易明细 / 指标
- [x] **backtest-ui** — 前端回测页：选交易对 + 策略 + 时间段 → 跑回测 → 展示收益曲线 + 交易明细 + 指标卡片
- [ ] **live-monitor-api** — 后端用子进程拉起 / 停止 `live_binance_quant.py` / `live_okx_quant.py` / `live_nado_quant.py`，流式读取 `logs/` 状态文件 + 进程 stdout
- [ ] **live-monitor-ui** — 前端实盘页：选交易所 → 启停 → 展示持仓 / 未实现盈亏 / 最近成交 / 实时日志
- [ ] **evolution-api** — 后端调 ATLAS（`dex/evolution.py`）或 GEPA（`dex/reflection.py`），通过 WebSocket 推送每代进度
- [ ] **evolution-ui** — 前端调优页：选引擎 + 配置 → 启动 → WebSocket 接进度 → 展示进化曲线 / 当前最佳 / 最终结果
- [ ] **integration-launch-script** — 一条命令拉起前后端的根目录启动脚本 + README 更新

---

## 进度概览

- 总任务数：11
- 已完成：6
- 进行中：0
