## 新增需求

### 需求:策略列表接口

后端必须提供 `GET /api/v1/strategy` 接口，返回 `dex/strategies/` 下所有 `BaseStrategy` 子类的摘要列表。响应必须包含 `strategies` 数组与 `total` 字段。每个摘要必须包含类名、模块路径、文件名、一句话描述、参数数量。列表顺序必须稳定（按类名字母序）。

#### 场景:获取策略列表
- **当** 客户端发起 `GET /api/v1/strategy`
- **那么** 返回 HTTP 200，响应体为 `{"strategies": [{"name": "...", "module": "...", "file": "...", "description": "...", "params_count": N, "live_status": "unknown"}, ...], "total": 11}`

#### 场景:列表包含所有 10 个策略
- **当** 后端扫描 `dex/strategies/` 完成
- **那么** 返回的 `total` 必须为 10，包含 `PureActionV2Strategy` 与 `MultiTFEnsembleStrategy`（这两个未在 `dex/strategies/__init__.py` 的 `__all__` 中，但必须被发现）

### 需求:策略详情接口

后端必须提供 `GET /api/v1/strategy/{name}` 接口，返回指定策略的完整元数据。响应必须包含类名、模块、文件、描述、完整 docstring、参数定义数组、信号类型标记、运行时参数列表、`live_status` 占位。参数定义必须包含名称、类型字符串、默认值（JSON 可序列化）、原始 annotation 字符串。

#### 场景:获取存在的策略详情
- **当** 客户端发起 `GET /api/v1/strategy/HybridMeanRevMomentumStrategy`
- **那么** 返回 HTTP 200，响应体含 `"name": "HybridMeanRevMomentumStrategy"`、`"params"` 数组含 10 个参数（rsi_period 等）、`"signal_kind": "discrete"`、`"runtime_params": ["enable_short"]`、`"live_status": "unknown"`

#### 场景:获取不存在的策略
- **当** 客户端发起 `GET /api/v1/strategy/NonExistent`
- **那么** 返回 HTTP 404，响应体为 `{"error": {"code": "not_found", "message": "strategy not found: NonExistent"}}`

### 需求:策略发现机制

后端必须通过扫描 `dex/strategies/` 目录下的 `.py` 文件发现策略类，禁止依赖 `dex/strategies/__init__.py` 的 `__all__`（因为它漏掉了 `PureActionV2Strategy` 与 `MultiTFEnsembleStrategy`）。每个模块必须用 `importlib` 导入，遍历模块属性找出 `BaseStrategy` 的非抽象子类。

#### 场景:发现未导出的策略
- **当** 后端扫描 `dex/strategies/pure_action_v2.py`
- **那么** 必须发现 `PureActionV2Strategy` 类，即使它不在 `__all__` 中

#### 场景:跳过基类
- **当** 后端扫描 `dex/strategies/base.py`
- **那么** 必须跳过 `BaseStrategy` 本身（抽象类不实例化）

### 需求:参数定义反射

后端必须用 `inspect.signature(cls.__init__)` 反射策略构造函数参数，排除 `self`。每个参数必须提取：名称、类型字符串（从 annotation 推断）、默认值、原始 annotation 字符串。无 annotation 时必须从默认值字面量推断类型（`int`/`float`/`bool`/`str`/`None`/`dict`/`list`）。无默认值的参数必须标记 `"required": true`。不可 JSON 序列化的默认值（如嵌套 dict）必须转为字符串表示。

#### 场景:有 annotation 的参数
- **当** 反射 `HybridMeanRevMomentumStrategy.__init__` 的 `rsi_period: int = 14`
- **那么** 参数定义返回 `{"name": "rsi_period", "type": "int", "default": 14, "annotation": "int", "required": false}`

#### 场景:无 annotation 的参数
- **当** 反射 `ScalpStrategy.__init__` 的 `window=10`（无 annotation）
- **那么** 参数定义返回 `{"name": "window", "type": "int", "default": 10, "annotation": null, "required": false}`（类型从默认值 10 推断为 int）

#### 场景:None 默认值
- **当** 反射 `TrendFollowStrategy.__init__` 的 `volume_threshold: float | None = None`
- **那么** 参数定义返回 `{"name": "volume_threshold", "type": "float | None", "default": null, "annotation": "float | None", "required": false}`

#### 场景:嵌套 dict 默认值
- **当** 反射 `MultiTFEnsembleStrategy.__init__` 的 `tf_weights: dict | None = None`
- **那么** 默认值 null 被返回；若默认值是实际 dict（如 `tf_params` 内部默认），则转为 JSON 字符串表示

### 需求:信号类型标记

后端必须在策略详情中返回 `signal_kind` 字段，标识策略 `generate_signals` 的返回类型契约。`GridStrategy` 返回 float position target（非标准 0/1/2/3 编码），必须标记 `"signal_kind": "position_target"`；其余 9 个策略必须标记 `"signal_kind": "discrete"`。

#### 场景:GridStrategy 信号类型
- **当** 客户端获取 `GridStrategy` 详情
- **那么** 响应含 `"signal_kind": "position_target"`

#### 场景:其他策略信号类型
- **当** 客户端获取 `TrendStrategy` 详情
- **那么** 响应含 `"signal_kind": "discrete"`

### 需求:运行时参数标记

后端必须在策略详情中返回 `runtime_params` 数组，列出 `generate_signals` 方法签名中除 `self`、`df` 之外的额外参数名（如 `enable_short`）。这些参数可在信号生成时覆盖构造函数值，前端展示时必须区分。

#### 场景:有运行时参数的策略
- **当** 客户端获取 `HybridMeanRevMomentumStrategy` 详情
- **那么** 响应含 `"runtime_params": ["enable_short"]`

#### 场景:无运行时参数的策略
- **当** 客户端获取 `TrendFollowStrategy` 详情
- **那么** 响应含 `"runtime_params": []`（其 `generate_signals(self, df)` 无额外参数）

### 需求:实盘运行状态占位

后端必须在策略摘要与详情中返回 `live_status` 字段。v0.1 本 task 不实现实盘进程查询，必须返回 `"unknown"` 占位。后续 `live-monitor-api` task 会替换为真实状态（`"running"` / `"stopped"`）。

#### 场景:查询策略运行状态
- **当** 客户端获取任意策略的摘要或详情
- **那么** `live_status` 字段值为 `"unknown"`
