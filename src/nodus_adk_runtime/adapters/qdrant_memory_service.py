"""
Qdrant Memory Service

Implements ADK BaseMemoryService using Qdrant for RAG.
This is a peripheral implementation that doesn't modify ADK core.
"""

from typing import TYPE_CHECKING, Optional
import structlog
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
import httpx
import openai
import os

if TYPE_CHECKING:
    from google.adk.sessions.session import Session
    from google.adk.memory.base_memory_service import SearchMemoryResponse, MemoryEntry

logger = structlog.get_logger()


class QdrantMemoryService:
    """
    Qdrant-based memory service implementing ADK BaseMemoryService interface.
    
    Uses Qdrant for vector storage and semantic search (RAG).
    """

    def __init__(
        self,
        qdrant_url: str,
        qdrant_api_key: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        collection_name: str = "adk_memory",
        vector_size: int = 1536,  # OpenAI text-embedding-3-small dimension
    ):
        """
        Initialize Qdrant memory service.

        Args:
            qdrant_url: URL of the Qdrant service
            qdrant_api_key: Optional API key for Qdrant
            openai_api_key: Optional OpenAI API key for embeddings
            collection_name: Name of the Qdrant collection
            vector_size: Dimension of embedding vectors
        """
        self.qdrant_url = qdrant_url
        self.qdrant_api_key = qdrant_api_key
        self.collection_name = collection_name
        self.vector_size = vector_size
        
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
        
        # Ensure collection exists
        self._ensure_collection()

    def _ensure_collection(self):
        """Ensure the Qdrant collection exists."""
        try:
            collections = self.client.get_collections()
            collection_names = [c.name for c in collections.collections]
            
            if self.collection_name not in collection_names:
                logger.info("Creating Qdrant collection", collection=self.collection_name)
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(
                        size=self.vector_size,
                        distance=Distance.COSINE,
                    ),
                )
            else:
                logger.debug("Qdrant collection exists", collection=self.collection_name)
        except Exception as e:
            logger.error("Failed to ensure Qdrant collection", error=str(e))
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

    async def add_session_to_memory(self, session: "Session"):
        """
        Add a session to memory.

        Args:
            session: ADK Session object
        """
        logger.info("Adding session to memory", session_id=session.id)
        
        try:
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
                        'text': event_data['text'],
                        'author': event_data['author'],
                        'timestamp': event_data['timestamp'].isoformat() if event_data['timestamp'] else None,
                    },
                )
                
                self.client.upsert(
                    collection_name=self.collection_name,
                    points=[point],
                )
            
            logger.info("Session added to memory", session_id=session.id, events=len(events_to_store))
            
        except Exception as e:
            logger.error("Failed to add session to memory", error=str(e), session_id=session.id)
            raise

    async def search_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        query: str,
    ) -> "SearchMemoryResponse":
        """
        Search memory for relevant content.

        Args:
            app_name: Application name
            user_id: User ID
            query: Search query

        Returns:
            SearchMemoryResponse with matching memories
        """
        logger.info("Searching memory", app_name=app_name, user_id=user_id, query=query)
        
        try:
            # Get query embedding
            query_embedding = await self._get_embedding(query)
            
            # Search in Qdrant
            search_results = self.client.search(
                collection_name=self.collection_name,
                query_vector=query_embedding,
                query_filter=None,  # TODO: Add filtering by app_name and user_id
                limit=10,
            )
            
            # Convert to MemoryEntry format
            from google.adk.memory.memory_entry import MemoryEntry
            from google.adk.memory.base_memory_service import SearchMemoryResponse
            from google.genai import types
            
            memories = []
            for result in search_results:
                payload = result.payload
                if payload:
                    # Create Content object
                    content = types.Content(
                        parts=[types.Part(text=payload.get('text', ''))],
                        role=payload.get('author', 'user'),
                    )
                    
                    # Create MemoryEntry
                    memory_entry = MemoryEntry(
                        content=content,
                        author=payload.get('author', 'user'),
                        timestamp=payload.get('timestamp', ''),
                    )
                    memories.append(memory_entry)
            
            logger.info("Memory search completed", results=len(memories))
            return SearchMemoryResponse(memories=memories)
            
        except Exception as e:
            logger.error("Failed to search memory", error=str(e))
            from google.adk.memory.base_memory_service import SearchMemoryResponse
            return SearchMemoryResponse(memories=[])

