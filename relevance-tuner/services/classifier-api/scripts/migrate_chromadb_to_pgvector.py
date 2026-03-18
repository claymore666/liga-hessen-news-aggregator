#!/usr/bin/env python3
"""
Migrate ChromaDB data to pgvector tables.

Reads embeddings, documents, and metadata from ChromaDB collections
and inserts them into PostgreSQL pgvector tables. No re-embedding needed.

Usage:
    # Mount ChromaDB volumes and run against PostgreSQL
    python scripts/migrate_chromadb_to_pgvector.py \
        --vectordb /path/to/vectordb \
        --duplicatedb /path/to/duplicatedb \
        --database-url postgresql://liga:PASSWORD@localhost:5432/liga_news

    # Dry run (count items only, don't insert)
    python scripts/migrate_chromadb_to_pgvector.py --dry-run ...
"""

import argparse
import json
import sys
import time

import chromadb
import numpy as np
import psycopg2
from chromadb.config import Settings
from pgvector.psycopg2 import register_vector


def extract_from_chromadb(persist_dir: str, collection_name: str) -> dict:
    """Extract all data from a ChromaDB collection."""
    client = chromadb.PersistentClient(
        path=persist_dir,
        settings=Settings(anonymized_telemetry=False),
    )
    collection = client.get_collection(collection_name)
    total = collection.count()
    print(f"  Collection '{collection_name}': {total} items")

    if total == 0:
        return {"ids": [], "embeddings": [], "metadatas": [], "documents": []}

    # Extract in batches (ChromaDB has internal limits)
    all_data = {"ids": [], "embeddings": [], "metadatas": [], "documents": []}
    batch_size = 5000
    offset = 0

    while offset < total:
        batch = collection.get(
            limit=batch_size,
            offset=offset,
            include=["embeddings", "metadatas", "documents"],
        )
        all_data["ids"].extend(batch["ids"])
        all_data["embeddings"].extend(batch["embeddings"])
        all_data["metadatas"].extend(batch["metadatas"])
        all_data["documents"].extend(batch["documents"] or [""] * len(batch["ids"]))
        offset += batch_size
        print(f"  Extracted {min(offset, total)}/{total} items...")

    return all_data


def insert_into_pgvector(conn, table_name: str, data: dict, batch_size: int = 500):
    """Insert extracted ChromaDB data into a pgvector table."""
    cur = conn.cursor()

    # Create table if not exists
    cur.execute(f"""
        CREATE EXTENSION IF NOT EXISTS vector;
        CREATE TABLE IF NOT EXISTS {table_name} (
            item_id TEXT PRIMARY KEY,
            embedding vector(768) NOT NULL,
            document TEXT,
            metadata JSONB DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    conn.commit()

    total = len(data["ids"])
    inserted = 0
    skipped = 0

    for i in range(0, total, batch_size):
        batch_ids = data["ids"][i:i + batch_size]
        batch_embeddings = data["embeddings"][i:i + batch_size]
        batch_metadatas = data["metadatas"][i:i + batch_size]
        batch_documents = data["documents"][i:i + batch_size]

        for j, item_id in enumerate(batch_ids):
            embedding = np.array(batch_embeddings[j], dtype=np.float32)
            metadata = batch_metadatas[j] or {}
            document = batch_documents[j] or ""

            try:
                cur.execute(
                    f"""
                    INSERT INTO {table_name} (item_id, embedding, document, metadata)
                    VALUES (%s, %s, %s, %s::jsonb)
                    ON CONFLICT (item_id) DO NOTHING
                    """,
                    (item_id, embedding, document, json.dumps(metadata)),
                )
                if cur.rowcount > 0:
                    inserted += 1
                else:
                    skipped += 1
            except Exception as e:
                print(f"  Error inserting {item_id}: {e}")
                conn.rollback()
                continue

        conn.commit()
        print(f"  Inserted {min(i + batch_size, total)}/{total} (new: {inserted}, skipped: {skipped})...")

    return inserted, skipped


def create_indexes(conn, table_name: str):
    """Create HNSW and GIN indexes on the table."""
    cur = conn.cursor()
    print(f"  Creating HNSW index on {table_name}...")
    cur.execute(f"""
        CREATE INDEX IF NOT EXISTS idx_{table_name}_embedding
        ON {table_name} USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)
    print(f"  Creating GIN index on {table_name}...")
    cur.execute(f"""
        CREATE INDEX IF NOT EXISTS idx_{table_name}_metadata
        ON {table_name} USING GIN (metadata)
    """)
    conn.commit()
    print(f"  Running ANALYZE on {table_name}...")
    cur.execute(f"ANALYZE {table_name}")
    conn.commit()


def main():
    parser = argparse.ArgumentParser(description="Migrate ChromaDB to pgvector")
    parser.add_argument("--vectordb", required=True, help="Path to ChromaDB vectordb directory")
    parser.add_argument("--duplicatedb", required=True, help="Path to ChromaDB duplicatedb directory")
    parser.add_argument("--database-url", required=True, help="PostgreSQL connection string")
    parser.add_argument("--dry-run", action="store_true", help="Only count items, don't insert")
    args = parser.parse_args()

    print("=" * 60)
    print("ChromaDB to pgvector Migration")
    print("=" * 60)

    # Extract from ChromaDB
    print("\n1. Extracting from ChromaDB search store...")
    search_data = extract_from_chromadb(args.vectordb, "news_items")

    print("\n2. Extracting from ChromaDB duplicate store...")
    dup_data = extract_from_chromadb(args.duplicatedb, "duplicate_detection")

    if args.dry_run:
        print(f"\nDry run: {len(search_data['ids'])} search items, {len(dup_data['ids'])} duplicate items")
        print("No data inserted.")
        return

    # Connect to PostgreSQL
    print(f"\n3. Connecting to PostgreSQL...")
    conn = psycopg2.connect(args.database_url)
    register_vector(conn)

    # Insert search data
    print(f"\n4. Inserting {len(search_data['ids'])} items into vector_search...")
    start = time.time()
    s_inserted, s_skipped = insert_into_pgvector(conn, "vector_search", search_data)
    print(f"  Done in {time.time() - start:.1f}s: {s_inserted} inserted, {s_skipped} skipped")

    # Insert duplicate data
    print(f"\n5. Inserting {len(dup_data['ids'])} items into vector_duplicates...")
    start = time.time()
    d_inserted, d_skipped = insert_into_pgvector(conn, "vector_duplicates", dup_data)
    print(f"  Done in {time.time() - start:.1f}s: {d_inserted} inserted, {d_skipped} skipped")

    # Create indexes
    print(f"\n6. Creating indexes...")
    create_indexes(conn, "vector_search")
    create_indexes(conn, "vector_duplicates")

    # Verify
    print(f"\n7. Verification:")
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM vector_search")
    vs_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM vector_duplicates")
    ds_count = cur.fetchone()[0]
    print(f"  vector_search: {vs_count} items (expected {len(search_data['ids'])})")
    print(f"  vector_duplicates: {ds_count} items (expected {len(dup_data['ids'])})")

    if vs_count == len(search_data["ids"]) and ds_count == len(dup_data["ids"]):
        print("\n  ✓ Migration successful!")
    else:
        print("\n  ⚠ Count mismatch — verify manually")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
