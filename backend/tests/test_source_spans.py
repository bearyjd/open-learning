from dataclasses import FrozenInstanceError

import pytest

from open_learning.ai import ModelClient, ModelError, _source_spans, normalized


def test_source_spans_preserve_bounds_short_tails_and_duplicate_origins():
    duplicate = "Rain gardens retain water close to shallow roots."
    chunks = ["Soil sensors record moisture after dawn. " * 65, "a" * 802, duplicate, duplicate]
    spans = _source_spans(chunks)
    assert len(spans) > len(chunks)
    for index, chunk in enumerate(chunks):
        selected = [span for span in spans.values() if span.chunk_index == index]
        assert "".join(span.text for span in selected) == chunk
        assert all(20 <= len(normalized(span.text)) and len(span.text) <= 1000 for span in selected)
        assert all(span.text in chunk for span in selected)
    tail_spans = [span for span in spans.values() if span.chunk_index == 1]
    assert len(tail_spans) == 1
    assert tail_spans[0].text == "a" * 802
    duplicate_ids = [key for key, span in spans.items() if span.text == duplicate]
    assert len(duplicate_ids) == 2
    assert spans[duplicate_ids[0]].chunk_index == 2
    assert spans[duplicate_ids[1]].chunk_index == 3
    with pytest.raises(TypeError):
        spans["invented"] = tail_spans[0]
    with pytest.raises(FrozenInstanceError):
        tail_spans[0].chunk_index = 99


def test_source_spans_preserve_substantive_prefix_across_long_whitespace():
    text = "x" + " " * 799 + "Garden roots retain rainfall. " * 40
    spans = _source_spans([text])
    assert "".join(span.text for span in spans.values()) == text
    assert all(
        20 <= len(normalized(span.text)) and len(span.text) <= 1000 for span in spans.values()
    )
    assert all(span.text in text and span.chunk_index == 0 for span in spans.values())


def test_unquotable_substantive_region_fails_before_request(settings):
    text = "x" + " " * 1200 + "Garden roots retain rainfall. " * 40
    with pytest.raises(ModelError, match="Source text cannot fit within bounded evidence spans"):
        ModelClient(settings).insights([text])


@pytest.mark.parametrize("chunks", [[], ["short"], [" \n\t" * 1000]])
def test_no_quotable_source_fails_before_request(settings, chunks):
    with pytest.raises(ModelError, match="No source spans are long enough"):
        ModelClient(settings).insights(chunks)
