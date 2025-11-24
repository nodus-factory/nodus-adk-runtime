"""
Memory Layer Adapter

Integrates Google ADK with Nodus Memory Layer (Postgres + Qdrant).
Provides both conversation history (Postgres) and RAG (Qdrant).
"""

from typing import Any, Dict, List, Optional
import structlog
import asyncpg

from .qdrant_memory_service import QdrantMemoryService

logger = structlog.get_logger()


class MemoryAdapter:
    """
    Adapter for Memory Layer integration.
    
    Combines:
    - Postgres: Conversation history (session messages)
    - Qdrant: RAG memory service (semantic search)
    """

    def __init__(
        self,
        database_url: str,
        qdrant_url: str,
        qdrant_api_key: Optional[str] = None,
        openai_api_key: Optional[str] = None,
    ):
        """
        Initialize memory adapter.

        Args:
            database_url: PostgreSQL connection URL
            qdrant_url: URL of the Qdrant service
            qdrant_api_key: API key for Qdrant authentication
            openai_api_key: Optional OpenAI API key for embeddings
        """
        self.database_url = database_url
        self.qdrant_memory = QdrantMemoryService(
            qdrant_url=qdrant_url,
            qdrant_api_key=qdrant_api_key,
            openai_api_key=openai_api_key,
        )
        self._db_pool: Optional[asyncpg.Pool] = None

    async def _get_db_pool(self) -> asyncpg.Pool:
        """Get or create database connection pool."""
        if self._db_pool is None:
            self._db_pool = await asyncpg.create_pool(self.database_url)
        return self._db_pool

    async def _ensure_schema(self):
        """Ensure database schema exists for conversation history."""
        pool = await self._get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id SERIAL PRIMARY KEY,
                    tenant_id VARCHAR(255),
                    user_id VARCHAR(255),
                    session_id VARCHAR(255),
                    role VARCHAR(50),
                    content TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_session_id ON conversation_messages(session_id);
                CREATE INDEX IF NOT EXISTS idx_user_id ON conversation_messages(user_id);
            """)

    async def save_message(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        role: str,
        content: str,
    ):
        """
        Save a message to conversation history (Postgres).

        Args:
            tenant_id: Tenant identifier
            user_id: User identifier
            session_id: Session identifier
            role: Message role (user/assistant)
            content: Message content
        """
        pool = await self._get_db_pool()
        await self._ensure_schema()
        
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO conversation_messages (tenant_id, user_id, session_id, role, content)
                VALUES ($1, $2, $3, $4, $5)
            """, tenant_id, user_id, session_id, role, content)
        
        logger.debug("Saved message", session_id=session_id, role=role)

    async def get_history(
        self, session_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get conversation history for a session.

        Args:
            session_id: Session identifier
            limit: Maximum number of messages

        Returns:
            List of messages with role and content
        """
        pool = await self._get_db_pool()
        await self._ensure_schema()
        
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT role, content, created_at
                FROM conversation_messages
                WHERE session_id = $1
                ORDER BY created_at ASC
                LIMIT $2
            """, session_id, limit)
        
        return [
            {
                "role": row["role"],
                "content": row["content"],
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    async def search(
        self, app_name: str, user_id: str, query: str, tenant_id: Optional[str] = None, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search user memory using RAG (Qdrant).

        Args:
            app_name: Application name
            user_id: User identifier
            query: Search query
            tenant_id: Optional tenant identifier for multi-tenancy
            limit: Maximum number of results

        Returns:
            List of memory items
        """
        logger.info("Searching memory", user_id=user_id, tenant_id=tenant_id, query=query)
        result = await self.qdrant_memory.search_memory(
            app_name=app_name,
            user_id=user_id,
            query=query,
            tenant_id=tenant_id,
        )
        
        # Convert MemoryEntry to dict
        memories = []
        for memory in result.memories:
            memories.append({
                "content": memory.content.parts[0].text if memory.content.parts else "",
                "author": memory.author,
                "timestamp": memory.timestamp,
            })
        
        return memories[:limit]

    async def store(
        self, user_id: str, content: str, metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Store item in user memory (Qdrant RAG).

        Args:
            user_id: User identifier
            content: Content to store
            metadata: Optional metadata

        Returns:
            Memory item ID
        """
        logger.info("Storing memory", user_id=user_id)
        # TODO: Implement direct storage to Qdrant if needed
        # For now, memory is stored via add_session_to_memory
        return "memory_id_placeholder"

    async def close(self):
        """Close database connections."""
        if self._db_pool:
            await self._db_pool.close()


