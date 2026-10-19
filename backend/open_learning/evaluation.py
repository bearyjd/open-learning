"""Bounded, local model samples with independent manual semantic review."""

import hashlib
import ipaddress
import json
import os
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .ai import InsightBatch, TutorResult, verify_quote

Text = Annotated[str, Field(min_length=1, max_length=4000)]
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_.-]{1,100}$")]
Version = Annotated[int, Field(strict=True, ge=1, le=1)]
Score = Annotated[int, Field(strict=True, ge=0, le=2)]
Dimension = Literal["support", "citation", "clarity", "assessment", "follow_up", "uncertainty"]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Output = InsightBatch | TutorResult
DEFAULT_CORPUS = Path(__file__).resolve().parents[1] / "evaluation" / "corpus.json"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Source(Strict):
    id: Identifier
    title: Text
    coverage: Literal["full_text", "excerpt"]
    text: Annotated[str, Field(min_length=20, max_length=8000)]


class Turn(Strict):
    role: Literal["user", "assistant"]
    message: Text


class Case(Strict):
    id: Identifier
    operation: Literal["insight", "tutor"]
    source_ids: list[Identifier] = Field(min_length=1, max_length=4)
    title: Text
    message: Text
    history: list[Turn] = Field(max_length=12)
    dimensions: list[Dimension] = Field(min_length=1, max_length=6)
    must_address: list[Text] = Field(min_length=1, max_length=12)
    forbidden_inferences: list[Text] = Field(min_length=1, max_length=12)
    critical_failures: list[Text] = Field(min_length=1, max_length=12)


class Calibration(Strict):
    id: Identifier
    case_id: Identifier
    output: Output
    expected_scores: dict[Dimension, Score]
    expected_critical_failure: bool
    reasons: Text


def unique(values):
    if len(set(values)) != len(values):
        raise ValueError("duplicate entries")


def check_output(case, output, sources):
    if case.operation == "insight":
        if not isinstance(output, InsightBatch):
            raise ValueError("operation mismatch")
        chunks = [sources[key].text for key in case.source_ids]
        for insight in output.insights:
            if insight.chunk_index >= len(chunks):
                raise ValueError("source mismatch")
            verify_quote(insight.evidence, chunks[insight.chunk_index])
    else:
        if not isinstance(output, TutorResult):
            raise ValueError("operation mismatch")
        verify_quote(output.evidence, "\n\n".join(sources[key].text for key in case.source_ids))


class Corpus(Strict):
    schema_version: Version
    corpus_version: Literal["1.0.0"]
    license: Literal["CC0-1.0"]
    provenance: Text
    sources: list[Source] = Field(min_length=1, max_length=8)
    cases: list[Case] = Field(min_length=8, max_length=8)
    calibrations: list[Calibration] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def consistent(self):
        for items in (self.sources, self.cases, self.calibrations):
            unique([item.id for item in items])
        sources = {item.id: item for item in self.sources}
        cases = {item.id: item for item in self.cases}
        if sum(case.operation == "insight" for case in self.cases) != 1:
            raise ValueError("require one insight and seven tutor cases")
        for case in self.cases:
            unique(case.source_ids)
            unique(case.dimensions)
            if not set(case.source_ids) <= sources.keys():
                raise ValueError("unknown source")
            if not {"support", "citation", "clarity"} <= set(case.dimensions):
                raise ValueError("missing core dimensions")
        for item in self.calibrations:
            if item.case_id not in cases:
                raise ValueError("unknown calibration case")
            case = cases[item.case_id]
            if set(item.expected_scores) != set(case.dimensions):
                raise ValueError("calibration dimensions mismatch")
            check_output(case, item.output, sources)
        if {item.expected_critical_failure for item in self.calibrations} != {True, False}:
            raise ValueError("positive and negative calibrations required")
        return self


def canonical(value):
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def production_digest():
    return hashlib.sha256(Path(__file__).with_name("ai.py").read_bytes()).hexdigest()


def load_corpus(path=DEFAULT_CORPUS):
    return Corpus.model_validate_json(Path(path).read_bytes(), strict=True)


def local_endpoint(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path.rstrip("/") != "/v1"):
        raise ValueError("invalid local endpoint")
    host = parsed.hostname
    if host == "localhost":
        host = "127.0.0.1"
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError("nonlocal endpoint")
    except (ValueError, TypeError) as error:
        raise ValueError("nonlocal endpoint") from error
    authority = f"[{host}]" if ":" in host else host
    if parsed.port is not None:
        authority += f":{parsed.port}"
    return f"{parsed.scheme}://{authority}/v1"


def write_private(path, value):
    data = canonical(value) + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
