"""
Qdrant Memory Service

Implements ADK BaseMemoryService using Qdrant for RAG with multi-tenancy.

Architecture:
- General collection per tenant: adk_memory_{tenant_id}
- Private collection per user: adk_memory_{tenant_id}_user_{user_id}
- Queries search in both: general (shared knowledge) + private (personal memories)
- Strict tenant isolation: users can only access their tenant's data
"""

from typing import TYPE_CHECKING, Optional, List, Dict, Any
import structlog
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue
import httpx
import openai
import os
import hashlib

if TYPE_CHECKING:
    from google.adk.sessions.session import Session
    from google.adk.memory.base_memory_service import SearchMemoryResponse, MemoryEntry

logger = structlog.get_logger()


class QdrantMemoryService:
    """
    Qdrant-based memory service with multi-tenancy support.
    
    Maintains two collection types:
    1. Tenant general collection: shared knowledge across tenant users
    2. User private collection: personal memories for each user
    
    Ensures strict tenant isolation and privacy.
    """

    def __init__(
        self,
        qdrant_url: str,
        qdrant_api_key: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        vector_size: int = 1536,  # OpenAI text-embedding-3-small dimension
    ):
        """
        Initialize Qdrant memory service.

        Args:
            qdrant_url: URL of the Qdrant service
            qdrant_api_key: Optional API key for Qdrant
            openai_api_key: Optional OpenAI API key for embeddings
            vector_size: Dimension of embedding vectors
        """
        self.qdrant_url = qdrant_url
        self.qdrant_api_key = qdrant_api_key
        self.vector_size = vector_size
        self.collection_prefix = "adk_memory"
        
        # Configure OpenAI client
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY")
        if self.openai_api_key:
            openai.api_key = self.openai_api_key
            self.use_real_embeddings = True
            logger.info("OpenAI embeddings enabled for memory service")
        else:
            self.use_real_embeddings = False
            logger.warning("No OpenAI API key found, using placeholder embeddings")
        
        # Initialize Qdrant client
        self.client = QdrantClient(
            url=qdrant_url,
            api_key=qdrant_api_key,
        )

    def _get_tenant_collection_name(self, tenant_id: str) -> str:
        """Get general collection name for tenant."""
        # Sanitize tenant_id for collection name
        safe_tenant = hashlib.md5(tenant_id.encode()).hexdigest()[:16]
        return f"{self.collection_prefix}_{safe_tenant}"
    
    def _get_user_collection_name(self, tenant_id: str, user_id: str) -> str:
        """Get private collection name for user within tenant."""
        safe_tenant = hashlib.md5(tenant_id.encode()).hexdigest()[:16]
        safe_user = hashlib.md5(user_id.encode()).hexdigest()[:16]
        return f"{self.collection_prefix}_{safe_tenant}_user_{safe_user}"

    def _ensure_collection(self, collection_name: str):
        """Ensure a specific Qdrant collection exists."""
        try:
            collections = self.client.get_collections()
            collection_names = [c.name for c in collections.collections]
            
            if collection_name not in collection_names:
                logger.info("Creating Qdrant collection", collection=collection_name)
                self.client.create_collection(
                    collection_name=collection_name,
                    vectors_config=VectorParams(
                        size=self.vector_size,
                        distance=Distance.COSINE,
                    ),
                )
            else:
                logger.debug("Qdrant collection exists", collection=collection_name)
        except Exception as e:
            logger.error("Failed to ensure Qdrant collection", error=str(e), collection=collection_name)
            raise

    async def _get_embedding(self, text: str) -> list[float]:
        """
        Get embedding for text using OpenAI API.
        Falls back to placeholder if OpenAI is not available.
        """
        if self.use_real_embeddings and self.openai_api_key:
            try:
                # Use OpenAI embeddings API
                response = openai.embeddings.create(
                    model="text-embedding-3-small",
                    input=text,
                )
                embedding = response.data[0].embedding
                logger.debug("Generated OpenAI embedding", text_length=len(text))
                return embedding
            except Exception as e:
                logger.error("Failed to generate OpenAI embedding, falling back to placeholder", error=str(e))
                # Fall through to placeholder
        
        # Placeholder embeddings (fallback)
        import hashlib
        import struct
        
        hash_obj = hashlib.sha256(text.encode())
        hash_bytes = hash_obj.digest()
        
        # Convert hash to vector (simple approach for placeholder)
        vector = []
        for i in range(0, min(len(hash_bytes), self.vector_size * 4), 4):
            if len(vector) >= self.vector_size:
                break
            value = struct.unpack('>I', hash_bytes[i:i+4] if len(hash_bytes[i:i+4]) == 4 else hash_bytes[i:i+4] + b'\x00' * (4 - len(hash_bytes[i:i+4])))[0]
            normalized = (value % 2000 - 1000) / 1000.0  # Normalize to [-1, 1]
            vector.append(normalized)
        
        # Pad or truncate to exact size
        while len(vector) < self.vector_size:
            vector.append(0.0)
        return vector[:self.vector_size]

    async def add_session_to_memory(
        self, 
        session: "Session",
        is_general: bool = False
    ):
        """
        Add a session to memory (private user collection or general tenant collection).

        Args:
            session: ADK Session object (must have tenant_id in custom_metadata)
            is_general: If True, stores in tenant general collection; otherwise in user private collection
        """
        # Extract tenant_id from session state
        tenant_id = session.state.get('tenant_id', 'default') if hasattr(session, 'state') and session.state else 'default'
        
        # Determine target collection
        if is_general:
            collection_name = self._get_tenant_collection_name(tenant_id)
            scope = "general"
        else:
            user_id = session.user_id if hasattr(session, 'user_id') else 'unknown'
            collection_name = self._get_user_collection_name(tenant_id, user_id)
            scope = "private"
        
        logger.info(
            "Adding session to memory", 
            session_id=session.id, 
            tenant_id=tenant_id,
            scope=scope,
            collection=collection_name
        )
        
        try:
            # Ensure collection exists
            self._ensure_collection(collection_name)
            
            # Extract events from session
            events_to_store = []
            for event in session.events:
                if event.content and event.content.parts:
                    # Extract text from content parts
                    text_parts = []
                    for part in event.content.parts:
                        if hasattr(part, 'text') and part.text:
                            text_parts.append(part.text)
                    
                    if text_parts:
                        text = ' '.join(text_parts)
                        events_to_store.append({
                            'text': text,
                            'author': event.author if hasattr(event, 'author') else 'unknown',
                            'timestamp': event.timestamp if hasattr(event, 'timestamp') else None,
                        })
            
            # Store each event as a point in Qdrant
            points = []
            for idx, event_data in enumerate(events_to_store):
                embedding = await self._get_embedding(event_data['text'])
                
                point_id = f"{session.id}_{idx}"
                point = PointStruct(
                    id=point_id,
                    vector=embedding,
                    payload={
                        'session_id': session.id,
                        'app_name': session.app_name if hasattr(session, 'app_name') else 'unknown',
                        'user_id': session.user_id if hasattr(session, 'user_id') else 'unknown',
                        'tenant_id': tenant_id,
                        'text': event_data['text'],
                        'author': event_data['author'],
                        'timestamp': event_data['timestamp'].isoformat() if event_data['timestamp'] else None,
                        'scope': scope,
                    },
                )
                points.append(point)
            
            if points:
                self.client.upsert(
                    collection_name=collection_name,
                    points=points,
                )
            
            logger.info(
                "Session added to memory", 
                session_id=session.id, 
                events=len(events_to_store),
                collection=collection_name,
                scope=scope
            )
            
        except Exception as e:
            logger.error("Failed to add session to memory", error=str(e), session_id=session.id, tenant_id=tenant_id)
            raise

    async def search_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        query: str,
        tenant_id: Optional[str] = None,
        limit: int = 5,
    ) -> "SearchMemoryResponse":
        """
        Search memory for relevant content in both tenant general and user private collections.

        Args:
            app_name: Application name
            user_id: User ID
            query: Search query
            tenant_id: Optional Tenant ID for isolation (defaults to 'default')
            limit: Maximum number of results per collection (default: 5)

        Returns:
            SearchMemoryResponse with matching memories from both collections
        """
        # Use default tenant if not provided
        if not tenant_id:
            tenant_id = 'default'
        
        logger.info(
            "Searching memory", 
            app_name=app_name, 
            user_id=user_id, 
            tenant_id=tenant_id,
            query=query
        )
        
        try:
            # Get query embedding
            query_embedding = await self._get_embedding(query)
            
            # Get collection names
            tenant_collection = self._get_tenant_collection_name(tenant_id)
            user_collection = self._get_user_collection_name(tenant_id, user_id)
            
            # Ensure collections exist
            self._ensure_collection(tenant_collection)
            self._ensure_collection(user_collection)
            
            # Search in both collections
            all_results = []
            
            # 1. Search in user private collection
            try:
                user_results = self.client.search(
                    collection_name=user_collection,
                    query_vector=query_embedding,
                    limit=limit,
                )
                for result in user_results:
                    all_results.append({
                        'result': result,
                        'scope': 'private',
                        'score': result.score,
                    })
                logger.debug("Searched user private collection", collection=user_collection, results=len(user_results))
            except Exception as e:
                logger.warning("Failed to search user collection", error=str(e), collection=user_collection)
            
            # 2. Search in tenant general collection
            try:
                tenant_results = self.client.search(
                    collection_name=tenant_collection,
                    query_vector=query_embedding,
                    limit=limit,
                )
                for result in tenant_results:
                    all_results.append({
                        'result': result,
                        'scope': 'general',
                        'score': result.score,
                    })
                logger.debug("Searched tenant general collection", collection=tenant_collection, results=len(tenant_results))
            except Exception as e:
                logger.warning("Failed to search tenant collection", error=str(e), collection=tenant_collection)
            
            # Sort all results by score (highest first)
            all_results.sort(key=lambda x: x['score'], reverse=True)
            
            # Take top results (total limit)
            top_results = all_results[:limit * 2]  # Allow up to 2x limit for combined results
            
            # Convert to MemoryEntry format
            from google.adk.memory.memory_entry import MemoryEntry
            from google.adk.memory.base_memory_service import SearchMemoryResponse
            from google.genai import types
            
            memories = []
            for item in top_results:
                result = item['result']
                payload = result.payload
                if payload:
                    # Create Content object
                    content = types.Content(
                        parts=[types.Part(text=payload.get('text', ''))],
                        role=payload.get('author', 'user'),
                    )
                    
                    # Create MemoryEntry with metadata about scope
                    memory_entry = MemoryEntry(
                        content=content,
                        author=payload.get('author', 'user'),
                        timestamp=payload.get('timestamp', ''),
                        custom_metadata={
                            'scope': item['scope'],
                            'score': item['score'],
                            'tenant_id': tenant_id,
                        },
                    )
                    memories.append(memory_entry)
            
            logger.info(
                "Memory search completed", 
                results=len(memories),
                user_collection=user_collection,
                tenant_collection=tenant_collection
            )
            return SearchMemoryResponse(memories=memories)
            
        except Exception as e:
            logger.error("Failed to search memory", error=str(e), tenant_id=tenant_id, user_id=user_id)
            from google.adk.memory.base_memory_service import SearchMemoryResponse
            return SearchMemoryResponse(memories=[])

