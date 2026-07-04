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
