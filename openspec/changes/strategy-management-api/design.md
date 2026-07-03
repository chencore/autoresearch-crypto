## 上下文

`dex/strategies/` 下 10 个策略类（`BaseStrategy` 子类），参数定义散落在各 `__init__`，无统一 schema、无 registry。`dex/strategies/__init__.py` 的 `__all__` 漏掉 `PureActionV2Strategy` 与 `MultiTFEnsembleStrategy`。前端 `strategy-management-ui` task 需要只读展示策略参数与说明。本 task 用反射 + 目录扫描暴露元数据，不动 `dex/` 代码。

## 目标 / 非目标

**目标：**
- `GET /api/v1/strategy` 返回 11 个策略的摘要列表
- `GET /api/v1/strategy/{name}` 返回某策略详情（含完整参数定义）
- 策略发现扫描目录，不依赖 `__all__`
- 参数反射用 `inspect.signature`，处理有/无 annotation 两种情况
- 标记 `GridStrategy` 的非标准信号类型
- 标记 `generate_signals` 的运行时参数
- `live_status` 占位返回 `"unknown"`

**非目标：**
- 不实现实盘进程状态查询（留给 `live-monitor-api` task）
- 不实现回测结果摘要（v0.1 回测不持久化，留给 v0.2）
- 不支持参数编辑（v0.1 只读，留给 v0.2）
- 不缓存策略元数据（11 个策略反射很快，<100ms；缓存留给后续优化）
- 不暴露策略的 `generate_signals` 实际调用（那是 `backtest-api` 的事）

## 决策

### 决策 1：扫描目录发现策略，而非依赖 `__all__` 或维护显式列表
- **选择**：`pkgutil.iter_modules(dex.strategies.__path__)` 遍历 `.py` 文件，`importlib.import_module` 导入每个模块，`inspect.getmembers` 找 `BaseStrategy` 子类
- **替代方案 A**：依赖 `dex/strategies/__init__.py` 的 `__all__`
- **替代方案 B**：在 `backend/app/services/strategy_registry.py` 维护显式列表
- **理由**：`__all__` 漏了 2 个策略；显式列表会随策略增减失同步。扫描目录是单一真相源，自动发现新增策略

### 决策 2：用 `inspect.signature` 反射 `__init__`，而非解析 AST
- **选择**：`inspect.signature(cls.__init__)` 拿 `Parameter` 对象，读 `name`/`annotation`/`default`
- **替代方案**：`ast.parse` 解析源码
- **理由**：`inspect` 是 Python 标准库，运行时反射准确；AST 解析需处理装饰器、继承、`super().__init__` 调用，复杂易错

### 决策 3：参数类型推断策略
- **选择**：
  1. 有 annotation → 用 `str(annotation)`（处理 `int`、`float | None`、`dict | None` 等）
  2. 无 annotation → 从默认值字面量推断（`int`/`float`/`bool`/`str`/`None`→`"null"`/`dict`/`list`）
  3. 无 annotation 且无默认值 → `type: "unknown"`
- **替代方案**：只信 annotation，无 annotation 标 `"unknown"`
- **理由**：3 个策略（scalp/pure_action/adaptive）无 annotation 但有默认值，推断后前端展示更友好

### 决策 4：默认值序列化用 `json.dumps` 兜底
- **选择**：先尝试 `json.dumps(default)`，失败则转 `str(default)`
- **替代方案**：只允许基本类型默认值
- **理由**：`MultiTFEnsembleStrategy.tf_params` 默认是嵌套 dict，`json.dumps` 能处理；`numpy` 类型（理论上不会有默认值）走 `str()` 兜底

### 决策 5：`signal_kind` 用硬编码映射，而非反射
- **选择**：在 `strategy_registry.py` 维护 `SIGNAL_KIND_OVERRIDE = {"GridStrategy": "position_target"}`，其余默认 `"discrete"`
- **替代方案**：解析 `generate_signals` 源码或 docstring 判断返回类型
- **理由**：只有 1 个例外，硬编码最简单；反射源码判断返回类型不可靠（无类型注解）

### 决策 6：`runtime_params` 用 `inspect.signature(generate_signals)` 反射
- **选择**：反射 `generate_signals` 签名，排除 `self` 与 `df`，剩余参数名放入 `runtime_params`
- **替代方案**：硬编码 `["enable_short"]`（4 个策略有此参数）
- **理由**：反射自动适应未来策略签名变化；硬编码易失同步

### 决策 7：策略元数据每次请求实时反射，不缓存
- **选择**：每次 `GET /api/v1/strategy` 都扫描目录 + 反射
- **替代方案**：启动时反射一次，缓存在模块级变量
- **理由**：11 个策略反射 < 100ms，单机个人使用无并发压力；缓存需考虑 dex 代码热更新（开发态可能改策略），实时反射最简单可靠。若后续性能不足再加 `lru_cache`

## 风险 / 权衡

- **[import dex.strategies 触发 numpy/pandas]** → backend 环境需补装交易核心依赖。缓解：本 task 实施时在 `backend/` 跑 `uv add numpy pandas`（最小子集，不装 torch）
- **[import 失败的模块]** → 某个策略模块 import 抛异常会导致整个列表 500。缓解：`strategy_registry` 用 try/except 包住每个模块的 import，失败的模块跳过并在日志记录
- **[策略类签名变化]** → dex 代码改 `__init__` 后 API 响应自动跟随，无需改 backend 代码。这是优势不是风险
- **[循环 import]** → `dex/strategies/__init__.py` 用了 lazy `__getattr__`，扫描时直接 `importlib.import_module("dex.strategies.trend")` 不触发 `__init__.py` 的 lazy 逻辑，无循环风险

## 迁移计划

- 新增 `backend/app/services/strategy_registry.py` + `backend/app/schemas/strategy.py`
- 改造 `backend/app/api/v1/strategy.py` 替换占位端点
- 在 `backend/` 目录 `uv add numpy pandas` 补装依赖
- 重启后端，curl 验证
- 回滚：`git checkout` 恢复 `strategy.py` 占位 + 删除新增文件

## 待解决问题

- `MultiTFEnsembleStrategy.tf_params` 默认值是嵌套 dict，序列化后前端如何友好展示？留给前端 task
- 是否需要 `GET /api/v1/strategy/{name}/params` 单独返回参数（便于前端表单）？v0.1 用详情接口即可，留给 v0.2
