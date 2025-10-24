from typing import Callable

from . import videos, images, excerpts, poetry, longform
from .system import system_prompt as enricher_core
from ..shared.base import base_prompt


def _base_for(ct: str) -> Callable[[], str]:
    return lambda: base_prompt(ct)


# Explicit specialized prompts where available; fall back to base for all others we support.
_CONTENT_TYPE_TO_PROMPT: dict[str, Callable[[], str]] = {
    "videos": videos.system_prompt,
    "images": images.system_prompt,
    "excerpts": excerpts.system_prompt,
    "poetry": poetry.system_prompt,
    "longform": longform.system_prompt,
    # Generic (base) prompts for additional content types
    "recipes": _base_for("recipes"),
    "papers": _base_for("papers"),
    "bucketlist": _base_for("bucketlist"),
    "devlogs": _base_for("devlogs"),
    "tweets": _base_for("tweets"),
    "podcasts": _base_for("podcasts"),
    "words": _base_for("words"),
    "soundscapes": _base_for("soundscapes"),
    "activities": _base_for("activities"),
}


def get_system_prompt(content_type: str) -> str:
    # Keep the Enricher system prompt generic so it can process multiple candidates over time.
    # Per-turn subtype will be derived from the latest message and injected as lightweight context.
    return enricher_core()


