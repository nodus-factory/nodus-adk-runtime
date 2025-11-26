"""
Database Memory Service for ADK.
Simple Postgres-based short-term conversation memory.
"""

import asyncpg
import structlog
from typing import Optional
from datetime import datetime
from google.adk.memory.base_memory_service import (
    BaseMemoryService, 
    SearchMemoryResponse, 
    MemoryEntry
)
from google.adk.sessions.session import Session
from google.genai import types

logger = structlog.get_logger()


class DatabaseMemoryService(BaseMemoryService):
    """
    Simple conversation memory using PostgreSQL.
    
    Stores recent conversation turns for:
    - PreloadMemoryTool (automatic context loading)
    - Fast retrieval (< 10ms)
    - Short-term memory (last ~100 messages per user)
    """
    
    def __init__(self, database_url: str):
        """
        Initialize database memory service.
        
        Args:
            database_url: PostgreSQL connection URL
        """
        self.database_url = database_url
        self._pool: Optional[asyncpg.Pool] = None
    
    async def _get_pool(self) -> asyncpg.Pool:
        """Get or create database connection pool."""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self.database_url,
                min_size=2,
                max_size=10,
            )
            logger.info("Database memory pool created")
        return self._pool
    
    def _extract_text(self, event) -> str:
        """Extract text from event content."""
        if not event.content or not event.content.parts:
            return ""
        
        text_parts = []
        for part in event.content.parts:
            if hasattr(part, 'text') and part.text:
                text_parts.append(part.text)
        
        return ' '.join(text_parts)
    
    async def add_session_to_memory(self, session: Session):
        """
        Store session events to database.
        Only keeps last 10 events per session (sliding window).
        """
        pool = await self._get_pool()
        tenant_id = session.state.get('tenant_id', 'default') if hasattr(session, 'state') and session.state else 'default'
        
        # Store only recent events (last 10)
        recent_events = session.events[-10:] if len(session.events) > 10 else session.events
        
        async with pool.acquire() as conn:
            for event in recent_events:
                if not event.content:
                    continue
                
                text = self._extract_text(event)
                if not text:
                    continue
                
                # Convert timestamp to datetime
                if isinstance(event.timestamp, (int, float)):
                    ts = datetime.fromtimestamp(event.timestamp)
                elif hasattr(event.timestamp, 'timestamp'):
                    ts = datetime.fromtimestamp(event.timestamp.timestamp())
                else:
                    ts = datetime.now()
                
                await conn.execute("""
                    INSERT INTO adk_conversation_memory 
                    (session_id, user_id, tenant_id, author, content, timestamp)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (session_id, timestamp) 
                    DO UPDATE SET content = EXCLUDED.content
                """, 
                    session.id,
                    session.user_id,
                    tenant_id,
                    event.author or 'unknown',
                    text,
                    ts
                )
            
            # Cleanup: keep only last 100 messages per user
            await conn.execute("""
                DELETE FROM adk_conversation_memory
                WHERE user_id = $1
                AND id NOT IN (
                    SELECT id FROM adk_conversation_memory
                    WHERE user_id = $1
                    ORDER BY timestamp DESC
                    LIMIT 100
                )
            """, session.user_id)
        
        logger.info(
            "Session saved to database memory",
            session_id=session.id,
            events=len(recent_events),
            tenant_id=tenant_id
        )
    
    async def search_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        query: str,
        tenant_id: Optional[str] = None,
        limit: int = 3,
    ) -> SearchMemoryResponse:
        """
        Search recent conversation memory.
        Simple keyword-based search (fast, no embeddings needed).
        
        Returns top 3 most recent relevant messages.
        """
        pool = await self._get_pool()
        
        async with pool.acquire() as conn:
            # Simple keyword search (case-insensitive)
            # If query is empty, return most recent
            rows = await conn.fetch("""
                SELECT author, content, timestamp
                FROM adk_conversation_memory
                WHERE user_id = $1
                AND (
                    $2 = '' 
                    OR content ILIKE '%' || $2 || '%'
                )
                ORDER BY timestamp DESC
                LIMIT $3
            """, user_id, query.lower(), limit)
        
        memories = []
        for row in rows:
            memories.append(
                MemoryEntry(
                    content=types.Content(
                        parts=[types.Part(text=row['content'])],
                        role=row['author']
                    ),
                    author=row['author'],
                    timestamp=row['timestamp'].isoformat(),
                )
            )
        
        logger.debug(
            "Memory search completed",
            user_id=user_id,
            query=query,
            results=len(memories)
        )
        
        return SearchMemoryResponse(memories=memories)
    
    async def close(self):
        """Close database connection pool."""
        if self._pool:
            await self._pool.close()
            logger.info("Database memory pool closed")
