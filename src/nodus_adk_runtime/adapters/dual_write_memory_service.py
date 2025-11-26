"""
Dual-Write Memory Service.
Writes to BOTH DatabaseMemoryService AND OpenMemory.
Reads only from DatabaseMemoryService (fast for PreloadMemoryTool).
"""

import httpx
import structlog
from typing import Optional
from google.adk.memory.base_memory_service import (
    BaseMemoryService,
    SearchMemoryResponse
)
from google.adk.sessions.session import Session

logger = structlog.get_logger()


class DualWriteMemoryService(BaseMemoryService):
    """
    Dual-write memory service:
    - WRITE: Both ADK Memory (Postgres) AND OpenMemory (HTTP)
    - READ: Only ADK Memory (fast, for PreloadMemoryTool)
    
    OpenMemory retrieval is via MCP tools (on-demand by agent).
    """
    
    def __init__(
        self,
        adk_memory: BaseMemoryService,
        openmemory_url: str,
        tenant_id: str,
        api_key: Optional[str] = None,
    ):
        """
        Args:
            adk_memory: Primary memory service (DatabaseMemoryService)
            openmemory_url: OpenMemory HTTP endpoint
            tenant_id: Tenant ID for multi-tenancy
            api_key: Optional API key for OpenMemory
        """
        self.adk_memory = adk_memory
        self.openmemory_url = openmemory_url
        self.tenant_id = tenant_id
        self.api_key = api_key
        
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        
        self.http_client = httpx.AsyncClient(
            base_url=openmemory_url,
            timeout=10.0,
            headers=headers
        )
    
    async def add_session_to_memory(self, session: Session):
        """
        DUAL WRITE: Save to both ADK Memory AND OpenMemory.
        
        ADK Memory saves always (critical path).
        OpenMemory saves best-effort (non-blocking on failure).
        """
        # 1. Save to ADK Memory (Postgres) - MUST succeed
        await self.adk_memory.add_session_to_memory(session)
        logger.info(
            "Session saved to ADK Memory",
            session_id=session.id,
            events=len(session.events)
        )
        
        # 2. Save to OpenMemory (async, best-effort)
        try:
            await self._save_to_openmemory(session)
            logger.info(
                "Session saved to OpenMemory",
                session_id=session.id,
                events=len(session.events)
            )
        except Exception as e:
            # Don't fail if OpenMemory is down
            logger.warning(
                "Failed to save to OpenMemory (non-fatal)",
                error=str(e),
                session_id=session.id
            )
    
    async def _save_to_openmemory(self, session: Session):
        """Save session events to OpenMemory with sector classification."""
        user_id = f"{self.tenant_id}:{session.user_id}"
        
        for event in session.events:
            if not event.content or not event.content.parts:
                continue
            
            # Extract text
            text_parts = []
            for part in event.content.parts:
                if hasattr(part, 'text') and part.text:
                    text_parts.append(part.text)
            
            if not text_parts:
                continue
            
            content = ' '.join(text_parts)
            
            # Store via OpenMemory HTTP API
            # Endpoint: POST /api/memories (from nodus-memory)
            response = await self.http_client.post("/api/memories", json={
                "user_id": user_id,
                "content": content,
                "tags": [
                    f"session:{session.id}",
                    f"app:{session.app_name}",
                    f"author:{event.author or 'unknown'}",
                ],
                "metadata": {
                    "session_id": session.id,
                    "tenant_id": self.tenant_id,
                    "author": event.author or 'unknown',
                    "timestamp": str(event.timestamp),
                    "source": "adk",
                }
            })
            
            response.raise_for_status()
    
    async def search_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        query: str,
        limit: int = 3,
    ) -> SearchMemoryResponse:
        """
        READ: Only from ADK Memory (fast for PreloadMemoryTool).
        
        OpenMemory reads are via MCP tools (openmemory_query).
        """
        return await self.adk_memory.search_memory(
            app_name=app_name,
            user_id=user_id,
            query=query,
            limit=limit,
        )
    
    async def close(self):
        """Cleanup resources."""
        await self.http_client.aclose()
        if hasattr(self.adk_memory, 'close'):
            await self.adk_memory.close()
