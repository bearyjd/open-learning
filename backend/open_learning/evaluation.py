"""Bounded, local model samples with independent manual semantic review."""

import argparse
import hashlib
import ipaddress
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .ai import InsightBatch, ModelClient, ModelError, TutorResult, verify_quote
from .config import Settings

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
    message: Annotated[str, Field(max_length=4000)]
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
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/v1"
    ):
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


@contextmanager
def reserved_private(path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        yield stream


def write_private(path, value):
    data = canonical(value) + b"\n"
    with reserved_private(path) as stream:
        stream.write(data)


class CaseResult(Strict):
    case_id: Identifier
    operation: Literal["insight", "tutor"]
    status: Literal["success", "failed"]
    elapsed_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    output: Output | None
    output_sha256: Digest | None
    error_category: Literal["model_contract_error"] | None

    @model_validator(mode="after")
    def consistent(self):
        if self.status == "success":
            if self.output is None or self.error_category is not None:
                raise ValueError("invalid successful result")
            if self.output_sha256 != digest(self.output):
                raise ValueError("output digest mismatch")
        elif (
            self.output is not None or self.output_sha256 is not None or self.error_category is None
        ):
            raise ValueError("invalid failure result")
        return self


class Run(Strict):
    schema_version: Version
    corpus_sha256: Digest
    production_sha256: Digest
    created_at: datetime
    model: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_./:-]{1,200}$")]
    endpoint: Text
    timeout_seconds: Annotated[float, Field(gt=0, le=180, allow_inf_nan=False)]
    results: list[CaseResult] = Field(min_length=8, max_length=8)

    @model_validator(mode="after")
    def consistent(self):
        unique([result.case_id for result in self.results])
        if self.created_at.utcoffset() is None or self.created_at.utcoffset().total_seconds() != 0:
            raise ValueError("UTC timestamp required")
        if local_endpoint(self.endpoint) != self.endpoint:
            raise ValueError("canonical local endpoint required")
        return self


def validate_run(run, corpus):
    if run.corpus_sha256 != digest(corpus) or run.production_sha256 != production_digest():
        raise ValueError("provenance mismatch")
    sources = {source.id: source for source in corpus.sources}
    if [item.case_id for item in run.results] != [case.id for case in corpus.cases]:
        raise ValueError("case membership mismatch")
    for case, result in zip(corpus.cases, run.results, strict=True):
        if result.operation != case.operation:
            raise ValueError("operation mismatch")
        if result.output is not None:
            check_output(case, result.output, sources)


def run_corpus(corpus, *, model, endpoint="http://127.0.0.1:8080/v1", timeout=90.0):
    endpoint = local_endpoint(endpoint)
    settings = Settings(
        _env_file=None,
        api_token="evaluation-placeholder-0123456789-ABCDEF",
        ai_base_url=endpoint,
        ai_model=model,
        ai_api_key="local",
        ai_timeout_seconds=timeout,
    )
    run = Run(
        schema_version=1,
        corpus_sha256=digest(corpus),
        production_sha256=production_digest(),
        created_at=datetime.now(UTC),
        model=model,
        endpoint=endpoint,
        timeout_seconds=float(timeout),
        results=[
            CaseResult(
                case_id=case.id,
                operation=case.operation,
                status="failed",
                elapsed_seconds=0.0,
                output=None,
                output_sha256=None,
                error_category="model_contract_error",
            )
            for case in corpus.cases
        ],
    )
    client = ModelClient(settings)
    sources = {source.id: source for source in corpus.sources}
    results = []
    for case in corpus.cases:
        started = perf_counter()
        try:
            chunks = [sources[key].text for key in case.source_ids]
            output = (
                client.insights(chunks)
                if case.operation == "insight"
                else client.tutor(
                    "\n\n".join(chunks),
                    case.title,
                    case.message,
                    [turn.model_dump() for turn in case.history],
                )
            )
            check_output(case, output, sources)
            result = CaseResult(
                case_id=case.id,
                operation=case.operation,
                status="success",
                output=output,
                output_sha256=digest(output),
                error_category=None,
                elapsed_seconds=perf_counter() - started,
            )
        except ModelError:
            result = CaseResult(
                case_id=case.id,
                operation=case.operation,
                status="failed",
                output=None,
                output_sha256=None,
                error_category="model_contract_error",
                elapsed_seconds=perf_counter() - started,
            )
        results.append(result)
    run.results = results
    validate_run(run, corpus)
    return run


class Rating(Strict):
    id: Identifier
    output_sha256: Digest
    scores: dict[Dimension, Score | None]
    rationale: Text | None
    critical_failure: bool | None


class Review(Strict):
    schema_version: Version
    rubric_version: Version
    run_sha256: Digest
    corpus_sha256: Digest
    status: Literal["pending", "complete"]
    reviewer_id: Annotated[str, Field(min_length=1, max_length=200)] | None
    review_method: Literal["human", "ai_assisted"] | None
    independent: bool
    entries: list[Rating] = Field(max_length=8)
    calibrations: list[Rating] = Field(min_length=2, max_length=2)


def review_template(run, corpus):
    validate_run(run, corpus)
    cases = {case.id: case for case in corpus.cases}

    def pending(identifier, output, dimensions):
        return Rating(
            id=identifier,
            output_sha256=digest(output),
            scores=dict.fromkeys(dimensions),
            rationale=None,
            critical_failure=None,
        )

    return Review(
        schema_version=1,
        rubric_version=1,
        run_sha256=digest(run),
        corpus_sha256=digest(corpus),
        status="pending",
        reviewer_id=None,
        review_method=None,
        independent=False,
        entries=[
            pending(item.case_id, item.output, cases[item.case_id].dimensions)
            for item in run.results
            if item.status == "success"
        ],
        calibrations=[
            pending(item.id, item.output, cases[item.case_id].dimensions)
            for item in corpus.calibrations
        ],
    )


def validate_ratings(entries, expected, *, complete):
    unique([entry.id for entry in entries])
    if {entry.id for entry in entries} != expected.keys():
        raise ValueError("review membership mismatch")
    for entry in entries:
        output, dimensions = expected[entry.id]
        if entry.output_sha256 != digest(output) or set(entry.scores) != set(dimensions):
            raise ValueError("review output or dimension mismatch")
        if complete and (
            entry.rationale is None
            or entry.critical_failure is None
            or any(score is None for score in entry.scores.values())
        ):
            raise ValueError("incomplete rating")


def score_run(run, corpus, review=None):
    run = Run.model_validate_json(canonical(run), strict=True)
    validate_run(run, corpus)
    cases = {case.id: case for case in corpus.cases}
    pending = review is None or review.status == "pending"
    if review is not None:
        review = Review.model_validate_json(canonical(review), strict=True)
        if review.run_sha256 != digest(run) or review.corpus_sha256 != digest(corpus):
            raise ValueError("review provenance mismatch")
        complete = review.status == "complete"
        if complete and (
            not review.independent or not review.reviewer_id or not review.review_method
        ):
            raise ValueError("independent review attestation required")
        validate_ratings(
            review.entries,
            {
                item.case_id: (item.output, cases[item.case_id].dimensions)
                for item in run.results
                if item.status == "success"
            },
            complete=complete,
        )
        validate_ratings(
            review.calibrations,
            {
                item.id: (item.output, cases[item.case_id].dimensions)
                for item in corpus.calibrations
            },
            complete=complete,
        )
        calibrations = {item.id: item for item in corpus.calibrations}
        for rating in review.calibrations:
            calibration = calibrations[rating.id]
            if complete and (
                rating.scores != calibration.expected_scores
                or rating.critical_failure != calibration.expected_critical_failure
            ):
                raise ValueError("reviewer calibration failed")
    ratings = {entry.id: entry for entry in review.entries} if review is not None else {}
    decisions = []
    for item in run.results:
        rating = ratings.get(item.case_id)
        passed = (
            not pending
            and item.status == "success"
            and rating is not None
            and not rating.critical_failure
            and all(score == 2 for score in rating.scores.values())
        )
        decisions.append(
            {
                "case_id": item.case_id,
                "contract_valid": item.status == "success",
                "meets_rubric": passed,
                "scores": rating.scores if rating else None,
                "critical_failure": rating.critical_failure if rating else None,
            }
        )
    accepted = all(item["meets_rubric"] for item in decisions)
    return {
        "status": "pending_review" if pending else "passed" if accepted else "failed",
        "accepted": accepted,
        "total_cases": len(run.results),
        "contract_successes": sum(item["contract_valid"] for item in decisions),
        "semantic_successes": sum(item["meets_rubric"] for item in decisions),
        "review_method": review.review_method if review else None,
        "reviewer_id": review.reviewer_id if review else None,
        "cases": decisions,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate-corpus", "run", "review-template", "score"):
        command = commands.add_parser(name)
        command.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
        if name == "run":
            command.add_argument("--execute", action="store_true")
            command.add_argument("--model", required=True)
            command.add_argument("--endpoint", default="http://127.0.0.1:8080/v1")
            command.add_argument("--timeout", type=float, default=90.0)
        if name in {"run", "review-template"}:
            command.add_argument("--output", type=Path, required=True)
        if name in {"review-template", "score"}:
            command.add_argument("--run", type=Path, required=True)
        if name == "score":
            command.add_argument("--review", type=Path)
    args = parser.parse_args(argv)
    try:
        corpus = load_corpus(args.corpus)
        if args.command == "validate-corpus":
            result = {
                "status": "valid",
                "cases": len(corpus.cases),
                "corpus_sha256": digest(corpus),
            }
        elif args.command == "run":
            if not args.execute:
                raise ValueError("explicit execution required")
            with reserved_private(args.output) as stream:
                run = run_corpus(
                    corpus, model=args.model, endpoint=args.endpoint, timeout=args.timeout
                )
                stream.write(canonical(run) + b"\n")
            result = score_run(run, corpus)
        else:
            run = Run.model_validate_json(args.run.read_bytes(), strict=True)
            if args.command == "review-template":
                write_private(args.output, review_template(run, corpus))
                result = {"status": "pending_review"}
            else:
                review = (
                    Review.model_validate_json(args.review.read_bytes(), strict=True)
                    if args.review
                    else None
                )
                result = score_run(run, corpus, review)
        print(json.dumps(result, sort_keys=True))
        return int(args.command == "score" and not result["accepted"])
    except (ValueError, OSError):
        print(json.dumps({"error": "invalid_input_or_output"}))
        return 2
    except Exception:
        print(json.dumps({"error": "evaluation_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
