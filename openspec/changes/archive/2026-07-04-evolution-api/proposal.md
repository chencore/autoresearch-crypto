## 为什么

R-v0.1-ck-6 要求前端能调优策略参数,展示进化曲线 / 当前最佳 / 最终结果。`dex/evolution.py` 提供 ATLAS 多策略进化引擎(4 agents × N 代,每代评估 / 交叉 / 变异 / softmax 重平衡权重),`dex/reflection.py` 提供 GEPA 反思式进化(假设 → 实验 → 反思 → 元反思)。两个引擎都是同步阻塞函数(`run_evolution` / `gepa_evolve`),前端需要实时看到每代 / 每周期的进度,不能用 HTTP 轮询(进度密集 + 需要主动推送完成事件)。

本 task 在后端用线程跑进化循环,通过 WebSocket 推送每代 / 每周期事件,前端订阅 `ws://host/api/v1/ws/evolve/{run_id}` 实时展示进化曲线。REST 接口提供 start / list / stop 控制。

## 变更内容

- 新增 `backend/app/schemas/evolve.py`:`EvolveStartRequest` / `EvolveStartResponse` / `EvolveRunSummary` / `EvolveRunListResponse` / `EvolveRunDetail` / `EvolveStopRequest` / `EvolveStopResponse` 7 个 Pydantic 模型
- 新增 `backend/app/services/evolution_manager.py`:
  - `EvolutionManager` 单例 + `threading.Lock`,管理 `run_id → EvolutionRun` 映射
  - `EvolutionRun` dataclass:id / config / status / current_gen / agents / event_buffer / subscribers / cancel_event / thread / error / started_at / completed_at
  - `create_run(config) -> run_id`:创建 run + 启动线程
  - `get_run(run_id) -> EvolutionRun | None`
  - `list_runs() -> list[dict]`
  - `request_stop(run_id)`:设置 `cancel_event`(线程下次循环检查后退出)
  - `broadcast(run_id, event)`:append event_buffer(cap 500)+ `asyncio.run_coroutine_threadsafe(ws.send_json(event), loop)` 推所有订阅者
  - `set_loop(loop)`:启动时捕获主事件 loop 引用
  - `subscribe(run_id, ws)` / `unsubscribe(run_id, ws)`
- 新增 `backend/app/services/evolution_runner.py`:
  - `_run_atlas(run_id, config, df)`:实例化 `EvolutionEngine`,for gen in 1..N 调 `engine.evolve(df, gen)`,每代检查 cancel_event + broadcast `{type:"generation", generation, total, agents:[{name,style,score,weight,params,generation}]}`;完成 broadcast `{type:"completed", best_agent, final_weights}`
  - `_run_gepa(run_id, config, df)`:实例化 `ReflectionEngine` + `create_default_agents()`,构造 `evaluate_fn`(参考 `scripts/evolve_gepa.py:make_evaluate_fn`),for cycle in 1..N 调 `engine.run_experiment()` + 每 5 周期 `engine.meta_reflect()`,每周期 broadcast `{type:"cycle", cycle, total, agent, hypothesis, score_before, score_after, accepted}`;每 5 周期 broadcast `{type:"meta_reflection", cycle, summary}`;完成 broadcast `{type:"completed", experiment_count, meta_count, blind_spots}`
  - 顶层 `run_evolution_thread(run_id, config)`:加载 df(复用 `backtest_runner._load_data` 模式)+ dispatch atlas/gepa + 捕获异常 → manager.mark_failed
- 改造 `backend/app/api/v1/evolve.py`:
  - `POST /api/v1/evolve/start` → body `{engine, symbol, interval, days, generations, evolution_interval?}` → `{run_id, status:"running"}`
  - `GET /api/v1/evolve/runs` → 列所有 run(id/status/engine/symbol/current_gen/total_generations/started_at)
  - `GET /api/v1/evolve/runs/{run_id}` → run 详情(含最新 agents state + event_buffer 摘要)
  - `POST /api/v1/evolve/stop` → body `{run_id}` → `{run_id, status:"stopping"}`(实际停止异步,通过 WS 推 stopped 事件)
- 改造 `backend/app/api/v1/ws.py`:新增 `@router.websocket("/evolve/{run_id}")` 端点
  - 接受连接 → 校验 run_id 存在 → send_json(event_buffer 全部) → 注册 ws 到 run.subscribers → 保持连接直到 run 完成 或 客户端断开
  - 客户端可发 `{action:"stop"}` 请求停止
- 改造 `backend/app/main.py`:`@app.on_event("startup")` 捕获 `asyncio.get_running_loop()` → `evolution_manager.set_loop(loop)`

## 功能 (Capabilities)

### 新增功能
- `evolution-api`: 后端 ATLAS / GEPA 进化引擎 + WebSocket 推送每代进度,REST 控制启停 + 列表

### 修改功能
<!-- 无 -->

## 影响

- 新增文件:`backend/app/schemas/evolve.py`、`backend/app/services/evolution_manager.py`、`backend/app/services/evolution_runner.py`
- 修改文件:`backend/app/api/v1/evolve.py`(占位重写)、`backend/app/api/v1/ws.py`(新增 evolve WebSocket)、`backend/app/main.py`(startup 捕获 loop)
- 复用:`dex/evolution.py`(`EvolutionEngine` / `create_default_agents`)、`dex/reflection.py`(`ReflectionEngine` / `Hypothesis`)、`dex/strategies/base.py`(`StrategyEvaluator`)、`backend/app/services/strategy_registry.py`(策略发现,GEPA evaluate_fn 用)、`backend/app/api/error.py` 统一错误格式(若已存在)
- 数据依赖:`data/crypto/<symbol>_<interval>_<days>d.parquet`(由 `prepare_crypto.py` 生成)
- 环境依赖:根 `uv` 环境(含 torch / pandas / numpy);macOS arm64 torch 装不上时,启动 atlas/gepa 会抛 ImportError → run 标 failed,WS 推 error 事件
- 不动:`dex/` 代码、`live_*.py` 脚本、其他 router、前端
