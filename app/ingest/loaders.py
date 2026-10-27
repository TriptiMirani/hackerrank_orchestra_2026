"""Typed CSV loading: pandas reads, Pydantic validates every row. Fails loudly on schema drift."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TypeVar

import pandas as pd
from pydantic import BaseModel

from app.config import DATASET_DIR
from app.schemas import (AuditLogEntry, DeletionRequest, DeletionRule, EvidenceFile, SampleOutputRow,
                         SystemActivity, UserIdentity)

M = TypeVar("M", bound=BaseModel)


def load_csv(path: Path, model: type[M]) -> list[M]:
    # dtype=str + keep_default_na=False: never let pandas turn "" into NaN or ids into numbers.
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    expected = list(model.model_fields)
    if list(df.columns) != expected:
        raise ValueError(f"{path.name}: columns {list(df.columns)} != expected {expected}")
    return [model.model_validate(row) for row in df.to_dict(orient="records")]


def _p(name: str, root: Path | None) -> Path:
    return (root or DATASET_DIR) / name


@lru_cache(maxsize=None)
def load_requests(root: Path | None = None) -> tuple[DeletionRequest, ...]:
    return tuple(load_csv(_p("deletion_requests.csv", root), DeletionRequest))


@lru_cache(maxsize=None)
def load_sample_requests(root: Path | None = None) -> tuple[DeletionRequest, ...]:
    return tuple(load_csv(_p("sample_deletion_requests.csv", root), DeletionRequest))


@lru_cache(maxsize=None)
def load_sample_output(root: Path | None = None) -> tuple[SampleOutputRow, ...]:
    return tuple(load_csv(_p("sample_output.csv", root), SampleOutputRow))


@lru_cache(maxsize=None)
def load_identities(root: Path | None = None) -> tuple[UserIdentity, ...]:
    return tuple(load_csv(_p("user_identities.csv", root), UserIdentity))


@lru_cache(maxsize=None)
def load_audit_log(root: Path | None = None) -> tuple[AuditLogEntry, ...]:
    return tuple(load_csv(_p("user_audit_log.csv", root), AuditLogEntry))


@lru_cache(maxsize=None)
def load_activity(root: Path | None = None) -> tuple[SystemActivity, ...]:
    return tuple(load_csv(_p("system_activity.csv", root), SystemActivity))


@lru_cache(maxsize=None)
def load_rules(root: Path | None = None) -> tuple[DeletionRule, ...]:
    return tuple(load_csv(_p("deletion_rules.csv", root), DeletionRule))


@lru_cache(maxsize=None)
def load_manifest(root: Path | None = None) -> tuple[EvidenceFile, ...]:
    return tuple(load_csv(_p("evidence_files.csv", root), EvidenceFile))
