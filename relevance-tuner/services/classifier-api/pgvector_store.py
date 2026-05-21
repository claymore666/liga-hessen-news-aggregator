"""
pgvector-backed vector store replacing ChromaDB.

Uses asyncpg for async PostgreSQL access with pgvector extension
for cosine similarity search. Two instances are created:
- vector_search table: nomic embeddings for semantic search + classification
- vector_duplicates table: paraphrase embeddings for duplicate detection
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg
import numpy as np
from pgvector.asyncpg import register_vector

logger = logging.getLogger(__name__)


class PgVectorStore:
    """
    pgvector-backed vector store for semantic search and similarity.

    Replaces ChromaDB PersistentClient. All data lives in PostgreSQL,
    eliminating the need for in-process HNSW indexes and SQLite files.
    """

    def __init__(
        self,
        dsn: str,
        table_name: str,
        embedder,
        embedding_dim: int = 768,
    ):
        """
        Initialize the store (call .init() to create pool and tables).

        Args:
            dsn: PostgreSQL connection string
            table_name: Table name (e.g., 'vector_search' or 'vector_duplicates')
            embedder: OllamaEmbedder instance (NomicV2Embedder or ParaphraseEmbedder)
            embedding_dim: Embedding dimension (default 768)
        """
        self.dsn = dsn
        self.table_name = table_name
        self.embedder = embedder
        self.embedding_dim = embedding_dim
        self.pool: asyncpg.Pool | None = None

    async def init(self):
        """Create connection pool, extension, table, and indexes."""
        self.pool = await asyncpg.create_pool(
            self.dsn,
            min_size=2,
            max_size=10,
            init=register_vector,
        )

        async with self.pool.acquire() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
                    item_id TEXT PRIMARY KEY,
                    embedding vector({self.embedding_dim}) NOT NULL,
                    document TEXT,
                    metadata JSONB DEFAULT '{{}}'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            count = await conn.fetchval(f"SELECT COUNT(*) FROM {self.table_name}")

            # Create IVFFlat index if enough data exists
            # IVFFlat uses less memory than HNSW, suitable for constrained environments
            if count >= 1000:
                await conn.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{self.table_name}_embedding
                    ON {self.table_name} USING ivfflat (embedding vector_cosine_ops)
                    WITH (lists = 100)
                """)
            # GIN index for metadata filtering
            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{self.table_name}_metadata
                ON {self.table_name} USING GIN (metadata)
            """)
            logger.info(f"PgVectorStore '{self.table_name}' initialized: {count} items")

    async def close(self):
        """Close the connection pool."""
        if self.pool:
            await self.pool.close()
            self.pool = None

    async def count(self) -> int:
        """Get the number of items in the store."""
        async with self.pool.acquire() as conn:
            return await conn.fetchval(f"SELECT COUNT(*) FROM {self.table_name}")

    async def add_item(
        self,
        item_id: str,
        title: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> bool:
        """
        Add an item to the store (skip if already exists).

        Returns True if newly added, False if already existed.
        """
        # Check existence first (for return value)
        async with self.pool.acquire() as conn:
            exists = await conn.fetchval(
                f"SELECT EXISTS(SELECT 1 FROM {self.table_name} WHERE item_id = $1)",
                item_id,
            )
            if exists:
                return False

        # Generate embedding
        text = f"{title} {content}"
        embeddings = await self.embedder.encode([text], show_progress_bar=False)
        embedding = np.array(embeddings[0], dtype=np.float32)

        # Prepare metadata
        meta = metadata or {}
        meta["title"] = title[:500]
        # Normalize fetched_at to epoch float for range queries
        meta = self._normalize_fetched_at(meta)
        # Remove None values
        meta = {k: v for k, v in meta.items() if v is not None}

        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.table_name} (item_id, embedding, document, metadata)
                VALUES ($1, $2, $3, $4::jsonb)
                ON CONFLICT (item_id) DO NOTHING
                """,
                item_id,
                embedding,
                text[:2000],
                json.dumps(meta),
            )

        return True

    async def add_items_batch(self, items: list[dict]) -> int:
        """
        Add multiple items in batch. Skips existing items.

        Returns the number of newly added items.
        """
        if not items:
            return 0

        # Check which IDs already exist
        ids = [str(item["id"]) for item in items]
        async with self.pool.acquire() as conn:
            existing_rows = await conn.fetch(
                f"SELECT item_id FROM {self.table_name} WHERE item_id = ANY($1)",
                ids,
            )
        existing_ids = {r["item_id"] for r in existing_rows}
        new_items = [item for item in items if str(item["id"]) not in existing_ids]

        if not new_items:
            return 0

        # Generate embeddings in batch
        texts = [f"{item['title']} {item['content']}" for item in new_items]
        embeddings_list = await self.embedder.encode(texts, show_progress_bar=len(texts) > 10)

        # Batch insert
        records = []
        for idx, item in enumerate(new_items):
            meta = item.get("metadata", {}) or {}
            meta["title"] = item["title"][:500]
            meta = {k: v for k, v in meta.items() if v is not None}
            meta = self._normalize_fetched_at(meta)
            embedding = np.array(embeddings_list[idx], dtype=np.float32)
            records.append((
                str(item["id"]),
                embedding,
                texts[idx][:2000],
                json.dumps(meta),
            ))

        async with self.pool.acquire() as conn:
            await conn.executemany(
                f"""
                INSERT INTO {self.table_name} (item_id, embedding, document, metadata)
                VALUES ($1, $2, $3, $4::jsonb)
                ON CONFLICT (item_id) DO NOTHING
                """,
                records,
            )

        return len(new_items)

    async def search(
        self,
        query: str,
        n_results: int = 10,
        filter_metadata: Optional[dict] = None,
    ) -> list[dict]:
        """
        Cosine similarity search.

        Args:
            query: Search query text
            n_results: Number of results to return
            filter_metadata: Optional JSONB containment filter (e.g., {"source": "hr"})
        """
        embeddings = await self.embedder.encode([query], show_progress_bar=False)
        embedding = np.array(embeddings[0], dtype=np.float32)

        where_clause = ""
        params = [embedding, n_results]
        if filter_metadata:
            where_clause = f"WHERE metadata @> $3::jsonb"
            params.append(json.dumps(filter_metadata))

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT item_id, document, metadata,
                       1 - (embedding <=> $1) as similarity
                FROM {self.table_name}
                {where_clause}
                ORDER BY embedding <=> $1
                LIMIT $2
                """,
                *params,
            )

        return [
            {
                "id": r["item_id"],
                "title": json.loads(r["metadata"]).get("title", "") if r["metadata"] else "",
                "score": float(r["similarity"]),
                "metadata": json.loads(r["metadata"]) if r["metadata"] else {},
                "snippet": (r["document"] or "")[:300],
            }
            for r in rows
        ]

    async def find_similar(
        self,
        item_id: str,
        n_results: int = 5,
        exclude_same_source: bool = True,
    ) -> list[dict]:
        """Find items similar to a given item."""
        # Get the item's embedding and source
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT embedding, metadata FROM {self.table_name} WHERE item_id = $1",
                item_id,
            )

        if row is None:
            return []

        embedding = row["embedding"]
        item_meta = json.loads(row["metadata"]) if row["metadata"] else {}
        item_source = item_meta.get("source", "")

        # Search for similar (get extra for filtering)
        fetch_limit = n_results + 10

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT item_id, document, metadata,
                       1 - (embedding <=> $1) as similarity
                FROM {self.table_name}
                WHERE item_id != $2
                ORDER BY embedding <=> $1
                LIMIT $3
                """,
                embedding,
                item_id,
                fetch_limit,
            )

        results = []
        for r in rows:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
            if exclude_same_source and meta.get("source", "") == item_source:
                continue
            results.append({
                "id": r["item_id"],
                "title": meta.get("title", ""),
                "score": float(r["similarity"]),
                "metadata": meta,
                "snippet": (r["document"] or "")[:300],
            })
            if len(results) >= n_results:
                break

        return results

    async def find_duplicates(
        self,
        title: str,
        content: str,
        threshold: float = 0.75,
        n_results: int = 5,
        fetched_after: str | None = None,
    ) -> list[dict]:
        """
        Find semantically similar items that may be duplicates.

        Args:
            title: Article title
            content: Article content
            threshold: Similarity threshold (default 0.75)
            n_results: Max results to return
            fetched_after: ISO timestamp - only match items fetched after this time
        """
        text = f"{title} {content}"
        embeddings = await self.embedder.encode([text], show_progress_bar=False)
        embedding = np.array(embeddings[0], dtype=np.float32)

        # Build WHERE clause for time-bounded dedup
        where_clause = ""
        params = [embedding, n_results]
        if fetched_after:
            try:
                dt = datetime.fromisoformat(fetched_after)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                epoch = dt.timestamp()
                # Guard the cast: legacy rows may store fetched_at as an ISO
                # string instead of an epoch float, which would abort the whole
                # query with "invalid input syntax for type double precision".
                # CASE guarantees short-circuit evaluation, so the ::float cast
                # only runs on numeric-looking values; everything else (ISO
                # strings, NULL, missing key) is treated as outside the window.
                where_clause = (
                    "WHERE CASE WHEN metadata->>'fetched_at' ~ '^[0-9.]+$' "
                    "THEN (metadata->>'fetched_at')::float >= $3 "
                    "ELSE false END"
                )
                params.append(epoch)
            except (ValueError, TypeError):
                pass

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT item_id, document, metadata,
                       1 - (embedding <=> $1) as similarity
                FROM {self.table_name}
                {where_clause}
                ORDER BY embedding <=> $1
                LIMIT $2
                """,
                *params,
            )

        results = []
        for r in rows:
            score = float(r["similarity"])
            if score >= threshold:
                meta = json.loads(r["metadata"]) if r["metadata"] else {}
                results.append({
                    "id": r["item_id"],
                    "title": meta.get("title", ""),
                    "score": score,
                    "metadata": meta,
                    "snippet": (r["document"] or "")[:300],
                })

        return results

    async def delete(self, ids: list[str]) -> int:
        """Delete items by ID. Returns count of deleted items."""
        if not ids:
            return 0
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                f"DELETE FROM {self.table_name} WHERE item_id = ANY($1)",
                ids,
            )
            # result is like 'DELETE 5'
            return int(result.split()[-1])

    async def get_all_ids(self) -> list[str]:
        """Get all item IDs in the store."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(f"SELECT item_id FROM {self.table_name}")
        return [r["item_id"] for r in rows]

    async def get_all_items(self, batch_size: int = 1000) -> list[dict]:
        """Get all items for syncing (without embeddings)."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT item_id, document, metadata FROM {self.table_name}"
            )
        items = []
        for r in rows:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
            items.append({
                "id": r["item_id"],
                "title": meta.get("title", ""),
                "content": r["document"] or "",
                "metadata": meta,
            })
        return items

    def get_stats(self) -> dict:
        """Get store statistics (sync wrapper for compatibility)."""
        # This is called in sync context by some endpoints.
        # Return a placeholder — callers should use get_stats_async.
        return {"table_name": self.table_name}

    async def get_stats_async(self) -> dict:
        """Get store statistics."""
        item_count = await self.count()
        async with self.pool.acquire() as conn:
            size = await conn.fetchval(
                f"SELECT pg_total_relation_size('{self.table_name}')"
            )
        return {
            "total_items": item_count,
            "table_name": self.table_name,
            "size_bytes": size or 0,
            "model": self.embedder.model_name,
        }

    @staticmethod
    def _normalize_fetched_at(meta: dict) -> dict:
        """Convert fetched_at ISO string to epoch float for range queries."""
        if "fetched_at" in meta and isinstance(meta["fetched_at"], str) and meta["fetched_at"]:
            try:
                dt = datetime.fromisoformat(meta["fetched_at"])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                meta["fetched_at"] = dt.timestamp()
            except (ValueError, TypeError):
                del meta["fetched_at"]
        return meta
