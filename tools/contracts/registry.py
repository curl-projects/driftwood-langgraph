from __future__ import annotations

from typing import Dict, List

from .base import Contract


_REGISTRY: Dict[str, Contract] = {}


def register(contract: Contract) -> None:
    _REGISTRY[contract.name] = contract


def get(name: str) -> Contract:
    return _REGISTRY[name]


def resolve(names: List[str]) -> List[Contract]:
    out: List[Contract] = []
    for n in names:
        c = _REGISTRY.get(n)
        if c:
            out.append(c)
    return out


