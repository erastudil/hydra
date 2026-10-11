"""
Integration test suite for Hydra Desktop Local Vector Indexer.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import os
import tempfile
import pytest

from desktop.local_vector_indexer import (
    LocalVectorIndexer,
    VectorDocumentChunk,
    SearchResult,
    vector_dot,
    vector_norm,
    vector_normalize,
    cosine_similarity,
    generate_text_embedding,
    get_local_vector_indexer,
    reset_local_vector_indexer,
)


@pytest.fixture
def indexer():
    idx = LocalVectorIndexer(embedding_dim=32)
    yield idx
    idx.clear()


def test_pure_python_vector_math():
    """Verify dot product, Euclidean norm, and cosine similarity calculations."""
    v1 = [1.0, 0.0, 0.0]
    v2 = [0.0, 1.0, 0.0]
    v3 = [2.0, 0.0, 0.0]
    v_neg = [-1.0, 0.0, 0.0]

    assert vector_dot(v1, v2) == 0.0
    assert vector_dot(v1, v3) == 2.0
    assert vector_norm(v3) == 2.0

    # Orthogonal vectors: 0.0
    assert cosine_similarity(v1, v2) == 0.0
    # Collinear vectors: 1.0
    assert abs(cosine_similarity(v1, v3) - 1.0) < 1e-6
    # Opposing vectors: -1.0
    assert abs(cosine_similarity(v1, v_neg) - (-1.0)) < 1e-6


def test_deterministic_text_embedding_properties():
    """Verify generated embeddings are unit-normalized and deterministic."""
    e1 = generate_text_embedding("hydra sovereign engine", dim=32)
    e2 = generate_text_embedding("hydra sovereign engine", dim=32)
    e3 = generate_text_embedding("completely unrelated cooking recipe", dim=32)

    assert len(e1) == 32
    # Unit norm
    assert abs(vector_norm(e1) - 1.0) < 1e-4
    # Determinism
    assert e1 == e2

    # Semantic similarity: identical is 1.0, unrelated is significantly lower
    sim_self = cosine_similarity(e1, e2)
    sim_diff = cosine_similarity(e1, e3)
    assert abs(sim_self - 1.0) < 1e-6
    assert sim_diff < sim_self


def test_add_chunk_and_top_k_search(indexer: LocalVectorIndexer):
    """Verify indexing document chunks and retrieving top-k nearest neighbors."""
    indexer.add_chunk(
        "doc_agent",
        "Autonomous agent loop execution with tools",
        metadata={"category": "agent"},
    )
    indexer.add_chunk(
        "doc_desktop",
        "Hydra desktop computer use and web desk interface",
        metadata={"category": "desktop"},
    )
    indexer.add_chunk(
        "doc_model",
        "Multi-model routing and frontier model cascade",
        metadata={"category": "model"},
    )

    assert indexer.total_chunks == 3

    # Query matching desktop
    results = indexer.search("desktop web desk interface", top_k=2)
    assert len(results) == 2
    assert results[0].chunk_id == "doc_desktop"
    assert results[0].score > results[1].score


def test_metadata_filtering(indexer: LocalVectorIndexer):
    """Verify metadata filtering restricts retrieval results to matching tags."""
    indexer.add_chunk("d1", "Python code engineering", metadata={"lang": "python", "level": "advanced"})
    indexer.add_chunk("d2", "Python basic tutorial", metadata={"lang": "python", "level": "beginner"})
    indexer.add_chunk("d3", "Rust systems programming", metadata={"lang": "rust", "level": "advanced"})

    # Filter by lang=python and level=advanced
    res = indexer.search(
        "code programming",
        top_k=5,
        filter_metadata={"lang": "python", "level": "advanced"},
    )

    assert len(res) == 1
    assert res[0].chunk_id == "d1"
    assert res[0].metadata["lang"] == "python"


def test_json_export_and_disk_persistence(indexer: LocalVectorIndexer):
    """Verify saving to disk and loading index state from file."""
    indexer.add_chunk("k1", "Sample document knowledge", metadata={"tag": "v1"})

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        tmp_path = tf.name

    try:
        indexer.save_to_disk(tmp_path)
        assert os.path.getsize(tmp_path) > 0

        # Load into new indexer instance
        new_idx = LocalVectorIndexer()
        new_idx.load_from_disk(tmp_path)
        assert new_idx.total_chunks == 1
        assert new_idx.get_chunk("k1") is not None
        assert new_idx.get_chunk("k1").text == "Sample document knowledge"
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_global_singleton_vector_indexer():
    """Verify singleton lifecycle for LocalVectorIndexer."""
    i1 = get_local_vector_indexer()
    i2 = get_local_vector_indexer()
    assert i1 is i2

    i3 = reset_local_vector_indexer()
    assert i3 is not i1
    assert get_local_vector_indexer() is i3
