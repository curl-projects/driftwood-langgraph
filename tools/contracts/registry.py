from __future__ import annotations

from typing import Dict, List
import logging

from .base import Contract


_REGISTRY: Dict[str, Contract] = {}
_logger = logging.getLogger(__name__)


def register(contract: Contract) -> None:
    _REGISTRY[contract.name] = contract
    try:
        _logger.info("contracts.register: %s", contract.name)
    except Exception:
        pass


def get(name: str) -> Contract:
    return _REGISTRY[name]


def resolve(names: List[str]) -> List[Contract]:
    out: List[Contract] = []
    try:
        _logger.info("contracts.resolve: requested=%s available=%s", names, list(_REGISTRY.keys()))
    except Exception:
        pass
    for n in names:
        c = _REGISTRY.get(n)
        if c:
            out.append(c)
    return out


