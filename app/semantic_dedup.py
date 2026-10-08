import math
import os
import requests

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
EMBEDDING_MODEL = "gemini-embedding-2"
EMBEDDING_DIM = 768
EMBEDDING_THRESHOLD = 0.68
TOP_K = 5
GEMINI_MODEL = "gemini-3.5-flash-lite"

def _text(story):
    return "TITLE: " + str(story.get("title", "")) + "\nSUMMARY: " + str(story.get("summary", ""))

def _embed(texts):
    if not texts:
        return []
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    requests_payload = [
        {
            "model": "models/" + EMBEDDING_MODEL,
            "content": {"parts": [{"text": text[:12000]}]},
            "embedContentConfig": {"outputDimensionality": EMBEDDING_DIM},
        }
        for text in texts
    ]
    response = requests.post(
        f"{GEMINI_API_BASE}/{EMBEDDING_MODEL}:batchEmbedContents",
        headers={"x-goog-api-key": key},
        json={"requests": requests_payload},
        timeout=60,
    )
    if not response.ok:
        raise RuntimeError(f"Gemini embedding API error {response.status_code}: {response.text[:500]}")
    data = response.json()
    embeddings = data.get("embeddings")
    if not isinstance(embeddings, list) or len(embeddings) != len(texts):
        raise RuntimeError("Gemini embedding API returned an invalid number of vectors")
    vectors = []
    for item in embeddings:
        values = item.get("values") if isinstance(item, dict) else None
        if not isinstance(values, list) or len(values) != EMBEDDING_DIM:
            raise RuntimeError("Gemini embedding API returned an invalid vector")
        vectors.append([float(value) for value in values])
    return vectors

def _cosine(left, right):
    denominator = math.sqrt(sum(x*x for x in left)) * math.sqrt(sum(x*x for x in right))
    return sum(x*y for x,y in zip(left,right)) / denominator if denominator else 0.0

def _verify(pairs):
    if not pairs:
        return []
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    lines = []
    for index, pair in enumerate(pairs, 1):
        lines.append(
            f"PAIR {index}\n"
            f"NEW: {_text(pair['candidate'])}\n"
            f"OLD: {_text(pair['history']) if pair['history'] else '[another new candidate]'}"
        )
    prompt = (
        "You are a high-precision news deduplication verifier. Compare each PAIR. "
        "Determine whether the stories describe the same specific real-world event. "
        "A DUPLICATE repeats the same event without a meaningful new claim. "
        "An UPDATE concerns the same event but adds materially new facts, figures, outcomes, or developments. "
        "DISTINCT means a different event, even if the company, product, person, or topic is the same. "
        "Do not treat shared topics or shared companies as duplicates. "
        "Compare claims, actors, action, product, time, place, and concrete figures. "
        "When uncertain, choose DISTINCT. "
        "Return JSON only: {\"relations\":[{\"pair\":1,\"relation_type\":\"DUPLICATE|UPDATE|DISTINCT\","
        "\"confidence\":0.0,\"new_claims\":[\"...\"]}]} .\n" + "\n\n".join(lines)
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    response = requests.post(
        f"{GEMINI_API_BASE}/{GEMINI_MODEL}:generateContent",
        headers={"x-goog-api-key": key},
        json=payload,
        timeout=90,
    )
    if not response.ok:
        raise RuntimeError(f"Gemini semantic verification error {response.status_code}: {response.text[:500]}")
    raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    import json
    result = json.loads(raw)
    relations = result.get("relations", [])
    if not isinstance(relations, list):
        raise RuntimeError("Gemini semantic verifier returned invalid relations")
    return relations

def find_semantic_relations(candidates, history):
    if not candidates:
        return []
    # One embedding request covers all new candidates and the recent history.
    history = list(history)[:150]
    texts = [_text(item) for item in candidates] + [_text(item) for item in history]
    vectors = _embed(texts)
    candidate_vectors = vectors[:len(candidates)]
    history_vectors = vectors[len(candidates):]

    pairs = []
    # Candidate -> published history retrieval.
    for ci, candidate_vector in enumerate(candidate_vectors):
        ranked = []
        for hi, history_vector in enumerate(history_vectors):
            score = _cosine(candidate_vector, history_vector)
            if score >= EMBEDDING_THRESHOLD:
                ranked.append((score, hi))
        for score, hi in sorted(ranked, reverse=True)[:TOP_K]:
            pairs.append({
                "candidate": candidates[ci],
                "history": history[hi],
                "candidate_indexes": [ci],
                "history_match": True,
                "score": score,
            })

    # Candidate -> candidate retrieval catches cross-source duplicates in the same run.
    for left in range(len(candidates)):
        ranked = []
        for right in range(left + 1, len(candidates)):
            score = _cosine(candidate_vectors[left], candidate_vectors[right])
            if score >= EMBEDDING_THRESHOLD:
                ranked.append((score, right))
        for score, right in sorted(ranked, reverse=True)[:TOP_K]:
            pairs.append({
                "candidate": candidates[left],
                "history": candidates[right],
                "candidate_indexes": [left, right],
                "history_match": False,
                "score": score,
            })

    if not pairs:
        return []

    verified = _verify(pairs)
    output = []
    for result in verified:
        try:
            pair_index = int(result.get("pair", 0)) - 1
            if not 0 <= pair_index < len(pairs):
                continue
            relation_type = str(result.get("relation_type", "DISTINCT")).upper()
            if relation_type not in {"DUPLICATE", "UPDATE", "DISTINCT"}:
                relation_type = "DISTINCT"
            confidence = max(0.0, min(1.0, float(result.get("confidence", 0.0))))
        except (TypeError, ValueError):
            continue
        pair = pairs[pair_index]
        output.append({
            "candidate_indexes": pair["candidate_indexes"],
            "history_match": pair["history_match"],
            "embedding_score": pair["score"],
            "relation_type": relation_type,
            "confidence": confidence,
            "new_claims": result.get("new_claims", []),
        })
    return output
