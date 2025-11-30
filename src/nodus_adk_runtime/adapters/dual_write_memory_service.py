"""
Dual-Write Memory Service.
Writes to BOTH DatabaseMemoryService AND OpenMemory (via MCP).
Reads only from DatabaseMemoryService (fast for PreloadMemoryTool).

OpenMemory writes are batched and processed in background every 5 minutes.
"""

import structlog
import asyncio
from typing import Any, Optional, List
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
    - WRITE: Both ADK Memory (Postgres) AND OpenMemory (via MCP in background)
    - READ: Only ADK Memory (fast, for PreloadMemoryTool)
    
    OpenMemory interactions are batched and processed every 5 minutes in background.
    
    Features:
    - Automatic LLM-based sector classification (multilingual)
    - Graceful fallback to OpenMemory's regex-based classification
    - Background batch processing to avoid blocking the agent
    """
    
    def __init__(
        self,
        adk_memory: BaseMemoryService,
        mcp_adapter: Any,
        user_context: UserContext,
        batch_interval_seconds: int = 300,  # 5 minuts per defecte
    ):
        """
        Args:
            adk_memory: Primary memory service (DatabaseMemoryService)
            mcp_adapter: Adapter to communicate with MCP Gateway
            user_context: User context for auth headers in MCP calls
            batch_interval_seconds: Interval for batch processing (default 300s = 5min)
        """
        self.adk_memory = adk_memory
        self.mcp_adapter = mcp_adapter
        self.user_context = user_context
        self.batch_interval = batch_interval_seconds
        
        # Initialize LiteLLM client for sector classification
        self.llm_client = AsyncOpenAI(
            api_key=settings.litellm_proxy_api_key,
            base_url=f"{settings.litellm_proxy_api_base}/v1",
        )
        
        # Queue for batching OpenMemory writes
        self.memory_queue: asyncio.Queue[Session] = asyncio.Queue()
        self._batch_task: Optional[asyncio.Task] = None
        self._shutdown = False
        
        logger.info(
            "DualWriteMemoryService initialized with background batching",
            batch_interval_seconds=batch_interval_seconds
        )
    
    def start_background_processor(self):
        """Start the background batch processor."""
        if self._batch_task is None or self._batch_task.done():
            self._batch_task = asyncio.create_task(self._background_batch_processor())
            logger.info("Background batch processor started")
    
    async def stop_background_processor(self):
        """Stop the background batch processor gracefully."""
        self._shutdown = True
        if self._batch_task and not self._batch_task.done():
            await self._batch_task
            logger.info("Background batch processor stopped")
    
    async def _background_batch_processor(self):
        """
        Background task that processes the memory queue every 5 minutes.
        Runs continuously until shutdown.
        """
        logger.info("Background batch processor running", interval_seconds=self.batch_interval)
        
        while not self._shutdown:
            try:
                # Esperar l'interval (5 minuts)
                await asyncio.sleep(self.batch_interval)
                
                # Processar totes les sessions encuades
                sessions_to_process: List[Session] = []
                while not self.memory_queue.empty():
                    try:
                        session = self.memory_queue.get_nowait()
                        sessions_to_process.append(session)
                    except asyncio.QueueEmpty:
                        break
                
                if sessions_to_process:
                    logger.info(
                        "Processing batch of sessions for OpenMemory",
                        batch_size=len(sessions_to_process)
                    )
                    await self._process_batch(sessions_to_process)
                else:
                    logger.debug("No sessions to process in this batch")
                    
            except asyncio.CancelledError:
                logger.info("Background processor cancelled")
                break
            except Exception as e:
                logger.error(
                    "Error in background batch processor (will retry)",
                    error=str(e),
                    error_type=type(e).__name__
                )
                # No morir, continuar processant
                await asyncio.sleep(10)  # Backoff curt
    
    async def _process_batch(self, sessions: List[Session]):
        """Process a batch of sessions to OpenMemory."""
        success_count = 0
        error_count = 0
        
        for session in sessions:
            try:
                await self._save_to_openmemory(session)
                success_count += 1
            except Exception as e:
                error_count += 1
                logger.warning(
                    "Failed to save session to OpenMemory (non-fatal)",
                    error=str(e),
                    session_id=session.id
                )
        
        logger.info(
            "Batch processing completed",
            total=len(sessions),
            success=success_count,
            errors=error_count
        )
    
    async def add_session_to_memory(self, session: Session):
        """
        DUAL WRITE: Save to both ADK Memory AND OpenMemory.
        
        ADK Memory saves always (critical path, blocking).
        OpenMemory saves in background (non-blocking, batched every 5 minutes).
        """
        # 1. Save to ADK Memory (Postgres) - MUST succeed (blocking)
        await self.adk_memory.add_session_to_memory(session)
        logger.info(
            "Session saved to ADK Memory",
            session_id=session.id,
            events=len(session.events)
        )
        
        # 2. Enqueue for OpenMemory background processing (non-blocking)
        await self.memory_queue.put(session)
        logger.info(
            "Session enqueued for OpenMemory background processing",
            session_id=session.id,
            queue_size=self.memory_queue.qsize()
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
        """Cleanup resources and process remaining queue."""
        # Process remaining sessions in queue before closing
        if not self.memory_queue.empty():
            logger.info("Processing remaining sessions before shutdown", queue_size=self.memory_queue.qsize())
            sessions_to_process: List[Session] = []
            while not self.memory_queue.empty():
                try:
                    session = self.memory_queue.get_nowait()
                    sessions_to_process.append(session)
                except asyncio.QueueEmpty:
                    break
            if sessions_to_process:
                await self._process_batch(sessions_to_process)
        
        # Stop background processor
        await self.stop_background_processor()
        
        # Close ADK memory
        if hasattr(self.adk_memory, 'close'):
            await self.adk_memory.close()
