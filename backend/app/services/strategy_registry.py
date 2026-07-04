import importlib
import inspect
import json
import logging
import pkgutil
from typing import Any

from dex.strategies import __path__ as _strategies_path
from dex.strategies.base import BaseStrategy

from app.schemas.strategy import ParamDef, StrategyDetail, StrategySummary

logger = logging.getLogger(__name__)

SIGNAL_KIND_OVERRIDE: dict[str, str] = {
    "GridStrategy": "position_target",
}


def _infer_type(default: Any) -> str:
    if default is None:
        return "null"
    if isinstance(default, bool):
        return "bool"
    if isinstance(default, int):
        return "int"
    if isinstance(default, float):
        return "float"
    if isinstance(default, str):
        return "str"
    if isinstance(default, dict):
        return "dict"
    if isinstance(default, list):
        return "list"
    return "unknown"


def _serialize_default(default: Any) -> Any:
    if default is inspect.Parameter.empty:
        return None
    try:
        return json.loads(json.dumps(default))
    except (TypeError, ValueError):
        return str(default)


def _reflect_params(cls: type) -> list[ParamDef]:
    sig = inspect.signature(cls.__init__)
    params: list[ParamDef] = []
    for name, p in sig.parameters.items():
        if name == "self":
            continue
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        annotation = str(p.annotation) if p.annotation is not inspect.Parameter.empty else None
        required = p.default is inspect.Parameter.empty
        type_str = annotation or (_infer_type(p.default) if not required else "unknown")
        params.append(
            ParamDef(
                name=name,
                type=type_str,
                default=_serialize_default(p.default),
                annotation=annotation,
                required=required,
            )
        )
    return params


def _reflect_runtime_params(cls: type) -> list[str]:
    try:
        sig = inspect.signature(cls.generate_signals)
    except (ValueError, TypeError):
        return []
    result: list[str] = []
    for name, p in sig.parameters.items():
        if name in ("self", "df"):
            continue
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        result.append(name)
    return result


def _get_description(cls: type) -> str:
    doc = inspect.getdoc(cls) or ""
    return doc.split("\n", 1)[0].strip() if doc else ""


def discover_strategies() -> list[type[BaseStrategy]]:
    seen: dict[str, type[BaseStrategy]] = {}
    for module_info in pkgutil.iter_modules(_strategies_path):
        module_name = f"dex.strategies.{module_info.name}"
        try:
            module = importlib.import_module(module_name)
        except Exception as e:
            logger.warning("Failed to import %s: %s", module_name, e)
            continue
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if obj is BaseStrategy:
                continue
            if not issubclass(obj, BaseStrategy):
                continue
            if inspect.isabstract(obj):
                continue
            if obj.__module__ != module.__name__:
                continue
            seen[obj.__name__] = obj
    return [seen[k] for k in sorted(seen.keys())]


def _to_summary(cls: type[BaseStrategy]) -> StrategySummary:
    source_file = inspect.getsourcefile(cls) or ""
    return StrategySummary(
        name=cls.__name__,
        module=cls.__module__,
        file=source_file.rsplit("/", 1)[-1] if source_file else "",
        description=_get_description(cls),
        params_count=len(_reflect_params(cls)),
        live_status="unknown",
    )


def list_summaries() -> list[StrategySummary]:
    return [_to_summary(cls) for cls in discover_strategies()]


def get_detail(name: str) -> StrategyDetail | None:
    for cls in discover_strategies():
        if cls.__name__ == name:
            source_file = inspect.getsourcefile(cls) or ""
            return StrategyDetail(
                name=cls.__name__,
                module=cls.__module__,
                file=source_file.rsplit("/", 1)[-1] if source_file else "",
                description=_get_description(cls),
                class_docstring=inspect.getdoc(cls) or "",
                params=_reflect_params(cls),
                signal_kind=SIGNAL_KIND_OVERRIDE.get(cls.__name__, "discrete"),
                runtime_params=_reflect_runtime_params(cls),
                live_status="unknown",
            )
    return None
