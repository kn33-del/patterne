from __future__ import annotations

from typing import Any

__all__ = ["app", "create_app"]


def __getattr__(name: str) -> Any:
    if name == "app":
        from .main import app

        return app
    if name == "create_app":
        from .main import create_app

        return create_app
    raise AttributeError(name)
