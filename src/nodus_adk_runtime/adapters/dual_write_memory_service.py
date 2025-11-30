"""
Dual-Write Memory Service.
Writes to BOTH DatabaseMemoryService AND OpenMemory (via MCP).
Reads only from DatabaseMemoryService (fast for PreloadMemoryTool).
"""

import structlog
from typing import Any, Optional
from google.adk.memory.base_memory_service import (
    BaseMemoryService,
    SearchMemoryResponse
)
from google.adk.sessions.session import Session
from ..middleware.auth import UserContext
from openai import AsyncOpenAI
from ..config import settings

logger = structlog.get_logger()


class DualWriteMemoryService(BaseMemoryService):
    """
    Dual-write memory service:
    - WRITE: Both ADK Memory (Postgres) AND OpenMemory (via MCP)
    - READ: Only ADK Memory (fast, for PreloadMemoryTool)
    
    OpenMemory interactions are fully mediated by the MCP Gateway.
    
    Features:
    - Automatic LLM-based sector classification (multilingual)
    - Graceful fallback to OpenMemory's regex-based classification
    """
    
    def __init__(
        self,
        adk_memory: BaseMemoryService,
        mcp_adapter: Any,
        user_context: UserContext,
    ):
        """
        Args:
            adk_memory: Primary memory service (DatabaseMemoryService)
            mcp_adapter: Adapter to communicate with MCP Gateway
            user_context: User context for auth headers in MCP calls
        """
        self.adk_memory = adk_memory
        self.mcp_adapter = mcp_adapter
        self.user_context = user_context
        
        # Initialize LiteLLM client for sector classification
        self.llm_client = AsyncOpenAI(
            api_key=settings.litellm_proxy_api_key,
            base_url=f"{settings.litellm_proxy_api_base}/v1",
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
        
        # 2. Save to OpenMemory (async, best-effort) via MCP
        try:
            await self._save_to_openmemory(session)
            logger.info(
                "Session saved to OpenMemory via MCP",
                session_id=session.id,
                events=len(session.events)
            )
        except Exception as e:
            # Don't fail if OpenMemory is down or MCP call fails
            logger.warning(
                "Failed to save to OpenMemory (non-fatal)",
                error=str(e),
                session_id=session.id
            )
    
    async def _save_to_openmemory(self, session: Session):
        """Save session events to OpenMemory using MCP 'store' tool."""
        # user_id is explicitly passed as "{tenant_id}:{user_id}" for multi-tenancy
        
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
            
            # Store via OpenMemory MCP Tool
            # Tool name is 'openmemory_store'
            result = await self.mcp_adapter.call_tool(
                server="openmemory",
                tool="openmemory_store", 
                args={
                    "content": content,
                    "user_id": f"{self.user_context.tenant_id}:{self.user_context.sub}",
                    "tags": [
                        f"session:{session.id}",
                        f"app:{session.app_name}",
                        f"author:{event.author or 'unknown'}",
                    ],
                    # Optional metadata if supported
                    "metadata": {
                        "session_id": session.id,
                        "author": event.author or 'unknown',
                        "timestamp": str(event.timestamp),
                        "source": "adk",
                    }
                },
                context=self.user_context
            )
            
            if result.get("status") == "error":
                raise Exception(f"MCP Tool Error: {result.get('error')}")
    
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
        
        OpenMemory reads are via MCP tools (openmemory_query) invoked by the Agent.
        """
        return await self.adk_memory.search_memory(
            app_name=app_name,
            user_id=user_id,
            query=query,
            limit=limit,
        )
    
    async def close(self):
        """Cleanup resources."""
        if hasattr(self.adk_memory, 'close'):
            await self.adk_memory.close()
