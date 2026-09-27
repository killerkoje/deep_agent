"""The set of models Main may call.

This module exists so `G_MODEL_KNOWN` has something to check against, and
so Main can read capability hints when picking a model per spawn.

It must never grow a skill->model mapping. The JSON schema already
forbids `default_for_skill` / `role` / `skills`; this loader re-checks at
runtime so a hand-edited catalog fails loudly instead of quietly turning
into the assignment table the design rules out (README 0.2).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import settings

FORBIDDEN_FIELDS = ("default_for_skill", "role", "skills")

# Embedding models live in the same catalog for cost bookkeeping, but they
# are not spawnable - `decide` or `implement` can never run on one.
_NON_SPAWNABLE_PREFIXES = ("text-embedding-",)


class CatalogError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _load(path_str: str) -> dict[str, Any]:
    path = Path(path_str)
    if not path.exists():
        raise CatalogError(f"model catalog not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    models = data.get("models")
    if not isinstance(models, list) or not models:
        raise CatalogError("catalog has no models")

    for entry in models:
        for field in FORBIDDEN_FIELDS:
            if field in entry:
                raise CatalogError(
                    f"'{field}' in catalog entry {entry.get('id')!r}: "
                    "role-to-model assignment tables are not allowed. "
                    "Describe the model's character in 'notes' and let Main choose."
                )
    return data


def all_models(path: Path | None = None) -> list[dict[str, Any]]:
    return _load(str(path or settings.models_catalog_path))["models"]


def spawnable_models(path: Path | None = None) -> list[dict[str, Any]]:
    return [
        m
        for m in all_models(path)
        if not m["id"].startswith(_NON_SPAWNABLE_PREFIXES)
    ]


def spawnable_ids(path: Path | None = None) -> set[str]:
    """What `G_MODEL_KNOWN` validates against."""
    return {m["id"] for m in spawnable_models(path)}


def list_models_for_prompt(path: Path | None = None) -> str:
    """Capability hints shown to Main. Prices included so cost is a visible
    factor in its choice; no skill is ever named here."""
    lines = []
    for m in spawnable_models(path):
        price = ""
        if m.get("input_per_1m") is not None:
            price = f" | in ${m['input_per_1m']}/1M out ${m.get('output_per_1m')}/1M"
        lines.append(f"- {m['id']}{price}\n    {m.get('notes', '')}".rstrip())
    return "\n".join(lines)
