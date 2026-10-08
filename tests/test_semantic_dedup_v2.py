import json

import app.semantic_dedup as semantic


class Response:
    ok = True
    def __init__(self, payload):
        self.payload = payload
        self.text = json.dumps(payload)
    def json(self):
        return self.payload


def test_embedding_retrieval_is_verified_by_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if "batchEmbedContents" in url:
            return Response({"embeddings": [{"values": [1.0] * 768}, {"values": [1.0] * 768}]})
        return Response({"candidates": [{"content": {"parts": [{"text": json.dumps({
            "relations": [{"pair": 1, "relation_type": "DUPLICATE", "confidence": 0.97, "new_claims": []}]
        })}]}}]})

    monkeypatch.setattr(semantic.requests, "post", fake_post)
    result = semantic.find_semantic_relations(
        [{"title": "OpenAI launches X", "summary": "new tool"}],
        [{"title": "OpenAI launches X", "summary": "new tool"}],
    )
    assert result[0]["relation_type"] == "DUPLICATE"
    assert result[0]["confidence"] == 0.97
    assert len(calls) == 2


def test_update_is_not_treated_as_duplicate(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")

    def fake_post(url, **kwargs):
        if "batchEmbedContents" in url:
            return Response({"embeddings": [{"values": [1.0] * 768}, {"values": [1.0] * 768}]})
        return Response({"candidates": [{"content": {"parts": [{"text": json.dumps({
            "relations": [{"pair": 1, "relation_type": "UPDATE", "confidence": 0.91, "new_claims": ["new figure"]}]
        })}]}}]})

    monkeypatch.setattr(semantic.requests, "post", fake_post)
    result = semantic.find_semantic_relations(
        [{"title": "Company reports results", "summary": "revenue rises"}],
        [{"title": "Company reports results", "summary": "revenue reported"}],
    )
    assert result[0]["relation_type"] == "UPDATE"
    assert result[0]["new_claims"] == ["new figure"]


def test_below_embedding_threshold_never_reaches_gemini_verifier(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return Response({"embeddings": [{"values": [1.0] * 768}, {"values": [0.0] * 768}]})

    monkeypatch.setattr(semantic.requests, "post", fake_post)
    assert semantic.find_semantic_relations(
        [{"title": "Apple", "summary": "new chip"}],
        [{"title": "Football", "summary": "match"}],
    ) == []
    assert len(calls) == 1
