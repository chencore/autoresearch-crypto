## 1. 环境准备

- [x] 1.1 在 `backend/` 目录运行 `uv add numpy pandas`，补装 `dex.strategies.base` 的依赖
- [x] 1.2 验证 `uv run python -c "from dex.strategies.base import BaseStrategy; print('ok')"` 成功

## 2. Pydantic 响应模型

- [x] 2.1 创建 `backend/app/schemas/__init__.py`（空）
- [x] 2.2 创建 `backend/app/schemas/strategy.py`：
  - `class ParamDef(BaseModel)`: `name: str`、`type: str`、`default: Any`、`annotation: str | None`、`required: bool`
  - `class StrategySummary(BaseModel)`: `name: str`、`module: str`、`file: str`、`description: str`、`params_count: int`、`live_status: str`
  - `class StrategyDetail(BaseModel)`: `name: str`、`module: str`、`file: str`、`description: str`、`class_docstring: str`、`params: list[ParamDef]`、`signal_kind: str`、`runtime_params: list[str]`、`live_status: str`
  - `class StrategyListResponse(BaseModel)`: `strategies: list[StrategySummary]`、`total: int`

## 3. 策略注册服务

- [x] 3.1 创建 `backend/app/services/__init__.py`（空）
- [x] 3.2 创建 `backend/app/services/strategy_registry.py`：
  - `SIGNAL_KIND_OVERRIDE = {"GridStrategy": "position_target"}`
  - `def _infer_type(default) -> str`: 从默认值字面量推断类型（int/float/bool/str/dict/list/None→"null"）
  - `def _serialize_default(default) -> Any`: 先 `json.dumps` + `json.loads` 兜底，失败转 `str(default)`；`inspect.Parameter.empty` 返回 `None`
  - `def _reflect_params(cls) -> list[ParamDef]`: `inspect.signature(cls.__init__)`，排除 `self`，每个参数提取 name/annotation/default/required
  - `def _reflect_runtime_params(cls) -> list[str]`: `inspect.signature(cls.generate_signals)`，排除 `self` 与 `df`
  - `def discover_strategies() -> list[type[BaseStrategy]]`: `pkgutil.iter_modules(dex.strategies.__path__)` 遍历，`importlib.import_module` 导入，`inspect.getmembers` 找 `BaseStrategy` 子类（排除 `BaseStrategy` 自身与抽象类），try/except 包住每个模块 import 失败，按类名排序
  - `def list_summaries() -> list[StrategySummary]`: 调 `discover_strategies`，每个类构造 `StrategySummary`
  - `def get_detail(name: str) -> StrategyDetail | None`: 找匹配类名的策略，构造 `StrategyDetail`；找不到返回 `None`

## 4. 路由改造

- [x] 4.1 改造 `backend/app/api/v1/strategy.py`：
  - 移除占位 `@router.get("")` 返回 `{"module": "strategy", "status": "todo"}`
  - `@router.get("", response_model=StrategyListResponse)`：调 `list_summaries()`，返回 `{"strategies": [...], "total": N}`
  - `@router.get("/{name}", response_model=StrategyDetail)`：调 `get_detail(name)`，返回详情；找不到抛 `HTTPException(status_code=404, detail=f"strategy not found: {name}")`（被全局异常处理器转成 `{"error": {"code": "not_found", "message": "..."}}`）

## 5. 启动验证

- [x] 5.1 在 `backend/` 目录启动 `uv run uvicorn app.main:app --port 8000`，无异常
- [x] 5.2 `curl -s http://127.0.0.1:8000/api/v1/strategy | python3 -m json.tool` 验证返回 `total: 10`，含 `PureActionV2Strategy` 与 `MultiTFEnsembleStrategy`
- [x] 5.3 `curl -s http://127.0.0.1:8000/api/v1/strategy/HybridMeanRevMomentumStrategy | python3 -m json.tool` 验证返回 10 个参数、`signal_kind: "discrete"`、`runtime_params: ["enable_short"]`
- [x] 5.4 `curl -s http://127.0.0.1:8000/api/v1/strategy/GridStrategy | python3 -m json.tool` 验证 `signal_kind: "position_target"`
- [x] 5.5 `curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/api/v1/strategy/NonExistent` 验证返回 404
- [x] 5.6 `curl -s http://127.0.0.1:8000/api/v1/strategy/NonExistent` 验证返回 `{"error": {"code": "not_found", "message": "strategy not found: NonExistent"}}`
- [x] 5.7 `curl -s http://127.0.0.1:8000/api/v1/strategy/MultiTFEnsembleStrategy | python3 -m json.tool` 验证嵌套 dict 默认值可序列化（`tf_weights` 默认 null，`tf_params` 默认 null）
- [x] 5.8 验证列表响应时间 < 500ms（11 个策略反射）
