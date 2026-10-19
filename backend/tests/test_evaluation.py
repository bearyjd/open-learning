"""Offline contracts and the production HTTP seam; no model inference."""

import copy
import json

import httpx
import pytest
from pydantic import ValidationError

from open_learning import evaluation as ev


@pytest.fixture
def corpus():
    passage = "A red tag requires inspection before shelving. A blue tag means shelving."
    cases = [
        {
            "id": f"case_{index}",
            "operation": "insight" if index == 0 else "tutor",
            "source_ids": ["rules"],
            "title": "Tags",
            "message": "Explain the rule.",
            "history": [],
            "dimensions": ["support", "citation", "clarity"],
            "must_address": ["Inspection precedes shelving."],
            "forbidden_inferences": ["No savings are described."],
            "critical_failures": ["Reversing inspection order."],
        }
        for index in range(8)
    ]
    output = {
        "answer": "Inspect red tags first.",
        "question": "What comes first?",
        "evidence": passage,
    }
    return {
        "schema_version": 1,
        "corpus_version": "1.0.0",
        "license": "CC0-1.0",
        "provenance": "Original fictional test rules.",
        "sources": [{"id": "rules", "title": "Rules", "coverage": "full_text", "text": passage}],
        "cases": cases,
        "calibrations": [
            {
                "id": "positive",
                "case_id": "case_1",
                "output": output,
                "expected_scores": dict.fromkeys(cases[1]["dimensions"], 2),
                "expected_critical_failure": False,
                "reasons": "Supported.",
            },
            {
                "id": "negative",
                "case_id": "case_1",
                "output": {**output, "answer": "Shelve red tags before inspection."},
                "expected_scores": {"support": 0, "citation": 0, "clarity": 2},
                "expected_critical_failure": True,
                "reasons": "Contradicts valid quotation.",
            },
        ],
    }


def test_corpus_is_strict_and_requires_exact_membership(corpus):
    assert len(ev.Corpus.model_validate(corpus).cases) == 8
    for mutate in (
        lambda value: value.update(schema_version=True),
        lambda value: value.update(extra="unrecognized"),
        lambda value: value["cases"].pop(),
        lambda value: value["cases"][1].update(id="case_0"),
        lambda value: value["cases"][1].update(source_ids=["missing"]),
        lambda value: value["calibrations"][0]["expected_scores"].update(support=True),
    ):
        bad = copy.deepcopy(corpus)
        mutate(bad)
        with pytest.raises(ValueError):
            ev.Corpus.model_validate(bad)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.com/v1",
        "http://127.0.0.1.evil/v1",
        "http://user:secret@127.0.0.1/v1",
        "http://127.0.0.1/v1?secret=key",
        "file:///v1",
        "http://127.0.0.1/other/v1",
    ],
)
def test_endpoint_rejects_nonlocal_or_credentialed_urls(endpoint):
    with pytest.raises(ValueError):
        ev.local_endpoint(endpoint)


def test_private_output_refuses_overwrite(tmp_path):
    output = tmp_path / "run.json"
    ev.write_private(output, {"test": 1})
    assert output.stat().st_mode & 0o777 == 0o600
    assert json.loads(output.read_text()) == {"test": 1}
    with pytest.raises(FileExistsError):
        ev.write_private(output, {"test": 2})
    assert json.loads(output.read_text()) == {"test": 1}


@pytest.mark.parametrize("destination", ["missing_parent", "directory", "existing_file"])
def test_cli_run_rejects_unwritable_destination_before_model_calls(
    corpus, tmp_path, monkeypatch, destination
):
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps(corpus))
    output = tmp_path / "run.json"
    if destination == "missing_parent":
        output = tmp_path / "absent" / "run.json"
    elif destination == "directory":
        output.mkdir()
    else:
        output.write_text("original")

    def forbidden(*args, **kwargs):
        pytest.fail("run_corpus called before destination was reserved")

    monkeypatch.setattr(ev, "run_corpus", forbidden)
    assert (
        ev.main(
            [
                "run",
                "--execute",
                "--corpus",
                str(corpus_path),
                "--model",
                "fixture",
                "--output",
                str(output),
            ]
        )
        == 2
    )
    if destination == "existing_file":
        assert output.read_text() == "original"


def test_calibration_retains_false_prose_with_valid_quote(corpus):
    value = ev.Corpus.model_validate(corpus)
    assert value.calibrations[1].output.evidence == value.sources[0].text
    assert value.calibrations[1].expected_scores["support"] == 0
    bad = copy.deepcopy(corpus)
    bad["calibrations"][0]["output"]["evidence"] = "A fabricated quotation not present in source."
    with pytest.raises((ValueError, ValidationError)):
        ev.Corpus.model_validate(bad)


def test_empty_starter_message_is_valid(corpus):
    corpus["cases"][1]["message"] = ""
    assert ev.Corpus.model_validate(corpus).cases[1].message == ""


@pytest.fixture
def transport(monkeypatch):
    received = []
    responses = []

    def respond(request):
        received.append(request)
        draft = responses.pop(0)
        if isinstance(draft, Exception):
            raise draft
        if isinstance(draft, httpx.Response):
            return draft
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(draft)}}]})

    client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs)
    )
    return received, responses


def drafts():
    card = {
        "evidence_id": "e0",
        "content": {
            "title": "Tags",
            "idea": "Inspect before shelving.",
            "explanation": "Red means inspect.",
            "why_it_matters": "Inspection must precede shelving.",
            "tags": ["tags"],
        },
    }
    tutor = {
        "evidence_id": "e0",
        "content": {
            "answer": "Inspect red tags first.",
            "question": "What comes first?",
        },
    }
    return [{"insights": [card]}] + [copy.deepcopy(tutor) for _ in range(7)]


@pytest.mark.parametrize(
    "failure",
    [
        {"evidence_id": "unknown", "content": {"answer": "Wrong", "question": "What now?"}},
        {"evidence_id": "e0", "content": {"answer": "x" * 2001, "question": "What now?"}},
        httpx.Response(200, content=b"not-json"),
        httpx.Response(503, content=b"secret-response-do-not-log"),
        httpx.ReadTimeout("secret-error-do-not-log"),
    ],
)
def test_runner_preserves_failures_uses_production_adapter_and_isolates_env(
    corpus,
    transport,
    monkeypatch,
    failure,
):
    received, responses = transport
    responses.extend(drafts())
    responses[2] = failure
    monkeypatch.setenv("OL_AI_BASE_URL", "https://paid-provider.invalid/v1")
    monkeypatch.setenv("OL_AI_API_KEY", "secret-env-do-not-log")
    monkeypatch.setenv("OL_AI_MODEL", "paid-model")
    run = ev.run_corpus(ev.Corpus.model_validate(corpus), model="fixture", timeout=3)
    assert len(received) == len(run.results) == 8
    assert [item.case_id for item in run.results] == [item["id"] for item in corpus["cases"]]
    assert run.results[2].error_category == "model_contract_error"
    assert run.results[2].output is None
    assert sum(item.status == "success" for item in run.results) == 7
    assert "secret" not in run.model_dump_json()
    for request in received:
        assert str(request.url) == "http://127.0.0.1:8080/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer local"
        assert json.loads(request.content)["model"] == "fixture"
        assert request.extensions["timeout"]["read"] == 3
    assert json.loads(received[0].content)["response_format"]["json_schema"]["name"] == (
        "_InsightDraftBatch"
    )
    assert (
        json.loads(received[1].content)["response_format"]["json_schema"]["name"] == "_TutorDraft"
    )


def test_run_rejects_remote_before_client_construction(corpus, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("client constructed before local endpoint validation")

    monkeypatch.setattr(ev, "ModelClient", forbidden)
    with pytest.raises(ValueError):
        ev.run_corpus(
            ev.Corpus.model_validate(corpus),
            model="fixture",
            endpoint="https://paid-provider.invalid/v1",
        )


@pytest.fixture
def run(corpus, transport):
    transport[1].extend(drafts())
    return ev.run_corpus(ev.Corpus.model_validate(corpus), model="fixture")


def completed_review(run, corpus):
    corpus = ev.Corpus.model_validate(corpus)
    value = ev.review_template(run, corpus).model_dump(mode="json")
    value.update(
        status="complete",
        reviewer_id="independent-reviewer",
        independent=True,
        review_method="human",
    )
    for entry in value["entries"]:
        entry.update(
            scores=dict.fromkeys(entry["scores"], 2),
            rationale="Checked every claim.",
            critical_failure=False,
        )
    for entry, calibration in zip(value["calibrations"], corpus.calibrations, strict=True):
        entry.update(
            scores=calibration.expected_scores,
            rationale=calibration.reasons,
            critical_failure=calibration.expected_critical_failure,
        )
    return value


def test_review_pending_then_complete_is_only_path_to_acceptance(run, corpus):
    corpus = ev.Corpus.model_validate(corpus)
    assert ev.score_run(run, corpus)["status"] == "pending_review"
    template = ev.review_template(run, corpus)
    assert ev.score_run(run, corpus, template)["accepted"] is False
    review = ev.Review.model_validate(completed_review(run, corpus.model_dump()))
    score = ev.score_run(run, corpus, review)
    assert score["accepted"] is True
    assert score["total_cases"] == score["contract_successes"] == score["semantic_successes"] == 8


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update(rubric_version=2),
        lambda data: data.update(run_sha256="0" * 64),
        lambda data: data.update(corpus_sha256="0" * 64),
        lambda data: data.update(independent=False),
        lambda data: data.update(reviewer_id=None),
        lambda data: data.update(review_method=None),
        lambda data: data["entries"].pop(),
        lambda data: data["entries"].append(data["entries"][0]),
        lambda data: data["entries"][0].update(id="foreign"),
        lambda data: data["entries"][0].update(output_sha256="0" * 64),
        lambda data: data["entries"][0].update(rationale=None),
        lambda data: data["entries"][0]["scores"].update(support=True),
        lambda data: data["entries"][0]["scores"].update(support=3),
        lambda data: data["entries"][0]["scores"].update(support="2"),
        lambda data: data["entries"][0]["scores"].pop("support"),
        lambda data: data["calibrations"].pop(),
        lambda data: data["calibrations"][1]["scores"].update(support=2),
        lambda data: data["calibrations"][1].update(critical_failure=False),
    ],
)
def test_review_rejects_invalid_bindings_coverage_scores_attestation_and_calibration(
    run,
    corpus,
    mutation,
):
    review = completed_review(run, corpus)
    mutation(review)
    with pytest.raises(ValueError):
        ev.score_run(run, ev.Corpus.model_validate(corpus), ev.Review.model_validate(review))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update(corpus_sha256="0" * 64),
        lambda data: data.update(production_sha256="0" * 64),
        lambda data: data.update(model="altered-model"),
        lambda data: data["results"].pop(),
        lambda data: data["results"][1].update(case_id="case_0"),
        lambda data: data["results"][0].update(operation="tutor"),
        lambda data: data["results"][1]["output"].update(answer="Altered prose"),
    ],
)
def test_tampered_runs_fail_closed(run, corpus, mutation):
    review = ev.Review.model_validate(completed_review(run, corpus))
    value = run.model_dump(mode="json")
    mutation(value)
    with pytest.raises(ValueError):
        ev.score_run(
            ev.Run.model_validate_json(json.dumps(value)), ev.Corpus.model_validate(corpus), review
        )


@pytest.mark.parametrize("dimension_score,critical", [(1, False), (2, True)])
def test_one_bad_case_cannot_hide_in_aggregate(run, corpus, dimension_score, critical):
    value = completed_review(run, corpus)
    value["entries"][3]["scores"]["support"] = dimension_score
    value["entries"][3]["critical_failure"] = critical
    score = ev.score_run(run, ev.Corpus.model_validate(corpus), ev.Review.model_validate(value))
    assert score["accepted"] is False
    assert score["status"] == "failed"
    assert score["total_cases"] == 8
    assert score["semantic_successes"] == 7


def test_failed_case_remains_in_denominator_and_cannot_receive_review(corpus, transport):
    transport[1].extend(drafts())
    transport[1][3] = httpx.Response(500)
    run = ev.run_corpus(ev.Corpus.model_validate(corpus), model="fixture")
    value = completed_review(run, corpus)
    score = ev.score_run(run, ev.Corpus.model_validate(corpus), ev.Review.model_validate(value))
    assert score["contract_successes"] == score["semantic_successes"] == 7
    assert score["total_cases"] == 8
    assert score["accepted"] is False
    value["entries"].append({**value["entries"][1], "id": "case_3"})
    with pytest.raises(ValueError):
        ev.score_run(run, ev.Corpus.model_validate(corpus), ev.Review.model_validate(value))


def test_cli_offline_commands_and_absent_execute_never_construct_client(
    run,
    corpus,
    tmp_path,
    monkeypatch,
    capsys,
):
    def forbidden(*args, **kwargs):
        pytest.fail("offline command constructed ModelClient")

    monkeypatch.setattr(ev, "ModelClient", forbidden)
    corpus_path, run_path, review_path = [
        tmp_path / name for name in ("corpus.json", "run.json", "review.json")
    ]
    corpus_path.write_text(json.dumps(corpus))
    ev.write_private(run_path, run)
    assert ev.main(["validate-corpus", "--corpus", str(corpus_path)]) == 0
    assert (
        ev.main(
            [
                "run",
                "--corpus",
                str(corpus_path),
                "--model",
                "fixture",
                "--output",
                str(tmp_path / "unused.json"),
            ]
        )
        == 2
    )
    assert not (tmp_path / "unused.json").exists()
    assert (
        ev.main(
            [
                "review-template",
                "--corpus",
                str(corpus_path),
                "--run",
                str(run_path),
                "--output",
                str(review_path),
            ]
        )
        == 0
    )
    assert (
        ev.main(
            [
                "score",
                "--corpus",
                str(corpus_path),
                "--run",
                str(run_path),
                "--review",
                str(review_path),
            ]
        )
        == 1
    )
    assert "pending_review" in capsys.readouterr().out
