"""Environment configuration.

Note what is deliberately absent: there is no MODEL_FOR_IMPLEMENT or
MODEL_FOR_QA. Only Main's own model is fixed here, for cost control
(SPEC Q1). Sub-agent models are chosen by Main per spawn from
config/models.available.json, and the catalog schema rejects
default_for_skill / role / skills so it cannot become an assignment table.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    main_model: str
    main_reasoning_effort: str
    embedding_model: str
    embedding_dims: int
    orch_api_token: str
    database_url: str | None
    org_id: str
    user_id: str
    run_max_usd: float | None
    target_repo_path: str | None
    models_catalog_path: Path
    workspace_root: Path


def _float_or_none(raw: str | None) -> float | None:
    return float(raw) if raw else None


def load_settings() -> Settings:
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        main_model=os.getenv("MAIN_MODEL", "gpt-6-luna"),
        main_reasoning_effort=os.getenv("MAIN_REASONING_EFFORT", "high"),
        embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        embedding_dims=int(os.getenv("EMBEDDING_DIMS", "1536")),
        orch_api_token=os.getenv("ORCH_API_TOKEN", "change-me"),
        database_url=os.getenv("DATABASE_URL"),
        org_id=os.getenv("ORG_ID", "default"),
        user_id=os.getenv("USER_ID", "default"),
        run_max_usd=_float_or_none(os.getenv("RUN_MAX_USD")),
        target_repo_path=os.getenv("TARGET_REPO_PATH"),
        models_catalog_path=REPO_ROOT / "config" / "models.available.json",
        workspace_root=REPO_ROOT / "workspace",
    )


settings = load_settings()
