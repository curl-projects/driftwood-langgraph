from __future__ import annotations

from .registry import register, resolve
from .media import MediaContract
from .article import ArticleContract
from .generic import GenericContract


def _bootstrap_contracts() -> None:
    register(MediaContract())
    register(ArticleContract())
    register(GenericContract())


_bootstrap_contracts()


