## 新增需求

### 需求:启动进化接口

后端必须提供 `POST /api/v1/evolve/start` 接口,请求体含 `engine`("atlas" / "gepa")、`symbol`(如 "BTCUSDT")、`interval`(如 "5m")、`days`(int)、`generations`(int,1~100)、`evolution_interval`(int,可选,默认 5,仅 atlas 用)字段。后端必须用 `threading.Thread` 启动一个后台线程跑进化循环(atlas 调 `EvolutionEngine.evolve()` 每代循环,gepa 调 `ReflectionEngine.run_experiment()` 每周期循环),主线程立即返回 `{"run_id": "<uuid>", "status": "running"}`。已存在的 run 不影响新 run 启动(v0.1 允许多 run 并存,但单机 CPU 受限用户应自行节制)。`engine` 字段非 atlas/gepa 必须返回 400 + `{"error": {"code": "invalid_engine", "message": "engine must be 'atlas' or 'gepa', got: <x>"}}`。数据文件不存在必须返回 404 + `{"error": {"code": "data_not_found", "message": "data file not found: <file>"}}`。

#### 场景:启动 atlas
- **当** POST `{"engine":"atlas", "symbol":"ETHUSDT", "interval":"5m", "days":"7", "generations":10}`
- **那么** 后端创建 `EvolutionRun(id=uuid, config={...}, status="running")` + 启动线程,立即返回 `{"run_id":"<uuid>", "status":"running"}`

#### 场景:启动 gepa
- **当** POST `{"engine":"gepa", "symbol":"ETHUSDT", "interval":"5m", "days":"7", "generations":15}`
- **那么** 后端创建 run + 启动线程,返回 `{"run_id":"<uuid>", "status":"running"}`

#### 场景:不支持的引擎
- **当** POST `{"engine":"random", ...}`
- **那么** 返回 400 + `{"error":{"code":"invalid_engine","message":"engine must be 'atlas' or 'gepa', got: random"}}`

#### 场景:数据文件不存在
- **当** POST `{"symbol":"DOGEUSDT", "interval":"5m", "days":"7", ...}` 但 `data/crypto/DOGEUSDT_5m_7d.parquet` 不存在
- **那么** 返回 404 + `{"error":{"code":"data_not_found","message":"data file not found: DOGEUSDT_5m_7d.parquet"}}`

### 需求:运行列表接口

后端必须提供 `GET /api/v1/evolve/runs` 接口,返回所有 run 的摘要列表,按 `started_at` 降序。每个 run 摘要含 `id` / `engine` / `symbol` / `status`("running" / "completed" / "failed" / "stopping" / "stopped")/ `current_gen` / `total_generations` / `started_at` / `completed_at` / `error`(失败时的错误信息,否则 null)字段。无 run 时返回 `{"runs": [], "total": 0}`。

#### 场景:列出多个 run
- **当** 已启动 2 个 run(atlas running / gepa completed)
- **那么** `GET /runs` 返回 `{"runs": [{id, engine:"gepa", status:"completed", ...}, {id, engine:"atlas", status:"running", ...}], "total": 2}`(降序)

### 需求:运行详情接口

后端必须提供 `GET /api/v1/evolve/runs/{run_id}` 接口,返回 run 详情。响应含 `id` / `engine` / `symbol` / `interval` / `days` / `generations` / `status` / `current_gen` / `started_at` / `completed_at` / `error` / `agents`(最新 agents state 列表)/ `event_count`(event_buffer 长度)字段。run_id 不存在必须返回 404 + `{"error":{"code":"not_found","message":"run not found: <id>"}}`。`agents` 字段为空数组表示尚未完成第一代。

#### 场景:运行中详情
- **当** atlas run 运行中已到第 3 代,GET `/runs/<id>`
- **那么** 返回 `{"id":"<id>", "engine":"atlas", "status":"running", "current_gen":3, "generations":10, "agents":[{name:"Alpha", style:"Trend", score:0.42, weight:0.31, ...}, ...], "event_count":6, ...}`

#### 场景:run 不存在
- **当** GET `/runs/invalid-uuid`
- **那么** 返回 404 + `{"error":{"code":"not_found","message":"run not found: invalid-uuid"}}`

### 需求:停止运行接口

后端必须提供 `POST /api/v1/evolve/stop` 接口,请求体含 `run_id`。后端必须设置该 run 的 `cancel_event`(线程下次循环检查后退出),立即返回 `{"run_id":"<id>", "status":"stopping"}`。run_id 不存在返回 404 + `{"error":{"code":"not_found","message":"run not found: <id>"}}`。run 已完成 / 已停止时调用 stop 返回 409 + `{"error":{"code":"already_finished","message":"run <id> is already <status>"}}`。

#### 场景:正常停止
- **当** atlas run 运行中,POST `{"run_id":"<id>"}`
- **那么** 设置 cancel_event,返回 `{"run_id":"<id>", "status":"stopping"}`;线程下次循环检查到 cancel_event → broadcast `{type:"stopped", generation:<最后代>}` → status 变 "stopped"

#### 场景:停止已完成 run
- **当** run 状态为 completed,POST `{"run_id":"<id>"}`
- **那么** 返回 409 + `{"error":{"code":"already_finished","message":"run <id> is already completed"}}`

### 需求:WebSocket 进度推送

后端必须提供 `WebSocket /api/v1/ws/evolve/{run_id}` 接口。客户端连接后,后端必须:
1. 校验 run_id 存在(不存在则 send `{type:"error", code:"not_found", message:"..."}` 后 close)
2. send_json 所有 `event_buffer` 中的历史事件(让 late subscriber 追上进度)
3. 注册 ws 到 `run.subscribers`,之后新事件实时推送
4. 保持连接直到客户端断开 或 run 状态变 completed/failed/stopped(此时推送完最后事件后 close)

事件类型必须包括:
- `{type:"started", run_id, config, total}` — 进化启动
- `{type:"generation", run_id, generation, total, agents:[{name, style, score, weight, params, generation}]}` — atlas 每代(atlas only)
- `{type:"cycle", run_id, cycle, total, agent, hypothesis, score_before, score_after, accepted:bool, reflection}` — gepa 每周期(gepa only)
- `{type:"meta_reflection", run_id, cycle, summary}` — gepa 每 5 周期(gepa only)
- `{type:"completed", run_id, ...}` — 进化正常完成(atlas 含 best_agent + final_weights;gepa 含 experiment_count + meta_count + blind_spots)
- `{type:"stopped", run_id, generation|cycle}` — 用户主动停止
- `{type:"error", run_id, code, message}` — 进化异常失败

客户端可发 `{action:"stop"}` 消息请求停止(等效于 POST /stop)。

#### 场景:订阅运行中 run
- **当** atlas run 已到第 3 代,客户端连 WS `/ws/evolve/<id>`
- **那么** 后端 send 3 个历史 generation 事件 + 1 个 started 事件(顺序按发生时间),之后实时推送第 4 代及之后事件

#### 场景:run 不存在
- **当** 客户端连 WS `/ws/evolve/invalid-uuid`
- **那么** 后端 accept + send `{type:"error", code:"not_found", message:"run not found: invalid-uuid"}` + close

#### 场景:run 完成后订阅
- **当** run 已 completed,客户端连 WS
- **那么** 后端 send 所有 event_buffer 事件(含 completed)+ close

#### 场景:客户端发 stop
- **当** 客户端在 WS 上发 `{action:"stop"}`
- **那么** 后端调 `manager.request_stop(run_id)`,与 POST /stop 等效

### 需求:运行生命周期与线程安全

后端必须在内存中维护 `run_id → EvolutionRun` 映射(进程管理器单例)。映射表必须线程安全(`threading.Lock`)。每个 run 必须在独立线程中跑进化循环,主线程不阻塞。`cancel_event`(threading.Event)必须在线程每次循环迭代开始检查,设置后线程必须在当前代 / 周期完成后退出(不强制中断)。run 完成后必须保留在映射表中(供查询历史 / WS 重连),不自动清理。后端重启时所有 run 丢失(v0.1 接受,不持久化)。

事件推送必须用 `asyncio.run_coroutine_threadsafe(ws.send_json(event), loop)` 调度到主事件 loop,禁止在线程中直接调 ws.send_json(会跨线程冲突)。主 loop 必须在 FastAPI startup 时捕获并保存到 `evolution_manager._loop`。

#### 场景:线程中推送事件
- **当** atlas 线程完成第 5 代,需推送 generation 事件
- **那么** 调 `manager.broadcast(run_id, event)` → 内部 `asyncio.run_coroutine_threadsafe(ws.send_json(event), main_loop)` 调度到主 loop → 主 loop 异步发送给所有订阅者

#### 场景:cancel_event 检查
- **当** 用户 POST /stop,cancel_event 被设置
- **那么** 线程下次循环 `for gen in range(...)` 开始处 `if cancel_event.is_set(): broadcast stopped + return`,当前代已完成的不丢失

#### 场景:后端重启
- **当** 后端重启,原本 running 的 run 线程随进程消失
- **那么** `GET /runs` 返回空列表(v0.1 接受,不持久化)
