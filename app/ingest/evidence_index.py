"""Parse every manifest file once and index the results. Extraction is per file, never per case."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.config import DATASET_DIR, OCR_CACHE_DIR
from app.extract.lexicon import Lexicon, build_lexicon
from app.extract.ocr import ocr_file
from app.extract.router import OcrFn, parse_evidence
from app.ingest.loaders import load_audit_log, load_identities, load_manifest
from app.llm.cache import LLMCache
from app.schemas import EvidenceFile, Observation, ParseResult, ParseStatus


def subject_keys(values) -> set[str]:
    """Normalized forms an identifier can be compared in: casefolded text and phone digits."""
    out = set()
    for v in values:
        if v:
            out.add(str(v).strip().casefold())
            digits = "".join(ch for ch in str(v) if ch.isdigit())
            if len(digits) >= 8 and "@" not in str(v):
                out.add(digits)
    return out


@dataclass
class EvidenceIndex:
    files: dict[str, EvidenceFile]
    results: dict[str, ParseResult]
    by_resource: dict[str, list[Observation]] = field(default_factory=dict)
    # normalized identifier values mentioned anywhere in each file (for subject-scoped coverage)
    file_subjects: dict[str, set[str]] = field(default_factory=dict)

    @classmethod
    def build(cls, manifest: tuple[EvidenceFile, ...], root: Path, lexicon: Lexicon | None = None,
              ocr: OcrFn | None = None) -> "EvidenceIndex":
        files = {ef.file_id: ef for ef in manifest}
        results = {fid: parse_evidence(ef, root, lexicon, ocr) for fid, ef in sorted(files.items())}
        by_resource: dict[str, list[Observation]] = defaultdict(list)
        subjects: dict[str, set[str]] = defaultdict(set)
        for res in results.values():
            for o in res.observations:
                by_resource[o.resource_id].append(o)
                subjects[o.file_id].update(subject_keys(o.identifiers.values()))
                if o.lookup_key:
                    subjects[o.file_id].update(subject_keys([o.lookup_key]))
        return cls(files, results, dict(by_resource), dict(subjects))

    def failed(self) -> list[ParseResult]:
        return [r for r in self.results.values() if r.status is ParseStatus.PARSE_FAILED]

    def needs_ocr(self) -> list[ParseResult]:
        return [r for r in self.results.values() if r.status is ParseStatus.NEEDS_OCR]


OCR_STATS = {"cache_hits": 0, "computed": 0, "failed": 0}


def cached_ocr(cache_dir: Path = OCR_CACHE_DIR) -> OcrFn:
    cache = LLMCache(cache_dir)

    def run(file_id: str, path: Path):
        res = ocr_file(file_id, path, cache)  # cache hit: 0 s; miss: runs RapidOCR once
        key = "cache_hits" if res.cached else ("computed" if res.status.value == "OK" else "failed")
        OCR_STATS[key] += 1
        return res
    return run


@lru_cache(maxsize=None)
def get_index(root: Path = DATASET_DIR, with_text_facts: bool = True) -> EvidenceIndex:
    """with_text_facts=False reproduces Phase 1 (structured files only, images NEEDS_OCR)."""
    data_root = root if root != DATASET_DIR else None
    if not with_text_facts:
        return EvidenceIndex.build(load_manifest(data_root), root)
    lexicon = build_lexicon(load_identities(data_root), load_audit_log(data_root))
    return EvidenceIndex.build(load_manifest(data_root), root, lexicon, cached_ocr())
