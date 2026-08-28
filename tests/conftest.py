"""Test bootstrap.

``custom_components/horaco_switch/__init__.py`` imports Home Assistant, which
is not needed to test HTML parsing. This loads ``const``, ``models`` and
``parser`` as members of a stand-in package so their relative imports resolve
without executing the integration's ``__init__``.

Result: ``python3 -m pytest tests/`` needs only ``pytest`` and
``beautifulsoup4``.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import types

_COMPONENT = (
    pathlib.Path(__file__).resolve().parent.parent
    / "custom_components"
    / "horaco_switch"
)

PACKAGE = "horaco_switch_parsing"


def _bootstrap() -> None:
    if PACKAGE in sys.modules:
        return

    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(_COMPONENT)]
    sys.modules[PACKAGE] = package

    # Order matters: parser imports const and models.
    for name in ("const", "models", "parser"):
        path = _COMPONENT / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"{PACKAGE}.{name}"] = module
        spec.loader.exec_module(module)
        setattr(package, name, module)


_bootstrap()
