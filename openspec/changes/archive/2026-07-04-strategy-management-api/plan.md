# Plan: strategy-management-api

> **详细实现计划**。位置铁律：本文件必须位于 `openspec/changes/<change-name>/plan.md`。

---

## 计划总览

本次实现分 **5 个阶段**。

| 阶段 | 目标 | 关键输出 | 估时 |
|------|------|----------|------|
| S1 | 环境准备 | backend 环境补装 numpy/pandas，dex 可 import | 0.1d |
| S2 | Pydantic 模型 | `schemas/strategy.py` 三类响应模型 | 0.25d |
| S3 | 策略注册服务 | `services/strategy_registry.py` 反射 + 扫描 | 0.5d |
| S4 | 路由改造 | `api/v1/strategy.py` 列表 + 详情端点 | 0.25d |
| S5 | 启动验证 | curl 6 项验证全绿 | 0.25d |

**总估时**：约 1.35 人日。

---

## S1. 环境准备

### 目标
backend 环境能 import `dex.strategies.base`。

### 实施步骤

1. 在 `backend/` 目录运行 `uv add numpy pandas`
2. 验证 `uv run python -c "from dex.strategies.base import BaseStrategy; print('ok')"` 输出 `ok`

### ✅ 完成验证
- [ ] `backend/pyproject.toml` dependencies 含 numpy、pandas
- [ ] 上述 python 命令输出 `ok`

---

## S2. Pydantic 响应模型

### 目标
定义 API 响应的 Pydantic schema，前端可据此生成 TS 类型。

### 实施步骤

1. 创建 `backend/app/schemas/__init__.py`（空文件）
2. 创建 `backend/app/schemas/strategy.py`：
   ```python
   from typing import Any
   from pydantic import BaseModel

   class ParamDef(BaseModel):
       name: str
       type: str
       default: Any = None
       annotation: str | None = None
       required: bool

   class StrategySummary(BaseModel):
       name: str
       module: str
       file: str
       description: str
       params_count: int
       live_status: str

   class StrategyDetail(BaseModel):
       name: str
       module: str
       file: str
       description: str
       class_docstring: str
       params: list[ParamDef]
       signal_kind: str
       runtime_params: list[str]
       live_status: str

   class StrategyListResponse(BaseModel):
       strategies: list[StrategySummary]
       total: int
   ```

### ✅ 完成验证
- [ ] `uv run python -c "from app.schemas.strategy import ParamDef, StrategySummary, StrategyDetail, StrategyListResponse; print('ok')"` 输出 `ok`

---

## S3. 策略注册服务

### 目标
反射 + 扫描发现 11 个策略，提取元数据。

### 实施步骤

1. 创建 `backend/app/services/__init__.py`（空文件）
2. 创建 `backend/app/services/strategy_registry.py`：
   - `import importlib`、`import inspect`、`import json`、`import pkgutil`、`import logging`
   - `from dex.strategies.base import BaseStrategy`
   - `from dex.strategies import __path__ as _strategies_path`
   - `from app.schemas.strategy import ParamDef, StrategySummary, StrategyDetail`
   - `logger = logging.getLogger(__name__)`
   - `SIGNAL_KIND_OVERRIDE = {"GridStrategy": "position_target"}`
   - `def _infer_type(default) -> str`：
     - `default is None` → `"null"`
     - `isinstance(default, bool)` → `"bool"`（bool 必须在 int 前判断，因为 `isinstance(True, int)` 为 True）
     - `isinstance(default, int)` → `"int"`
     - `isinstance(default, float)` → `"float"`
     - `isinstance(default, str)` → `"str"`
     - `isinstance(default, dict)` → `"dict"`
     - `isinstance(default, list)` → `"list"`
     - 否则 → `"unknown"`
   - `def _serialize_default(default) -> Any`：
     - `default is inspect.Parameter.empty` → `None`
     - 尝试 `json.loads(json.dumps(default))`，成功返回结果
     - 失败（含不可序列化对象）→ `str(default)`
   - `def _reflect_params(cls) -> list[ParamDef]`：
     - `sig = inspect.signature(cls.__init__)`
     - 遍历 `sig.parameters.values()`，跳过 `name == "self"` 与 `kind == VAR_POSITIONAL` / `VAR_KEYWORD`
     - 每个 param：`annotation = str(p.annotation) if p.annotation is not inspect.Parameter.empty else None`；`default_value = p.default`；`required = p.default is inspect.Parameter.empty`；`type_str = annotation or _infer_type(default_value)`（若 required 且无 annotation，`type_str = "unknown"`）
     - 返回 `list[ParamDef]`
   - `def _reflect_runtime_params(cls) -> list[str]`：
     - `sig = inspect.signature(cls.generate_signals)`
     - 遍历参数，排除 `self` 与 `df`，返回剩余参数名列表
     - 若 `generate_signals` 无签名（抽象方法），返回 `[]`
   - `def _get_description(cls) -> str`：
     - `doc = inspect.getdoc(cls) or ""`
     - 返回 doc 的第一行（`doc.split("\n")[0].strip()`）
   - `def discover_strategies() -> list[type[BaseStrategy]]`：
     - `results = []`
     - `for module_info in pkgutil.iter_modules(_strategies_path):`
       - `try: module = importlib.import_module(f"dex.strategies.{module_info.name}") except Exception as e: logger.warning(...); continue`
       - `for name, obj in inspect.getmembers(module, inspect.isclass):`
         - `if obj is BaseStrategy: continue`
         - `if not issubclass(obj, BaseStrategy): continue`
         - `if inspect.isabstract(obj): continue`
         - `if obj.__module__ != module.__name__: continue`（避免重复导入基类）
         - `results.append(obj)`
     - 按 `cls.__name__` 去重 + 排序
     - 返回
   - `def list_summaries() -> list[StrategySummary]`：
     - `strategies = discover_strategies()`
     - 每个 `cls` 构造 `StrategySummary(name=cls.__name__, module=cls.__module__, file=inspect.getsourcefile(cls).split("/")[-1], description=_get_description(cls), params_count=len(_reflect_params(cls)), live_status="unknown")`
   - `def get_detail(name: str) -> StrategyDetail | None`：
     - `for cls in discover_strategies(): if cls.__name__ == name: ...`
     - 构造 `StrategyDetail`，含 `params=_reflect_params(cls)`、`signal_kind=SIGNAL_KIND_OVERRIDE.get(cls.__name__, "discrete")`、`runtime_params=_reflect_runtime_params(cls)`、`class_docstring=inspect.getdoc(cls) or ""`
     - 找不到返回 `None`

### ✅ 完成验证
- [ ] `uv run python -c "from app.services.strategy_registry import discover_strategies; ss=discover_strategies(); print(len(ss), [s.__name__ for s in ss])"` 输出 `10 [...]`，含 `PureActionV2Strategy`、`MultiTFEnsembleStrategy`
- [ ] `uv run python -c "from app.services.strategy_registry import list_summaries; ss=list_summaries(); print(len(ss)); print(ss[0].model_dump_json())"` 输出 `11` 与第一个策略的 JSON

---

## S4. 路由改造

### 目标
替换 strategy router 的占位端点为真实接口。

### 实施步骤

1. 改造 `backend/app/api/v1/strategy.py`：
   ```python
   from fastapi import APIRouter, HTTPException
   from app.schemas.strategy import StrategyListResponse, StrategyDetail
   from app.services.strategy_registry import list_summaries, get_detail

   router = APIRouter()

   @router.get("", response_model=StrategyListResponse)
   async def list_strategies() -> StrategyListResponse:
       summaries = list_summaries()
       return StrategyListResponse(strategies=summaries, total=len(summaries))

   @router.get("/{name}", response_model=StrategyDetail)
   async def get_strategy(name: str) -> StrategyDetail:
       detail = get_detail(name)
       if detail is None:
           raise HTTPException(status_code=404, detail=f"strategy not found: {name}")
       return detail
   ```

### ✅ 完成验证
- [ ] `uv run python -c "from app.api.v1.strategy import router; print(router.routes)"` 显示两个路由

---

## S5. 启动验证

### 目标
全链路冒烟，6 项 curl 全绿。

### 实施步骤

1. 在 `backend/` 目录运行 `uv run uvicorn app.main:app --port 8000`
2. 执行验证命令清单

### ✅ 完成验证
- [ ] `curl -s http://127.0.0.1:8000/api/v1/strategy | python3 -m json.tool` 返回 `total: 10`，含 `PureActionV2Strategy` 与 `MultiTFEnsembleStrategy`
- [ ] `curl -s http://127.0.0.1:8000/api/v1/strategy/HybridMeanRevMomentumStrategy` 返回 10 个参数、`signal_kind: "discrete"`、`runtime_params: ["enable_short"]`
- [ ] `curl -s http://127.0.0.1:8000/api/v1/strategy/GridStrategy` 返回 `signal_kind: "position_target"`
- [ ] `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/v1/strategy/NonExistent` 返回 `404`
- [ ] `curl -s http://127.0.0.1:8000/api/v1/strategy/NonExistent` 返回 `{"error": {"code": "not_found", "message": "strategy not found: NonExistent"}}`
- [ ] `curl -s http://127.0.0.1:8000/api/v1/strategy/MultiTFEnsembleStrategy` 的 `tf_weights` 与 `tf_params` 默认值可序列化（null）
- [ ] 列表响应时间 < 500ms：`curl -s -o /dev/null -w "%{time_total}" http://127.0.0.1:8000/api/v1/strategy` 输出 < 0.5

---

## 风险与回滚

- **风险**：`dex.strategies.__init__` 的 lazy `__getattr__` 触发循环 import → 缓解：`pkgutil.iter_modules` 直接遍历文件，`importlib.import_module("dex.strategies.<name>")` 不触发 `__init__.py` 的 `__getattr__`
- **风险**：某策略模块 import 失败（如依赖缺失）→ 缓解：try/except 包住每个模块，失败跳过 + 日志警告，不影响整体列表
- **风险**：`inspect.signature` 对无 annotation 参数返回 `inspect.Parameter.empty` → 缓解：`_infer_type` 兜底从默认值推断
- **回滚**：恢复 `strategy.py` 占位 + 删除新增的 `services/` 与 `schemas/` 文件
