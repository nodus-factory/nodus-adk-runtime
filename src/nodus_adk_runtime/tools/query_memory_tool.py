"""
Query Memory Tool

Tool for querying personal conversation memory (CAPA 2) from Qdrant.
Stores episodic and semantic memories from past conversations.
"""

from typing import Any, Dict, Optional
import structlog
from google.adk.tools.base_tool import BaseTool
from google.genai import types
from typing_extensions import override
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, Range, MatchValue
from openai import AsyncOpenAI
from datetime import datetime, timedelta
from nodus_adk_runtime.config import settings

logger = structlog.get_logger()


class QueryMemoryTool(BaseTool):
    """
    Tool to query personal conversation memory (CAPA 2: Mid-term memory).
    
    Searches in user-specific memory collection:
    - memory_t_<tenant>_<user_id>: Personal conversation history, preferences, facts
    
    Supports temporal filtering for time-based queries.
    """

    def __init__(
        self,
        qdrant_url: str,
        qdrant_api_key: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        tenant_id: str = "default",
        user_id: str = "1",
    ):
        """
        Initialize the memory query tool.
        
        Args:
            qdrant_url: URL of Qdrant service
            qdrant_api_key: Optional Qdrant API key
            openai_api_key: OpenAI API key for embeddings
            tenant_id: Tenant identifier
            user_id: User identifier
        """
        super().__init__(
            name="query_memory",
            description=(
                "Search your personal long-term memory for past conversations, "
                "preferences, and facts you've shared. Use this when the user asks "
                "about past discussions, their preferences, or information from "
                "previous conversations. Supports time-based filtering."
            ),
        )
        self.qdrant_url = qdrant_url
        self.qdrant_api_key = qdrant_api_key
        self.openai_api_key = openai_api_key
        self.tenant_id = tenant_id
        self.user_id = user_id
        
        # Initialize Qdrant client
        self.client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
        
        # Configure OpenAI to use LiteLLM proxy for embeddings
        if openai_api_key:
            self.openai_client = AsyncOpenAI(
                api_key=openai_api_key,
                base_url=settings.litellm_proxy_api_base + "/v1",
            )
        else:
            self.openai_client = None
        
        logger.info(
            "QueryMemoryTool initialized",
            tenant_id=tenant_id,
            user_id=user_id,
            qdrant_url=qdrant_url,
        )

    def _get_collection_name(self) -> str:
        """Generate memory collection name with tenant and user awareness."""
        # Format: memory_t_<tenant>_<user_id>
        tenant_name = self.tenant_id.replace("t_", "") if self.tenant_id.startswith("t_") else self.tenant_id
        return f"memory_t_{tenant_name}_{self.user_id}"

    async def _get_embedding(self, text: str) -> list[float]:
        """Generate embedding using OpenAI via LiteLLM."""
        if not self.openai_client:
            raise ValueError("OpenAI client not initialized")
        try:
            response = await self.openai_client.embeddings.create(
                model="text-embedding-3-small",
                input=text,
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error("Failed to generate embedding", error=str(e))
            raise

    def _build_time_filter(self, time_range: Optional[str]) -> Optional[Filter]:
        """
        Build Qdrant filter for time-based queries.
        
        Args:
            time_range: 'last_day', 'last_week', 'last_month', or None
            
        Returns:
            Qdrant Filter object or None
        """
        if not time_range:
            return None
        
        now = datetime.now()
        time_deltas = {
            'last_day': timedelta(days=1),
            'last_week': timedelta(days=7),
            'last_month': timedelta(days=30),
        }
        
        delta = time_deltas.get(time_range)
        if not delta:
            return None
        
        cutoff_time = now - delta
        cutoff_timestamp = int(cutoff_time.timestamp() * 1000)  # milliseconds
        
        return Filter(
            must=[
                FieldCondition(
                    key="created_at",
                    range=Range(
                        gte=cutoff_timestamp,
                    )
                )
            ]
        )

    @override
    def _get_declaration(self) -> types.FunctionDeclaration:
        """Define the tool's function signature for the LLM."""
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(
                        type=types.Type.STRING,
                        description="The search query to find relevant memories",
                    ),
                    "limit": types.Schema(
                        type=types.Type.INTEGER,
                        description="Maximum number of results to return (default: 5)",
                    ),
                    "time_range": types.Schema(
                        type=types.Type.STRING,
                        description="Optional time filter: 'last_day', 'last_week', 'last_month'",
                    ),
                },
                required=["query"],
            ),
        )

    @override
    async def run_async(self, *, args: Dict[str, Any], tool_context: Any) -> Dict[str, Any]:
        """
        Execute the memory search.
        
        Args:
            args: Dictionary with 'query', optional 'limit', optional 'time_range'
            tool_context: ADK tool invocation context
            
        Returns:
            Search results with memories and metadata
        """
        query = args.get("query", "")
        limit = args.get("limit", 5)
        time_range = args.get("time_range")
        
        if not query:
            return {"status": "error", "message": "Query is required"}
        
        collection_name = self._get_collection_name()
        
        logger.info(
            "Searching personal memory",
            query=query,
            limit=limit,
            time_range=time_range,
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            collection=collection_name,
        )
        
        try:
            # Check if collection exists
            collections = self.client.get_collections()
            collection_names = [c.name for c in collections.collections]
            
            if collection_name not in collection_names:
                logger.info(
                    "Memory collection does not exist yet",
                    collection=collection_name,
                )
                return {
                    "status": "success",
                    "message": "No memories stored yet",
                    "results": [],
                }
            
            # Generate query embedding
            query_embedding = await self._get_embedding(query)
            
            # Build time filter if specified
            query_filter = self._build_time_filter(time_range)
            
            # Search memory collection
            search_response = self.client.query_points(
                collection_name=collection_name,
                query=query_embedding,
                query_filter=query_filter,
                limit=limit * 2,  # Get more to filter by score
            )
            
            # Apply minimum score threshold
            MIN_SCORE_THRESHOLD = 0.40
            
            results = []
            for point in search_response.points:
                if point.score < MIN_SCORE_THRESHOLD:
                    continue
                
                payload = point.payload
                results.append({
                    "content": payload.get("content", ""),
                    "created_at": payload.get("created_at", ""),
                    "session_id": payload.get("session_id", ""),
                    "author": payload.get("author", "unknown"),
                    "score": point.score,
                    "metadata": payload,
                })
            
            # Limit final results
            results = results[:limit]
            
            logger.info(
                "Memory search completed",
                query=query,
                collection=collection_name,
                total_results=len(search_response.points),
                filtered_results=len(results),
                top_score=results[0]["score"] if results else 0,
                threshold=MIN_SCORE_THRESHOLD,
            )
            
            if not results:
                return {
                    "status": "success",
                    "message": "No relevant memories found for this query",
                    "results": [],
                }
            
            return {
                "status": "success",
                "results": results,
                "total": len(results),
            }
            
        except Exception as e:
            logger.error("Memory search error", error=str(e), query=query)
            return {
                "status": "error",
                "message": f"Search failed: {str(e)}",
            }

