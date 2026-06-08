"""引擎注册表 + 工厂。

按需懒加载：只在 get_engine() 被调用时才 import 对应引擎模块，
避免无谓地拉起重依赖（ax / nevergrad / pymoo）——你只需安装要用的那个引擎的依赖。

新增引擎：实现 base.Engine 的子类，在 _REGISTRY 注册即可（架构上可插拔）。
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..spec import OptimizationSpec
    from .base import Engine

# name -> (module, class)
_REGISTRY: dict[str, tuple[str, str]] = {
    "bayesian_ax": ("circuit_optimizer.engines.bayesian_ax", "AxEngine"),
    "evolutionary": ("circuit_optimizer.engines.evolutionary", "NevergradEngine"),
    "nsga": ("circuit_optimizer.engines.nsga", "NSGAEngine"),
}


def available() -> list[str]:
    """已注册的引擎名列表。"""
    return list(_REGISTRY)


def get_engine(name: str, spec: "OptimizationSpec") -> "Engine":
    """按名创建引擎实例。未知名 → ValueError。"""
    if name not in _REGISTRY:
        raise ValueError(
            f"未知优化引擎 {name!r}；可选：{', '.join(_REGISTRY)}"
        )
    module_path, class_name = _REGISTRY[name]
    module = importlib.import_module(module_path)
    engine_cls = getattr(module, class_name)
    return engine_cls(spec)
