## 上下文

`dex/evolution.py` 提供 `EvolutionEngine` 类 + `run_evolution(df, generations, ...)` 顶层函数。`run_evolution` 是 monolithic 同步函数,内部 `for gen in range(1, generations+1)` 循环调 `engine.evolve(df, gen)` 或仅 evaluate。要 WebSocket 推送每代进度,后端不能直接调 `run_evolution`,必须自己复刻循环 + 每代 broadcast。

`dex/reflection.py` 同理,`gepa_evolve(agents, evaluate_fn, engine, cycles)` monolithic,内部 `for cycle in range(1, cycles+1)` 调 `engine.run_experiment()` + 每 5 周期 `engine.meta_reflect()`。后端复刻循环。

`scripts/evolve_gepa.py:make_evaluate_fn` 提供 evaluate_fn 闭包模板:从 params keys 推断 strategy_cls(`grid_spacing_pct` → GridStrategy / `rsi_low+rsi_high` → HybridMeanRevMomentum / `trend_ma_period+adx_threshold` → PureAction / 默认 Trend),用 `StrategyEvaluator.simulate + compute_metrics` 算 (score, sharpe, ret, max_dd)。后端复用此模式。

两个引擎都 CPU 密集(numpy / pandas),不能在 asyncio 主 loop 跑(会阻塞所有 WebSocket)。必须用 `threading.Thread` 后台跑。线程 → 主 loop 推事件用 `asyncio.run_coroutine_threadsafe(coro, loop)`。

后端已有 `app/api/v1/ws.py` 占位(`/{topic}`),本 task 改造为 `/evolve/{run_id}` 端点。`app/api/v1/evolve.py` 也是占位,本 task 重写 4 个 REST 接口。

## 目标 / 非目标

**目标:**
- 4 个 REST 接口:start / runs / runs/{id} / stop
- 1 个 WebSocket 端点:/ws/evolve/{run_id}
- ATLAS / GEPA 两个引擎,后台线程跑,WS 推每代 / 每周期事件
- 线程安全(run 映射 + cancel_event + 跨 loop 推送)
- cancel_event 协作式停止(线程下次循环检查)
- event_buffer 让 late subscriber 追上进度

**非目标:**
- 不持久化 run(后端重启全丢,v0.1 接受)
- 不限制并发 run 数(v0.1 单机个人用,用户自行节制)
- 不实现 gepa_evolve_v2(用基础版 `gepa_evolve` 逻辑,v0.2 升级)
- 不实现 ATLAS 的 ensemble_signal(后端不预测,只优化参数)
- 不动 `dex/` 代码、`live_*.py`、前端
- 不做认证(单机本地)
- 不限制 run 历史增长(v0.1 接受内存增长,用户手动重启清理)

## 决策

### 决策 1:复刻进化循环,不调顶层 `run_evolution` / `gepa_evolve`
- **选择**:后端 `_run_atlas` / `_run_gepa` 自己 for 循环,每代 / 每周期调 `engine.evolve()` / `engine.run_experiment()` + broadcast
- **替代方案**:调 `run_evolution(verbose=False)` + monkey-patch print 捕获进度
- **理由**:print 捕获是 hack 且会丢结构化数据(如 params dict);复刻循环 5 行代码,完全控制 broadcast 时机与事件结构。`engine.evolve(df, gen)` 是公开方法,API 稳定。

### 决策 2:线程跑,不用 asyncio executor
- **选择**:`threading.Thread(target=_runner, args=(run_id, config), daemon=True).start()`
- **替代方案**:`asyncio.get_event_loop().run_in_executor(None, _runner, ...)`
- **理由**:进化是长跑(数十秒到几分钟),线程法无需持有 loop 引用即可启动;`run_in_executor` 需在 async context 调用,POST /start 还得 `await loop.run_in_executor` 多此一举。线程法更直观。daemon=True 让后端退出时线程自动结束。

### 决策 3:跨 loop 推事件用 `asyncio.run_coroutine_threadsafe`
- **选择**:`asyncio.run_coroutine_threadsafe(ws.send_json(event), self._loop)`
- **替代方案 A**:`loop.call_soon_threadsafe(queue.put_nowait, event)` + WS 协程 drain queue
- **替代方案 B**:在线程中直接 `ws.send_json(event)`(同步)
- **理由**:B 跨线程访问 WebSocket 会破坏 asyncio 单线程模型;A 需每个 WS 维护 queue + drain 协程,多 subscriber 复杂;`run_coroutine_threadsafe` 直接调度到主 loop,fire-and-forget,简单可靠。主 loop 在 startup 捕获保存。

### 决策 4:event_buffer cap 500,late subscriber 追进度
- **选择**:`run.event_buffer: list[dict]`,append 后若 `len > 500` 则 `event_buffer = event_buffer[-300:]`
- **替代方案**:不 buffer,late subscriber 只看新事件
- **理由**:用户刷新页面 / 重连 WS 时会丢历史进度;buffer 500 事件(约 50 代 atlas 或 50 周期 gepa)足够覆盖典型进化;cap 500 防内存无限增长。

### 决策 5:cancel_event 协作式停止,不强制 kill 线程
- **选择**:`threading.Event`,线程每代 / 每周期开头检查 `is_set()` → broadcast stopped + return
- **替代方案**:`thread.kill()`(Python 没有原生支持)/ `_thread.interrupt_main`
- **理由**:Python 无安全 kill 线程机制;协作式检查在每代开头,延迟最多 1 代(几秒)可接受;强制 kill 可能留下不一致状态(如 state 文件半写入)。

### 决策 6:run 完成后保留在映射表,不自动清理
- **选择**:run completed / failed / stopped 后仍可查询,`GET /runs/{id}` 返回最终状态
- **替代方案**:完成后立即从映射表移除
- **理由**:前端可能延迟请求详情 / WS 重连需重发 event_buffer;移除后 404 用户体验差;v0.1 单机内存增长可接受(每个 run 几 KB),用户手动重启清理。

### 决策 7:GEPA evaluate_fn 用 params keys 推断 strategy_cls
- **选择**:复用 `scripts/evolve_gepa.py:make_evaluate_fn` 模式,根据 params 中特征 key 推断 strategy_cls
- **替代方案 A**:每个 agent 绑定固定 strategy_cls,evaluate_fn 接收 agent_name
- **替代方案 B**:改 dex/reflection.py 让 evaluate_fn 接收 agent
- **理由**:不能改 dex 代码;A 需修改 evaluate_fn 签名,与 dex 接口不兼容;params keys 推断是 scripts/ 已验证模式,4 个 strategy 各有特征 key(grid_spacing_pct / rsi_low+rsi_high / trend_ma_period+adx_threshold / 默认)。

### 决策 8:WebSocket 完成后主动 close
- **选择**:run 状态变 completed/failed/stopped 后,WS 推送完最后事件 → close
- **替代方案**:保持 WS 开着,让客户端自己关
- **理由**:客户端需要明确知道「这次进化结束了,不用再等事件」;close 是清晰信号;客户端 close 后若有新 run 需重连。

## 风险 / 权衡

- **[macOS arm64 torch 装不上]** → `_run_atlas` / `_run_gepa` import dex.evolution 时会 ImportError → run 标 failed,WS 推 error 事件。这是预期行为,用户自备 Linux/WSL 环境。
- **[线程异常未捕获]** → runner 抛异常 → run.status="failed" + error 字段 + WS 推 error。缓解:`run_evolution_thread` 顶层 try/except 兜底,所有异常转 failed 状态。
- **[多 run 并发 CPU 争抢]** → 用户同时跑 5 个 atlas → CPU 满载。缓解:v0.1 不限制,文档提示「单机建议同时不超过 2 个」;v0.2 加并发限制。
- **[WS 断连重连丢事件]** → 客户端断连期间的事件在 event_buffer 中(cap 500),重连后 send_json buffer 全部。若超过 cap 500,中间事件丢失。缓解:v0.1 cap 500 足够;前端可调 `GET /runs/{id}` 拿最新 agents state 兜底。
- **[cancel_event 延迟]** → 用户 stop 后线程最多再跑 1 代才退出(几秒)。缓解:可接受,文档提示「停止是异步的,WS 收到 stopped 事件才算真正停」。
- **[asyncio.run_coroutine_threadsafe 主 loop 未设]** → startup 未触发或 _loop 为 None → broadcast 静默丢弃事件。缓解:startup 中显式 `evolution_manager.set_loop(asyncio.get_running_loop())`;manager 中 `if self._loop is None: return`。
- **[run_id 用 uuid4]** → 极小概率冲突。缓解:uuid4 122 位随机,实际无冲突风险。
- **[event_buffer 占内存]** → 每个 event 约 1KB,500 events 约 500KB/run,10 run 同时约 5MB。缓解:可接受,v0.1 不优化。

## 迁移计划

- 新增 `backend/app/schemas/evolve.py`:7 个 Pydantic 模型
- 新增 `backend/app/services/evolution_manager.py`:`EvolutionManager` + `EvolutionRun` + 单例
- 新增 `backend/app/services/evolution_runner.py`:`run_evolution_thread` + `_run_atlas` + `_run_gepa` + `make_evaluate_fn`
- 改造 `backend/app/api/v1/evolve.py`:4 个 REST 接口
- 改造 `backend/app/api/v1/ws.py`:新增 `/evolve/{run_id}` WebSocket
- 改造 `backend/app/main.py`:startup 捕获 loop
- 验证:启动后端 + curl start + websocat 连 WS 看事件 + curl stop
- 回滚:`git checkout backend/app/api/v1/evolve.py backend/app/api/v1/ws.py backend/app/main.py` + 删新增 schemas/services

## 待解决问题

- 是否需要 `POST /evolve/restart`?v0.1 不做,用户调 stop + start
- 是否持久化 run 到 SQLite?v0.1 不做,内存够用
- 是否限制并发 run 数?v0.1 不做,文档提示
- gepa_evolve_v2(含 edge guards / revival)是否实现?v0.1 用基础版,v0.2 升级
