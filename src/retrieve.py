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
import uuid
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

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

    count = client.count(collection_name=name, exact=True).count
    logger.info("Indexed %d chunks into collection '%s'", count, name)
    return count


if __name__ == "__main__":
    build_index("configs/pandas/pandas_settings.json")
