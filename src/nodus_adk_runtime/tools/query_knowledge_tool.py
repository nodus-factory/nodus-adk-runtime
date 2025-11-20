"""
Query Knowledge Base Tool

Tool for querying the Backoffice knowledge base (uploaded documents)
with proper tenant and user isolation.
"""

from typing import Any, Dict, Optional
import structlog
from google.adk.tools.base_tool import BaseTool
from google.genai import types
from typing_extensions import override
from qdrant_client import QdrantClient
from openai import AsyncOpenAI
import hashlib

logger = structlog.get_logger()


class QueryKnowledgeBaseTool(BaseTool):
    """
    Tool to query the Backoffice knowledge base (RAG).
    
    Searches in tenant-specific and user-specific document collections:
    - knowledge_t_<tenant>_0: General tenant documents
    - knowledge_t_<tenant>_<user_id>: User-specific documents
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
        Initialize the knowledge base query tool.
        
        Args:
            qdrant_url: URL of Qdrant service
            qdrant_api_key: Optional Qdrant API key
            openai_api_key: OpenAI API key for embeddings
            tenant_id: Tenant identifier
            user_id: User identifier
        """
        super().__init__(
            name="query_knowledge_base",
            description=(
                "Search the organization's knowledge base for relevant information "
                "from uploaded documents (PDFs, files, etc.). Use this when the user "
                "asks about specific documents, projects, or information that might be "
                "in the knowledge base."
            ),
        )
        self.qdrant_url = qdrant_url
        self.qdrant_api_key = qdrant_api_key
        self.openai_api_key = openai_api_key
        self.tenant_id = tenant_id
        self.user_id = user_id
        
        # Initialize Qdrant client
        self.client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
        
        # Configure OpenAI for embeddings (async client)
        if openai_api_key:
            self.openai_client = AsyncOpenAI(api_key=openai_api_key)
        else:
            self.openai_client = None
        
        logger.info(
            "QueryKnowledgeBaseTool initialized",
            tenant_id=tenant_id,
            user_id=user_id,
            qdrant_url=qdrant_url,
        )

    def _get_collection_name(self, is_general: bool = False) -> str:
        """Generate collection name with tenant awareness."""
        # Backoffice uses: knowledge_t_<tenant_name>_<user_id>
        # tenant_id is already in format "t_default" or just "default"
        # Remove "t_" prefix if present to avoid duplication
        tenant_name = self.tenant_id.replace("t_", "") if self.tenant_id.startswith("t_") else self.tenant_id
        
        if is_general:
            # General tenant collection: knowledge_t_default_0
            return f"knowledge_t_{tenant_name}_0"
        else:
            # User-specific collection: knowledge_t_default_<user_id>
            return f"knowledge_t_{tenant_name}_{self.user_id}"

    async def _get_embedding(self, text: str) -> list[float]:
        """Generate embedding using OpenAI (same as Backoffice)."""
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
                        description="The search query to find relevant documents",
                    ),
                    "limit": types.Schema(
                        type=types.Type.INTEGER,
                        description="Maximum number of results to return (default: 5)",
                    ),
                },
                required=["query"],
            ),
        )

    @override
    async def run_async(self, *, args: Dict[str, Any], tool_context: Any) -> Dict[str, Any]:
        """
        Execute the knowledge base search.
        
        Args:
            args: Dictionary with 'query' and optional 'limit'
            tool_context: ADK tool invocation context
            
        Returns:
            Search results with documents and metadata
        """
        query = args.get("query", "")
        limit = args.get("limit", 5)
        
        if not query:
            return {"status": "error", "message": "Query is required"}
        
        logger.info(
            "Searching knowledge base",
            query=query,
            limit=limit,
            tenant_id=self.tenant_id,
            user_id=self.user_id,
        )
        
        try:
            # Generate query embedding
            query_embedding = await self._get_embedding(query)
            
            all_results = []
            
            # 1. Search user-specific collection
            user_collection = self._get_collection_name(is_general=False)
            try:
                user_search_response = self.client.query_points(
                    collection_name=user_collection,
                    query=query_embedding,
                    limit=limit,
                )
                for result in user_search_response.points:
                    all_results.append({
                        "text": result.payload.get("text", ""),
                        "source": result.payload.get("source", "unknown"),
                        "score": result.score,
                        "scope": "private",
                        "metadata": result.payload,
                    })
                logger.debug(
                    "User collection searched",
                    collection=user_collection,
                    results=len(user_search_response.points),
                )
            except Exception as e:
                logger.warning(
                    "User collection search failed",
                    collection=user_collection,
                    error=str(e),
                )
            
            # 2. Search general tenant collection
            tenant_collection = self._get_collection_name(is_general=True)
            try:
                tenant_search_response = self.client.query_points(
                    collection_name=tenant_collection,
                    query=query_embedding,
                    limit=limit,
                )
                for result in tenant_search_response.points:
                    all_results.append({
                        "text": result.payload.get("text", ""),
                        "source": result.payload.get("source", "unknown"),
                        "score": result.score,
                        "scope": "general",
                        "metadata": result.payload,
                    })
                logger.debug(
                    "Tenant collection searched",
                    collection=tenant_collection,
                    results=len(tenant_search_response.points),
                )
            except Exception as e:
                logger.warning(
                    "Tenant collection search failed",
                    collection=tenant_collection,
                    error=str(e),
                )
            
            # Sort by score and limit
            all_results.sort(key=lambda x: x["score"], reverse=True)
            
            # 🔥 FIX: Apply minimum score threshold to filter irrelevant results
            MIN_SCORE_THRESHOLD = 0.65  # Adjust based on testing
            filtered_results = [r for r in all_results if r["score"] >= MIN_SCORE_THRESHOLD]
            top_results = filtered_results[:limit]
            
            logger.info(
                "Knowledge base search completed",
                query=query,
                total_results=len(all_results),
                filtered_results=len(filtered_results),
                returned_results=len(top_results),
                min_score=top_results[0]["score"] if top_results else 0,
            )
            
            if not top_results:
                return {
                    "status": "success",
                    "message": "No relevant documents found in the knowledge base for this query",
                    "results": [],
                }
            
            return {
                "status": "success",
                "results": top_results,
                "total": len(top_results),
            }
            
        except Exception as e:
            logger.error("Knowledge base search error", error=str(e), query=query)
            return {
                "status": "error",
                "message": f"Search failed: {str(e)}",
            }

