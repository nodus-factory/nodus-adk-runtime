"""
Query Pages Tool

Tool for querying documents uploaded to specific Llibreta notebook pages.
Searches in pages_t_{tenant}_{user} collection with page-specific filtering.
"""

from typing import Any, Dict, Optional
import structlog
from google.adk.tools.base_tool import BaseTool
from google.genai import types
from typing_extensions import override
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from openai import AsyncOpenAI
from nodus_adk_runtime.config import settings

logger = structlog.get_logger()


class QueryPagesTool(BaseTool):
    """
    Tool to query documents from Llibreta notebook pages.
    
    Searches in user-specific pages collection:
    - pages_t_<tenant>_<user_id>: Documents uploaded to specific notebook pages
    
    Supports filtering by page_number and notebook_id.
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
        Initialize the pages query tool.
        
        Args:
            qdrant_url: URL of Qdrant service
            qdrant_api_key: Optional Qdrant API key
            openai_api_key: OpenAI API key for embeddings
            tenant_id: Tenant identifier
            user_id: User identifier
        """
        super().__init__(
            name="query_pages",
            description=(
                "Search documents uploaded to Llibreta notebook pages. "
                "Use this when the user asks about 'documents on this page', "
                "'PDF here', 'spreadsheet on page X', or references specific "
                "page content. Can filter by page_number or notebook_id."
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
            "QueryPagesTool initialized",
            tenant_id=tenant_id,
            user_id=user_id,
            qdrant_url=qdrant_url,
        )

    def _get_collection_name(self) -> str:
        """Generate pages collection name with tenant and user awareness."""
        # Format: pages_t_<tenant>_<user_id>
        tenant_name = self.tenant_id.replace("t_", "") if self.tenant_id.startswith("t_") else self.tenant_id
        return f"pages_t_{tenant_name}_{self.user_id}"

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

    def _build_filter(
        self, 
        page_number: Optional[int] = None, 
        notebook_id: Optional[str] = None
    ) -> Optional[Filter]:
        """
        Build Qdrant filter for page/notebook constraints.
        
        Args:
            page_number: Specific page number to search (e.g. 1, 2, 3)
            notebook_id: Notebook ID to search within
            
        Returns:
            Qdrant Filter object or None
        """
        conditions = []
        
        if page_number is not None:
            conditions.append(
                FieldCondition(
                    key="page_number",
                    match=MatchValue(value=page_number)
                )
            )
        
        if notebook_id:
            conditions.append(
                FieldCondition(
                    key="notebook_id",
                    match=MatchValue(value=notebook_id)
                )
            )
        
        return Filter(must=conditions) if conditions else None

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
                        description="Natural language query to search for in page documents",
                    ),
                    "page_number": types.Schema(
                        type=types.Type.INTEGER,
                        description="Optional: specific page number to search (e.g. 1, 2, 3)",
                    ),
                    "notebook_id": types.Schema(
                        type=types.Type.STRING,
                        description="Optional: notebook ID to search within",
                    ),
                    "limit": types.Schema(
                        type=types.Type.INTEGER,
                        description="Maximum number of results (default: 5)",
                    ),
                },
                required=["query"],
            ),
        )

    @override
    async def run_async(self, *, args: Dict[str, Any], tool_context: Any) -> Dict[str, Any]:
        """
        Execute the pages search.
        
        Args:
            args: Dictionary with 'query', optional 'page_number', 'notebook_id', 'limit'
            tool_context: ADK tool invocation context
            
        Returns:
            Search results with page documents and metadata
        """
        query = args.get("query", "")
        page_number = args.get("page_number")
        notebook_id = args.get("notebook_id")
        limit = args.get("limit", 5)
        
        if not query:
            return {"status": "error", "message": "Query is required"}
        
        collection_name = self._get_collection_name()
        
        logger.info(
            "Searching pages collection",
            query=query,
            page_number=page_number,
            notebook_id=notebook_id,
            limit=limit,
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
                    "Pages collection does not exist yet",
                    collection=collection_name,
                )
                return {
                    "status": "success",
                    "message": "No documents uploaded to pages yet",
                    "results": [],
                }
            
            # Generate query embedding
            query_embedding = await self._get_embedding(query)
            
            # Build page/notebook filter if specified
            query_filter = self._build_filter(page_number, notebook_id)
            
            # Search pages collection
            search_response = self.client.query_points(
                collection_name=collection_name,
                query=query_embedding,
                query_filter=query_filter,
                limit=limit * 2,  # Get more to filter by score
            )
            
            # Apply minimum score threshold
            MIN_SCORE_THRESHOLD = 0.35  # Slightly lower for documents
            
            results = []
            for point in search_response.points:
                if point.score < MIN_SCORE_THRESHOLD:
                    continue
                
                payload = point.payload
                results.append({
                    "text": payload.get("text", ""),
                    "source": payload.get("source", "unknown"),
                    "page_number": payload.get("page_number"),
                    "notebook_id": payload.get("notebook_id"),
                    "score": point.score,
                    "timestamp": payload.get("timestamp", ""),
                })
            
            # Limit final results
            results = results[:limit]
            
            logger.info(
                "Pages search completed",
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
                    "message": "No relevant documents found on this page",
                    "results": [],
                }
            
            return {
                "status": "success",
                "results": results,
                "total": len(results),
                "filters": {
                    "page_number": page_number,
                    "notebook_id": notebook_id,
                },
            }
            
        except Exception as e:
            logger.error("Pages search error", error=str(e), query=query)
            return {
                "status": "error",
                "message": f"Search failed: {str(e)}",
            }


