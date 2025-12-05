from __future__ import annotations

from .registry import register, resolve
from .media import MediaContract
from .article import ArticleContract
from .generic import GenericContract
from .generated_media import GeneratedMediaContract
import logging
logger = logging.getLogger(__name__)


def _bootstrap_contracts() -> None:
    register(MediaContract())
    register(ArticleContract())
    register(GenericContract())
    register(GeneratedMediaContract())
    try:
        logger.info("contracts.bootstrap: registered=%s", [
            c for c in ("media","article","generic","generated_media")
        ])
    except Exception:
        pass


_bootstrap_contracts()


