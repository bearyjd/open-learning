"""Offline contracts and the production HTTP seam; no model inference."""

import copy
import json

import pytest
from pydantic import ValidationError

from open_learning import evaluation as ev


@pytest.fixture
def corpus():
    passage = "A red tag requires inspection before shelving. A blue tag means shelving."
    cases = [
        {
            "id": f"case_{index}", "operation": "insight" if index == 0 else "tutor",
            "source_ids": ["rules"], "title": "Tags", "message": "Explain the rule.",
            "history": [], "dimensions": ["support", "citation", "clarity"],
            "must_address": ["Inspection precedes shelving."],
            "forbidden_inferences": ["No savings are described."],
            "critical_failures": ["Reversing inspection order."],
        }
        for index in range(8)
    ]
    output = {"answer": "Inspect red tags first.", "question": "What comes first?",
              "evidence": passage}
    return {
        "schema_version": 1, "corpus_version": "1.0.0", "license": "CC0-1.0",
        "provenance": "Original fictional test rules.",
        "sources": [{"id": "rules", "title": "Rules", "coverage": "full_text",
                     "text": passage}],
        "cases": cases,
        "calibrations": [
            {"id": "positive", "case_id": "case_1", "output": output,
             "expected_scores": dict.fromkeys(cases[1]["dimensions"], 2),
             "expected_critical_failure": False, "reasons": "Supported."},
            {"id": "negative", "case_id": "case_1",
             "output": {**output, "answer": "Shelve red tags before inspection."},
             "expected_scores": {"support": 0, "citation": 0, "clarity": 2},
             "expected_critical_failure": True, "reasons": "Contradicts valid quotation."},
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


@pytest.mark.parametrize("endpoint", [
    "https://example.com/v1", "http://127.0.0.1.evil/v1", "http://user:secret@127.0.0.1/v1",
    "http://127.0.0.1/v1?secret=key", "file:///v1", "http://127.0.0.1/other/v1",
])
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


def test_calibration_retains_false_prose_with_valid_quote(corpus):
    value = ev.Corpus.model_validate(corpus)
    assert value.calibrations[1].output.evidence == value.sources[0].text
    assert value.calibrations[1].expected_scores["support"] == 0
    bad = copy.deepcopy(corpus)
    bad["calibrations"][0]["output"]["evidence"] = "A fabricated quotation not present in source."
    with pytest.raises((ValueError, ValidationError)):
        ev.Corpus.model_validate(bad)
