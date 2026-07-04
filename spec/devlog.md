# Development Log

> **维护规则**：每次 PR 合并后，由 AI 自动追加一条记录。
>
> 每条记录应包含：日期、变更名、摘要、关键决策/坑点。

---

## Entry Template

```markdown
### YYYY-MM-DD · <change-name>

**摘要**：一句话说清楚这次变更做了什么。

**关键决策**：
- 决策点 1 — 选了什么、放弃了什么、为什么
- 决策点 2 — ...

**踩坑 / 经验**：
- 坑点描述 + 如何解决（可选）

**相关产出**：
- 归档位置：`openspec/changes/archive/<change-name>/`
- PR：#xxx（如适用）
```

---

## Log Entries

<!-- 最新条目在最上面 -->

### 2026-07-04 · evolution-api

**摘要**：实现后端 ATLAS / GEPA 进化引擎 + WebSocket 推送（R-v0.1-ck-6），新增 `services/evolution_manager.py`（线程安全 run 映射单例）+ `services/evolution_runner.py`（复刻进化循环 + 跨 loop 推事件）+ `schemas/evolve.py`（8 个 Pydantic 模型），改造 `api/v1/evolve.py`（4 个 REST 接口）+ `api/v1/ws.py`（/evolve/{run_id} WebSocket）+ `main.py`（startup 捕获主 loop）。父分支：`version/v0.1`。

**关键决策**：
- 复刻进化循环不调顶层 `run_evolution` / `gepa_evolve`——monolithic 函数无法插入 broadcast hook；直接 for 循环调 `engine.evolve()` / `engine.run_experiment()` 完全控制事件推送时机
- 进化跑在 `threading.Thread(daemon=True)` 不用 asyncio executor——长跑任务线程法无需持有 loop 引用即可启动，更直观
- 跨 loop 推事件用 `asyncio.run_coroutine_threadsafe(ws.send_json(event), self._loop)`——线程不能直接调 ws.send_json（破坏 asyncio 单线程模型）；run_coroutine_threadsafe fire-and-forget 调度到主 loop
- event_buffer cap 500 让 late subscriber 追进度——用户刷新页面 / 重连 WS 时收到历史事件；cap 500 防内存无限增长
- cancel_event 协作式停止——Python 无安全 kill 线程机制；每代 / 每周期开头检查 `is_set()`，延迟最多 1 代可接受
- GEPA evaluate_fn 用 params keys 推断 strategy_cls（复用 scripts/evolve_gepa.py 模式）——不能改 dex 代码；4 个 strategy 各有特征 key
- run 完成后保留在映射表不自动清理——前端延迟请求 / WS 重连需重发 event_buffer；移除后 404 体验差
- WebSocket 完成后主动 close——客户端明确知道「进化结束不用再等事件」

**踩坑 / 经验**：
- ATLAS / GEPA 实际可在 backend uv 环境跑通（不像 live_*.py 需要 torch）——evolution.py 只 import numpy/pandas/strategies，无 torch 依赖；ETHUSDT_5m_7d.parquet 2016 bars 上 atlas 3 代 ~130ms、gepa 6 cycle ~1s
- 7d 5m 数据（2016 bars）atlas 30 代 < 1s 跑完无法测停止——临时生成 30d 5m（8640 bars）数据集，gepa 100 cycle 跑到 cycle 38 时 stop 生效，WS 收到 stopped 事件 cycle=38 验证通过
- WebSocket 连接已完成的 run 时，send_json event_buffer 后立即 close——客户端 websockets 库抛 ConnectionClosedOK，需 try/except 捕获正常退出
- `asyncio.wait_for(websocket.receive_json(), timeout=1.0)` 让 WS 协程每秒检查 run.status 是否变完成态——避免阻塞 forever 接收导致 run 完成后 WS 不关
- `request_stop` 返回 `None` / `"already_finished"` / `"not_found"` 三态——区分 not_found（404）和 already_finished（409）让前端能给用户更精确的反馈

**未完成验证**：
- 大规模进化（100+ 代 / 1000+ cycle）的性能未压测，v0.1 单机个人用不追求
- 多 subscriber 并发推送未压测，v0.1 单浏览器标签页场景

**相关产出**：
- 归档位置：`openspec/changes/archive/2026-07-04-evolution-api/`
- 主规范：`openspec/specs/evolution-api/spec.md`（首次创建）
- 项目级 task 勾选：`spec/tasks.md` evolution-api ✅
- 父分支：`version/v0.1`

---

### 2026-07-04 · live-monitor-ui

**摘要**：实现前端实盘监控页（R-v0.1-ck-5），`Live.vue` 占位页重写为顶部 3 交易所卡片网格 + 启停 Modal + 中部状态区（NDescriptions 透传 state dict）+ 底部日志区（`<pre>` 末尾 200 行 2s 轮询），新增 `api/live.ts` 封装 5 个接口与 8 个 TS 类型。父分支：`version/v0.1`。

**关键决策**：
- 卡片网格用 NGrid `cols=3` + `responsive="screen"`，宽屏一行排开 / 窄屏堆叠，naive-ui 自带无需手写 CSS
- 启动表单用 NModal 居中弹窗（4 字段小表单）不用 NDrawer——Drawer 适合长表单，内联展开破坏网格美观
- 状态区 `v-for` 遍历 `Object.entries(state)` 渲染 NDescriptionsItem——3 个脚本 state 字段不一致，透传避免前端跟脚本字段变化同步
- 日志区用 `<pre>` 不用 NCode——NCode 行号 + 高亮渲染 200 行开销大，`<pre>` 等宽字体足够，max-height 400px 滚动
- 轮询用 `setInterval(fetchExchanges, 2000)` + `setInterval(fetchDetail, 2000)`，`onUnmounted` 清理——axios 30s 超时远小于 2s 不会堆积
- 轮询竞态用 `pollSeq` 序号法——每次发起请求前 `++pollSeq`，响应回来比对才更新；AbortController 需管理生命周期 + CanceledError 处理，序号法 3 行代码搞定
- 状态/日志区合并 `Promise.allSettled` 拉取——都 2s 间隔 + 都依赖 selectedExchange，合并简化生命周期，allSettled 让一个失败不影响另一个
- 轮询失败 `console.warn` 不弹 message——2s × 失败 = 每 2s 弹一次刷屏；手动操作（启动/停止）失败才弹 message
- 错误分级：`already_running` / `not_running` → warning（业务预期），`network` / 其他 → error

**踩坑 / 经验**：
- macOS arm64 上 start 返 200 + PID 后子进程立刻退出（torch 缺失），2s 后卡片变 stopped——这是预期行为，success message 已显示「已启动」，用户从状态变化理解失败
- vite proxy 已配 `/api → 127.0.0.1:8000`，前端通过 5173 端口直接访问后端 API，无需 CORS
- typecheck 一次通过（vue-tsc --noEmit），naive-ui 组件类型完整
- NCard `@click` 与按钮 `@click.stop` 配合——卡片点击选中交易所，按钮区 `@click.stop` 阻止冒泡避免触发选中

**未完成验证**：
- 视觉渲染（卡片网格、Modal 表单、状态/日志区、选中高亮）需手动浏览器目视，AI 环境无浏览器
- typecheck + dev server 启动 + vite proxy 联通后端 + start 流程通过 proxy 全绿

**相关产出**：
- 归档位置：`openspec/changes/archive/2026-07-04-live-monitor-ui/`
- 主规范：`openspec/specs/live-monitor-ui/spec.md`（首次创建）
- 项目级 task 勾选：`spec/tasks.md` live-monitor-ui ✅
- 父分支：`version/v0.1`

---

### 2026-07-04 · live-monitor-api

**摘要**：实现后端实盘进程管理 5 个接口（R-v0.1-ck-5），`api/v1/live.py` 占位重写为 exchanges / start / stop / status / logs，新增 `services/live_manager.py`（线程安全进程映射单例）+ `services/live_state_reader.py`（读 state JSON + log txt，nado 取最新日志文件）+ `schemas/live.py`（8 个 Pydantic 模型）。父分支：`version/v0.1`。

**关键决策**：
- 进程映射用内存单例 + `threading.Lock`，不持久化 PID——v0.1 单机个人用，后端重启孤儿进程留给用户 `pkill` 清理，持久化回捞需处理 PID 复用竞态太复杂
- 日志用 HTTP 轮询（`?tail=200`，Query `ge=1, le=1000`）不用 WebSocket——脚本日志 append 模式无流式概念，2s 轮询单机开销可忽略，WebSocket 留给 evolution-api
- state 字段 `dict[str, Any]` 透传不裁剪——3 个脚本 state 字段不一致，透传避免后端跟脚本字段变化同步
- nado 日志 `glob("live_nado_log_*.txt")` 按文件名降序取最新——nado 每次启动新日志文件，文件名时间戳字典序即时间序
- start 用 `subprocess.Popen(["uv", "run", "python", script, ...], cwd=PROJECT_DIR, stdout=DEVNULL, stderr=DEVNULL)` fire-and-forget——脚本依赖根 uv 环境（torch/ccxt），`uv run` 自动激活；不等脚本输出，启动失败通过 `/status` + `/logs` 轮询发现
- stop 用 `terminate() → wait(5)` 超时 `kill()` 兜底——SIGTERM 让脚本 graceful shutdown 保存 state，5s 足够清理
- `_cleanup_if_dead` 在每次 is_running/get_pid/list_exchanges/stop 调用前检查 `proc.poll()`，子进程退出立即清理映射

**踩坑 / 经验**：
- macOS arm64 上 `torch==2.6.0+cu124` 无 wheel，`uv run python live_binance_quant.py` 子进程会立刻退出——API 设计上 start 返 200 + PID（Popen 成功），前端通过 `/exchanges` 看到 stopped + `/logs` 看到 ImportError，这是预期行为
- nado CLI 用 `--ticker` 不是 `--symbol`，v0.1 统一用 `--symbol` 让 nado 启动报错——已知限制，留给 v0.2 按 exchange 适配 CLI 参数
- 11 项 curl 验证全绿：5.4 start binance 返 PID 21083，5.5 等 2s 后 exchanges 显示 stopped（子进程退出），5.6 stop not_running 404，5.7 fake state 文件 → status 返 state + updated_at，5.8 fake log 500 行 → tail=10 返末尾 10 行 + total_lines=500，5.10 tail=5000 被 Query le=1000 拦截返 422

**未完成验证**：
- 真实 live_*.py 启动需 Linux/WSL 环境（macOS arm64 torch 装不上），本 task 仅验证 API 行为正确（start 返 200、子进程退出后 status 显示 stopped、logs 能读）
- nado 实盘启动会报错（CLI 参数不匹配），v0.2 修复

**相关产出**：
- 归档位置：`openspec/changes/archive/2026-07-04-live-monitor-api/`
- 主规范：`openspec/specs/live-monitor-api/spec.md`（首次创建）
- 项目级 task 勾选：`spec/tasks.md` live-monitor-api ✅
- 父分支：`version/v0.1`

---

### 2026-07-04 · backtest-ui

**摘要**：实现前端回测可视化页（R-v0.1-ck-4），`Backtest.vue` 占位页重写为左侧表单 + 右侧结果区，引入 echarts 画收益曲线，新增 `api/backtest.ts` 封装两个接口与 TS 类型。父分支：`version/v0.1`。

**关键决策**：
- 图表库选 echarts + vue-echarts（按需引入 LineChart / GridComponent / TooltipComponent）——naive-ui 无图表组件，echarts 中文社区最流行，bundle 增量 ~300KB 单机可接受
- 交易对 select value 用 `${symbol}|${interval}|${days}` 拼接字符串，submit 时 split——比 `value-key` 简单，比三个独立 select 节省空间
- 指标卡片用 NStatistic 不用 NCard——NStatistic 专为数值展示设计，自带 tabular-nums 字体
- 收益曲线 x 轴用 timestamp（int ms），echarts `type='time'` 自动渲染日期轴——比 category 模式省心
- 交易明细表一次性渲染不分页——v0.1 单回测约 200~400 条 trades 可接受
- 错误时结果区恢复 NEmpty 不保留上次结果——避免显示陈旧数据
- 表单与结果区左右分栏（左 320px / 右 flex 1）——1280px 宽屏省垂直空间

**踩坑 / 经验**：
- `pnpm dev` 必须从 frontend 目录跑，从 backend 跑会触发 `ERR_PNPM_NO_PKG_MANIFEST`
- echarts 6.x + vue-echarts 8.x 配合 Vue 3.5 + TypeScript 5.6 typecheck 一次通过，无类型缺失
- NDatePicker range 返回 `[start_ts, end_ts]` ms int，转 ISO 用 `new Date(ts).toISOString()` 显式 UTC，避免时区偏移
- 复用 `strategy-management-ui` 的 `fetchStrategyList` 直接拉策略列表，无需新建

**未完成验证**：
- 视觉渲染（指标卡片染色、收益曲线图、交易明细表染色）需手动浏览器目视，AI 环境无浏览器
- API 路径、Vue 模块加载、echarts 依赖、typecheck 全绿

**相关产出**：
- 归档位置：`openspec/changes/archive/2026-07-04-backtest-ui/`
- 主规范：`openspec/specs/backtest-ui/spec.md`（首次创建）
- 项目级 task 勾选：`spec/tasks.md` backtest-ui ✅
- 父分支：`version/v0.1`

---

### 2026-07-04 · backtest-api

**摘要**：实现回测执行接口（R-v0.1-ck-4），`GET /api/v1/backtest/symbols` 扫描 `data/crypto/` 列交易对，`POST /api/v1/backtest/run` 跑完整回测流程返回 equity_curve / trades / metrics / meta。父分支：`version/v0.1`。

**关键决策**：
- 文件名解析用正则 `{SYMBOL}_{INTERVAL}_{DAYS}d.parquet`，不读 parquet 元数据——文件名是单一真相源
- 回测流程封装在 `services/backtest_runner.py`，router 只做 HTTP 适配——evolution-api 后续可直接 import service 不走 HTTP
- trades 反查 `df.timestamp` / `df.close` 填充 `timestamp` / `price` 在 service 层做，不动 dex——v0.1 铁律「在 dex 之上加 Web，不污染交易核心」
- equity_curve 返回 `{step, timestamp, equity}` 点对象数组，不返回三个并行数组——前端 v-for 直接渲染
- numpy 类型用 `float()` / `int()` 显式转，不用 `.tolist()` 兜底——避免 dict 被转成 list 的坑
- 错误响应用 `JSONResponse` 直接返回 `{error:{code,message}}`，绕过全局异常处理器——不依赖处理器实现细节
- evaluator 用 `dex.config` 默认 `INITIAL_CAPITAL=10000` / `COMMISSION=0.0002` / `SLIPPAGE=0.0002`，v0.1 不暴露给前端

**踩坑 / 经验**：
- `spec/requirements.md` R-v0.1-ck-4 写「调 `BaseStrategy.simulate`」是笔误——实际 `simulate` 在 `StrategyEvaluator` 上，`BaseStrategy` 只有 `generate_signals`。本 task 按实际 API 实现，项目级 spec 仅人工修改不动
- `pd.DatetimeIndex.astype('int64')` 在 pandas 2.x 行为不确定（可能返回 ns / s），用 `timestamps.values.astype('datetime64[ns]').view('int64')` 显式拿 ns 再 `// 10**6` 转 ms
- 根环境 `prepare_crypto.py` 装不上（torch==2.6.0+cu124 无 macOS arm64 wheel），用 backend 环境生成 synthetic parquet 兜底
- `StrategyEvaluator.simulate` 返回的 trades 只含 `step` 索引，无 `timestamp`/`price`，必须 service 层反查 `df` 填充

**验证数据**：
- synthetic `ETHUSDT_5m_7d.parquet`（2016 bar，2026-06-25 ~ 2026-07-02，列含 timestamp/datetime/open/high/low/close/volume）
- 用户实盘验证需自行 `uv run python prepare_crypto.py` 下载真实数据（根环境需先解决 torch 依赖）

**相关产出**：
- 归档位置：`openspec/changes/archive/2026-07-04-backtest-api/`
- 主规范：`openspec/specs/backtest-api/spec.md`（首次创建）
- 项目级 task 勾选：`spec/tasks.md` backtest-api ✅
- 父分支：`version/v0.1`

---

### 2026-07-04 · strategy-management-ui

**摘要**：实现前端策略管理只读页（R-v0.1-ck-3），`Strategies.vue` 占位页重写为 NDataTable 列表 + NDrawer 详情抽屉，新增 `api/strategy.ts` 封装两个接口与 TypeScript 类型。父分支：`version/v0.1`。

**关键决策**：
- API 类型与 fetch 函数同放 `api/strategy.ts`，不另起 `types/` 目录——v0.1 接口少，手写管理最简单；自动生成留待 v0.2
- 列表用 NDataTable 而非手写 table——自带 loading / 空状态 / 列对齐，与详情抽屉的参数表风格统一
- 详情用 NDrawer 而非独立路由 / NModal——保留列表上下文，符合管理台交互习惯
- 抽屉内四段（NDescriptions / pre docstring / NDataTable 参数表 / NSpace tag 区）不套 NCard，避免多层 padding 浪费空间
- 每次打开抽屉重新拉详情，不缓存——开发态可能改 dex 代码后重启后端，缓存会显示旧数据
- 错误用 NMessage 顶部一闪，404 在抽屉内用 NEmpty 局部显示

**踩坑 / 经验**：
- `useMessage()` 必须在 `<NMessageProvider>` 内调用，setup-frontend-scaffold 的 App.vue 未包，本 task 补上
- 关闭防异步用 `currentName.value !== name` 检查，比 `cancelled` 标志位更直接（关闭 / 切换都会改 currentName）
- `JSON.stringify(row.default, null, 2)` 让嵌套 dict 默认值可读，配合 `<pre>` 保留换行
- NDataTable 的 `ellipsis: { tooltip: true }` 处理 module 列长字符串

**未完成验证**：
- tasks.md 5.9 切换抽屉不残留：需手动浏览器目视，AI 环境无浏览器；代码层用 `currentName` 守卫保证

**相关产出**：
- 归档位置：`openspec/changes/archive/2026-07-04-strategy-management-ui/`
- 主规范：`openspec/specs/strategy-management-ui/spec.md`（首次创建）
- 项目级 task 勾选：`spec/tasks.md` strategy-management-ui ✅
- 父分支：`version/v0.1`

---

### 2026-07-04 · strategy-management-api

**摘要**：实现策略管理只读接口，`GET /api/v1/strategy` 列表 + `GET /api/v1/strategy/{name}` 详情，用 `pkgutil` 扫描 `dex/strategies/` 目录 + `inspect.signature` 反射 `__init__` 与 `generate_signals`，发现 10 个 `BaseStrategy` 子类。父分支：`version/v0.1`。

**关键决策**：
- 扫描目录发现策略而非依赖 `__all__`——`dex/strategies/__init__.py` 的 `__all__` 漏了 `PureActionV2Strategy` 与 `MultiTFEnsembleStrategy`，扫描目录是单一真相源
- 参数反射用 `inspect.signature` 而非 AST 解析——运行时反射准确，无需处理装饰器/继承/`super()` 调用
- 类型推断三级兜底：有 annotation 用 annotation → 无 annotation 从默认值字面量推断 → 无默认值标 `"unknown"`
- 默认值序列化用 `json.loads(json.dumps(default))` 兜底——`MultiTFEnsembleStrategy.tf_params` 嵌套 dict 可处理，失败转 `str()`
- `signal_kind` 硬编码映射（仅 `GridStrategy` → `"position_target"`）——只有 1 个例外，反射源码判断不可靠
- `runtime_params` 反射 `generate_signals` 签名排除 `self`/`df`——自动适应未来签名变化
- 策略元数据每次请求实时反射，不缓存——10 个策略反射 < 100ms，单机无并发压力

**踩坑 / 经验**：
- `bool` 必须在 `int` 前判断（`isinstance(True, int)` 为 True），否则 bool 参数被标成 int
- `inspect.getmembers` 会拿到导入的基类，需用 `obj.__module__ != module.__name__` 过滤
- 探索阶段误报"11 个策略"，实际是 10 个（`grep "^class .*Strategy"` 含 `BaseStrategy` 抽象类与 `StrategyEvaluator` 非策略类）——已修正 `spec/requirements.md` 三处
- 根 `.gitignore` 的 `uv.lock` 匹配任意层级，改为 `/uv.lock` 只忽略根，让 `backend/uv.lock` 可提交

**相关产出**：
- 归档位置：`openspec/changes/archive/2026-07-04-strategy-management-api/`
- 主规范：`openspec/specs/strategy-management/spec.md`（首次创建）
- 项目级 task 勾选：`spec/tasks.md` strategy-management-api ✅
- 父分支：`version/v0.1`

---

### 2026-07-04 · setup-frontend-scaffold

**摘要**：初始化 Vue 3 + Vite + TypeScript + Naive UI 前端骨架，新增 `frontend/` 顶层目录，承载 app 实例、路由、Pinia、Naive UI、axios client、全局布局、四个业务页面占位。父分支：`version/v0.1`。

**关键决策**：
- 手起 `package.json` 而非 `create-vue` CLI，只放本 task 需要的最小集，与 backend 骨架对称
- Vite proxy 单条 `/api` 配置同时支持 HTTP + WebSocket（`ws: true`），`/health` 单独配
- 路由用 `createWebHistory()` 而非 hash，开发态 Vite 自动 fallback
- Naive UI 全局注册（`app.use(naive)`），单机应用 bundle 体积不敏感
- axios baseURL `/api/v1`，`/health` 独立 fetch（后端 health 在根路径非 /api/v1）

**踩坑 / 经验**：
- pnpm 11 不再读 `package.json` 的 `pnpm.onlyBuiltDependencies`，改用 `pnpm-workspace.yaml` 的 `allowBuilds` 字段（pnpm install 时自动生成模板）
- `tsconfig.node.json` 用 `composite: true` 时不能 `noEmit: true`（TS6310），简化为单 tsconfig 包含 vite.config.ts
- vite.config.ts 用 `node:url` 需要 `@types/node`，否则 typecheck 报 TS2307
- Vite 5 默认监听 IPv6（`localhost:5173` → `::1`），`curl 127.0.0.1` 走 IPv4 连不上，用 `localhost` 即可

**相关产出**：
- 归档位置：`openspec/changes/archive/2026-07-04-setup-frontend-scaffold/`
- 主规范：`openspec/specs/frontend-scaffold/spec.md`（首次创建）
- 项目级 task 勾选：`spec/tasks.md` setup-frontend-scaffold ✅
- 父分支：`version/v0.1`

---

### 2026-07-04 · setup-backend-scaffold

**摘要**：初始化 FastAPI 后端骨架，新增 `backend/` 顶层目录，承载 app 实例、配置加载、SQLite 连接、CORS、统一错误处理、五个业务 router 占位与 `/health` 接口。父分支：`version/v0.1`。

**关键决策**：
- 后端独立 `pyproject.toml`（uv 环境），与根交易核心环境隔离 — 决策提升至 `spec/design.md` 决策 6
- backend 引用根 `dex/` 包用 `sys.path.insert`（path 依赖因根项目 name 含 `-` 不可用）— 决策提升至 `spec/design.md` 决策 7
- 完整 `from dex.strategies.base import` 触发 numpy/torch 重依赖，本 task 只验证 `from dex.config import` 路径可达，完整 import 留给后续业务 task 补装交易核心依赖

**踩坑 / 经验**：
- 路由用 `@router.get("/")` 会触发 FastAPI 307 重定向到带尾斜杠版本，改为 `@router.get("")` 直接命中
- 异常处理器注册 `fastapi.HTTPException` 不捕获 Starlette 路由器抛的 404，改注册 `starlette.exceptions.HTTPException` 才统一错误格式
- `pydantic-settings` 的 `list[str]` 字段从 `.env` 读取时需用 JSON 数组格式（`CORS_ORIGINS=["http://localhost:5173"]`）

**相关产出**：
- 归档位置：`openspec/changes/archive/2026-07-04-setup-backend-scaffold/`
- 主规范：`openspec/specs/backend-scaffold/spec.md`（首次创建）
- 项目级 task 勾选：`spec/tasks.md` setup-backend-scaffold ✅
- 父分支：`version/v0.1`

---

### 2026-07-03 · v0.1-kickoff

**摘要**：版本 v0.1 kickoff，确立 v0.1 为单机本地量化工作台，在现有 `dex/` 交易核心之上新增 FastAPI + Vue 3 Web 界面。

**关键决策**：
- v0.1 范围聚焦四个 MVP 模块：策略管理（只读）、回测可视化、实盘监控面板、参数调优 / 进化
- 技术栈：FastAPI + SQLite + Vue 3/Vite/Naive UI + WebSocket
- 实盘用 subprocess 拉起 `live_*.py`，不动现有交易核心
- 策略参数 v0.1 只读，编辑留给 v0.2
- 回测结果 v0.1 不持久化，关闭即丢
- 明确非目标：多用户 / 鉴权 / SaaS / 移动端 / 自定义数据上传

**相关产出**：
- `spec/requirements.md`：新增 R-v0.1-ck-1 ~ R-v0.1-ck-9
- `spec/tasks.md`：新增 `## 版本 v0.1`，拆分 11 个 tasks
- `spec/design.md`：填入技术栈、系统架构、模块划分、数据模型、5 项关键决策

---

### 2026-04-16 · bootstrap-speccoding-template

**摘要**：从 SpecCoding Template 初始化项目骨架。

**关键决策**：
- 采用「两级 Spec 体系」：`spec/` 管全局、`openspec/` 管单次变更
- 开发工作流固化为七阶段：git branch → scaffold → brainstorm → plan → execute → archive → merge

**相关产出**：
- 项目级 spec 文档骨架（requirements / design / tasks / devlog / structure）
- OpenSpec 配置 + 示例归档变更 `example-add-user-auth`
