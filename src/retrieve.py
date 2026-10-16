"""
Dependency Migration Assistant — Retrieval Module

build_index(settings_path) embeds every chunk of one library and stores it in
Qdrant (one collection per library, e.g. "pandas_chunks"). Running it again
rebuilds the collection from scratch, so it never holds stale or duplicate data.

Embedded text is "section > subsection: text", so a subsection title such as
"Empty DataFrames/Series will now default to have a RangeIndex" helps search
find its paragraph.
"""

import json
import logging
import re
import uuid
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, FieldCondition, Filter, IsNullCondition, MatchAny, MatchValue, PayloadField, PointStruct,
    VectorParams,
)
from rank_bm25 import BM25Okapi

from ingest import load_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Third-party libraries log every HTTP request at INFO level; keep our own messages readable.
for noisy in ("httpx", "httpcore", "urllib3", "sentence_transformers", "huggingface_hub"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)

MODEL_NAME = "BAAI/bge-small-en-v1.5"
QDRANT_HOST = "localhost"
QDRANT_PORT = 6333
EMBED_BATCH_SIZE = 32
UPSERT_BATCH_SIZE = 128
KEYWORD_SCROLL_BATCH = 256
RRF_K = 60            # standard Reciprocal Rank Fusion constant
CANDIDATE_POOL = 20   # results each searcher contributes before the two lists are fused
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "  # optional for BGE v1.5
_MODEL_CACHE = {}     # model name -> loaded model; loaded once per process
_KEYWORD_CACHE = {}  # collection name -> (BM25 index, chunks); built once per process


def collection_name(settings: dict) -> str:
    """One Qdrant collection per library, named from the settings file."""
    return f"{settings['library']}_chunks"


def point_id(chunk_id: str) -> str:
    """Qdrant ids must be integers or UUIDs, so derive a stable UUID from the chunk id."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def embed_text(chunk: dict) -> str:
    """The text that gets embedded: section > subsection: text."""
    heading = chunk["section"]
    if chunk.get("subsection"):
        heading = f"{heading} > {chunk['subsection']}"
    return f"{heading}: {chunk['text']}"


def load_model():
    """Load the embedding model (imported here so importing this module stays fast)."""
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(MODEL_NAME)


def build_index(settings_path: str, client=None, model=None) -> int:
    """
    Embed all chunks for one library and store them in Qdrant.
    Returns the number of chunks indexed, or 0 if something went wrong.
    `client` and `model` are optional so tests can pass in lightweight stand-ins.
    """
    settings = load_settings(settings_path)
    if settings is None:
        return 0

    chunks_path = Path("data/chunks") / f"{settings['library']}_chunks.json"
    if not chunks_path.exists():
        logger.error("Chunks file not found: %s — run chunk.py first", chunks_path)
        return 0
    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    if not chunks:
        logger.error("No chunks in %s", chunks_path)
        return 0

    client = client or QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    try:
        client.get_collections()  # quick connection check
    except Exception as error:
        logger.error("Cannot reach Qdrant at %s:%s (is the Docker container running?): %s",
                     QDRANT_HOST, QDRANT_PORT, error)
        return 0

    model = model or load_model()
    logger.info("Embedding %d chunks with %s ...", len(chunks), MODEL_NAME)
    vectors = model.encode(
        [embed_text(c) for c in chunks],
        batch_size=EMBED_BATCH_SIZE,
        normalize_embeddings=True,
        show_progress_bar=True,
    )

    name = collection_name(settings)
    if client.collection_exists(name):
        client.delete_collection(name)  # rebuild from scratch: no stale or duplicate points
    client.create_collection(
        collection_name=name,
        vectors_config=VectorParams(size=int(vectors.shape[1]), distance=Distance.COSINE),
    )

    for start in range(0, len(chunks), UPSERT_BATCH_SIZE):
        batch = [
            PointStruct(id=point_id(c["chunk_id"]), vector=vectors[start + i].tolist(), payload=c)
            for i, c in enumerate(chunks[start:start + UPSERT_BATCH_SIZE])
        ]
        client.upsert(collection_name=name, points=batch)

    _KEYWORD_CACHE.pop(name, None)  # the stored chunks just changed
    count = client.count(collection_name=name, exact=True).count
    logger.info("Indexed %d chunks into collection '%s'", count, name)
    return count


def tokenize(text: str) -> list:
    """Lowercase words: 'DataFrame.append()' -> ['dataframe', 'append']; 'numeric_only' stays one word."""
    return re.findall(r"\w+", text.lower())


def fetch_all_chunks(client, name: str) -> list:
    """Read every stored chunk back out of Qdrant (payloads only, no vectors)."""
    chunks, offset = [], None
    while True:
        points, offset = client.scroll(
            collection_name=name, limit=KEYWORD_SCROLL_BATCH,
            offset=offset, with_payload=True, with_vectors=False,
        )
        chunks.extend(point.payload for point in points)
        if offset is None:
            return chunks


def get_keyword_index(client, name: str):
    """Build the BM25 index once per process from what Qdrant holds. None if the collection is empty."""
    if name not in _KEYWORD_CACHE:
        chunks = fetch_all_chunks(client, name)
        if not chunks:
            return None
        bm25 = BM25Okapi([tokenize(embed_text(c)) for c in chunks])
        _KEYWORD_CACHE[name] = (bm25, chunks)
    return _KEYWORD_CACHE[name]


def matches_filters(chunk: dict, filters: dict) -> bool:
    """True if the chunk satisfies every filter. A list value means "any of these"."""
    for key, value in filters.items():
        if isinstance(value, (list, tuple, set)):
            if chunk.get(key) not in value:
                return False
        elif chunk.get(key) != value:
            return False
    return True


def keyword_search(query: str, library: str, top_k: int = 5, filters: dict | None = None, client=None) -> list:
    """
    Exact-word (BM25) search over one library's chunks.
    `filters` matches exact values on any chunk field, e.g. {"version": "2.0.0"}; a list matches any of its values.
    Chunks sharing no word with the query are never returned.
    Returns [{"rank": int, "score": float, "chunk": {...}}], best first.
    """
    if top_k <= 0:
        return []
    tokens = tokenize(query)
    if not tokens:
        return []

    name = collection_name({"library": library})
    client = client or QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    try:
        if not client.collection_exists(name):
            logger.error("Collection '%s' not found — run retrieve.py to build the index first", name)
            return []
        index = get_keyword_index(client, name)
    except Exception as error:
        logger.error("Cannot reach Qdrant at %s:%s (is the Docker container running?): %s",
                     QDRANT_HOST, QDRANT_PORT, error)
        return []
    if index is None:
        logger.error("Collection '%s' is empty", name)
        return []

    bm25, chunks = index
    scores = bm25.get_scores(tokens)
    results = []
    for i in sorted(range(len(chunks)), key=lambda i: -scores[i]):
        if scores[i] <= 0:
            break  # everything after this shares no word with the query
        if filters and not matches_filters(chunks[i], filters):
            continue
        results.append({"rank": len(results) + 1, "score": float(scores[i]), "chunk": chunks[i]})
        if len(results) == top_k:
            break
    return results


def get_model():
    """The embedding model, loaded once per process."""
    if MODEL_NAME not in _MODEL_CACHE:
        _MODEL_CACHE[MODEL_NAME] = load_model()
    return _MODEL_CACHE[MODEL_NAME]


def build_qdrant_filter(filters: dict | None):
    """Turn {"version": "2.0.1", "category": "bug_fix"} into a Qdrant filter. None matches missing/null; a list matches any of its values."""
    if not filters:
        return None
    conditions = []
    for key, value in filters.items():
        if value is None:
            conditions.append(IsNullCondition(is_null=PayloadField(key=key)))
        elif isinstance(value, (list, tuple, set)):
            conditions.append(FieldCondition(key=key, match=MatchAny(any=list(value))))
        else:
            conditions.append(FieldCondition(key=key, match=MatchValue(value=value)))
    return Filter(must=conditions)


def collection_ready(client, name: str) -> bool:
    """True if Qdrant is reachable and the collection exists; otherwise logs why and returns False."""
    try:
        if not client.collection_exists(name):
            logger.error("Collection '%s' not found — run retrieve.py to build the index first", name)
            return False
    except Exception as error:
        logger.error("Cannot reach Qdrant at %s:%s (is the Docker container running?): %s",
                     QDRANT_HOST, QDRANT_PORT, error)
        return False
    return True


def semantic_search(query: str, library: str, top_k: int = 5, filters: dict | None = None,
                    client=None, model=None, query_prefix: str = "") -> list:
    """
    Meaning-based search: finds chunks that say the same thing in different words.
    Returns [{"rank": int, "score": float, "chunk": {...}}], best first.
    """
    if top_k <= 0 or not query.strip():
        return []
    name = collection_name({"library": library})
    client = client or QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    if not collection_ready(client, name):
        return []

    vector = (model or get_model()).encode([query_prefix + query], normalize_embeddings=True)[0].tolist()
    try:
        points = client.query_points(
            collection_name=name, query=vector, limit=top_k,
            query_filter=build_qdrant_filter(filters), with_payload=True,
        ).points
    except Exception as error:
        logger.error("Semantic search failed: %s", error)
        return []
    return [{"rank": i, "score": float(point.score), "chunk": point.payload} for i, point in enumerate(points, start=1)]


def reciprocal_rank_fusion(rankings: list, k: int = RRF_K) -> list:
    """
    Combine several ranked lists of ids (each best first) into one.
    An id scores 1 / (k + position) in every list it appears in; the scores are added up.
    Returns [(id, score)], best first. Ties go to the id with the better single position, then by id.
    """
    scores, best_position = {}, {}
    for ranking in rankings:
        for position, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + position)
            best_position[item] = min(best_position.get(item, position), position)
    return sorted(scores.items(), key=lambda pair: (-round(pair[1], 12), best_position[pair[0]], pair[0]))


def search(query: str, library: str, top_k: int = 5, filters: dict | None = None,
           candidate_pool: int = CANDIDATE_POOL, client=None, model=None) -> list:
    """
    Hybrid search: meaning-based and exact-word results, fused with Reciprocal Rank Fusion.
    Returns [{"rank", "score", "chunk", "semantic_rank", "keyword_rank"}], best first.
    `score` is the fused score (not comparable to cosine or BM25 scores); the two *_rank
    fields show where each searcher placed the chunk (None = it did not return it).
    """
    if top_k <= 0 or not query.strip():
        return []
    client = client or QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    if not collection_ready(client, collection_name({"library": library})):
        return []  # reported once here, instead of once per searcher

    semantic = semantic_search(query, library, top_k=candidate_pool, filters=filters, client=client, model=model)
    keyword = keyword_search(query, library, top_k=candidate_pool, filters=filters, client=client)

    chunk_by_id = {r["chunk"]["chunk_id"]: r["chunk"] for r in semantic + keyword}
    semantic_rank = {r["chunk"]["chunk_id"]: r["rank"] for r in semantic}
    keyword_rank = {r["chunk"]["chunk_id"]: r["rank"] for r in keyword}

    fused = reciprocal_rank_fusion([
        [r["chunk"]["chunk_id"] for r in semantic],
        [r["chunk"]["chunk_id"] for r in keyword],
    ])
    return [
        {"rank": i, "score": score, "chunk": chunk_by_id[chunk_id],
         "semantic_rank": semantic_rank.get(chunk_id), "keyword_rank": keyword_rank.get(chunk_id)}
        for i, (chunk_id, score) in enumerate(fused[:top_k], start=1)
    ]


if __name__ == "__main__":
    build_index("configs/pandas/pandas_settings.json")
