"""
Workspace Context Builder

Builds rich context for Workspace operations by:
1. Querying OpenMemory for relevant memories (projects, people, documents)
2. Parsing recent conversation for pronouns and references
3. Extracting user preferences and constraints

Output: Structured context dict for the Planner
"""

from typing import Any, Dict, List, Optional
import json
import structlog

logger = structlog.get_logger()


class WorkspaceContextBuilder:
    """
    Builds context for Workspace operations using OpenMemory and conversation history.
    """
    
    def __init__(self, mcp_adapter: Any, user_context: Any):
        self.mcp_adapter = mcp_adapter
        self.user_context = user_context
    
    async def build(
        self,
        task: str,
        scope: str,
        conversation_context: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Build comprehensive context for the task.
        
        Args:
            task: Natural language task description
            scope: Primary domain (gmail, calendar, drive, etc.)
            conversation_context: ADK ToolContext with conversation history
            
        Returns:
            Dict with:
                - user: User info (id, email, tenant)
                - projects: Active projects from memory
                - people: Relevant people (names, emails, roles)
                - recent_activity: Recent Workspace activity from memory
                - conversation: Recent conversation turns
                - preferences: User preferences
        """
        logger.info(
            "Building Workspace context",
            task=task[:100],
            scope=scope,
            user_id=self.user_context.sub
        )
        
        # Extract email from user_context (may be in different attributes)
        user_email = "unknown@mynodus.com"
        if hasattr(self.user_context, 'email') and self.user_context.email:
            user_email = self.user_context.email
        elif hasattr(self.user_context, 'username') and '@' in str(self.user_context.username):
            user_email = self.user_context.username
        
        context = {
            "user": {
                "id": self.user_context.sub,
                "email": user_email,
                "tenant_id": self.user_context.tenant_id or "default"
            },
            "projects": [],
            "people": [],
            "recent_activity": [],
            "conversation": [],
            "preferences": {}
        }
        
        # Query OpenMemory for relevant context
        try:
            memories = await self._query_openmemory(task, scope)
            context = self._extract_context_from_memories(memories, context)
        except Exception as e:
            logger.warning(
                "Failed to query OpenMemory for context",
                error=str(e)
            )
        
        # Parse recent conversation
        if conversation_context:
            context["conversation"] = self._parse_conversation(conversation_context)
        
        logger.info(
            "Context built",
            projects=len(context["projects"]),
            people=len(context["people"]),
            recent_activity=len(context["recent_activity"]),
            conversation_turns=len(context["conversation"])
        )
        
        return context
    
    async def _query_openmemory(self, task: str, scope: str) -> List[Dict[str, Any]]:
        """
        Query OpenMemory for relevant memories.
        
        Searches for:
        - Active projects
        - People mentioned in task
        - Recent Workspace activity (emails, docs, events)
        """
        try:
            # Build search query based on task and scope
            search_query = self._build_memory_query(task, scope)
            
            logger.debug(
                "Querying OpenMemory",
                query=search_query,
                scope=scope
            )
            
            # Call OpenMemory via MCP
            result = await self.mcp_adapter.call_tool(
                server_id="openmemory",
                tool_name="query",
                params={
                    "query": search_query,
                    "user_id": f"{self.user_context.tenant_id}:{self.user_context.sub}",  # Format: tenant:user (e.g., "default:12")
                    "tags": [scope, "workspace"],
                    "limit": 20
                },
                context=self.user_context
            )
            
            # Extract memories from result
            if result and "content" in result:
                content_text = result["content"][0].get("text", "")
                try:
                    memories_data = json.loads(content_text)
                    return memories_data.get("memories", [])
                except:
                    return []
            
            return []
            
        except Exception as e:
            logger.warning(
                "OpenMemory query failed",
                error=str(e)
            )
            return []
    
    def _build_memory_query(self, task: str, scope: str) -> str:
        """
        Build an OpenMemory search query based on task and scope.
        
        Examples:
        - "Busca emails de John" → "John email gmail"
        - "Què tinc a l'agenda?" → "calendar events today"
        - "Document del projecte X" → "project X document drive"
        """
        # Extract key terms from task
        task_lower = task.lower()
        
        # Add scope-specific terms
        scope_terms = {
            "gmail": "email correu mail",
            "calendar": "agenda calendar event reunió",
            "drive": "document fitxer file drive",
            "docs": "document doc google docs",
            "sheets": "full spreadsheet excel"
        }
        
        query_parts = [task]
        if scope in scope_terms:
            query_parts.append(scope_terms[scope])
        
        return " ".join(query_parts)
    
    def _extract_context_from_memories(
        self,
        memories: List[Dict[str, Any]],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Extract structured context from OpenMemory results.
        
        Looks for:
        - Project names and IDs
        - People names and emails
        - Recent Workspace activity
        """
        for memory in memories:
            content = memory.get("content", "")
            tags = memory.get("tags", [])
            
            # Extract projects
            if "project" in tags:
                context["projects"].append({
                    "name": memory.get("metadata", {}).get("project_name", "Unknown"),
                    "id": memory.get("metadata", {}).get("project_id"),
                    "context": content[:200]
                })
            
            # Extract people
            if "person" in tags or "contact" in tags:
                context["people"].append({
                    "name": memory.get("metadata", {}).get("name", "Unknown"),
                    "email": memory.get("metadata", {}).get("email"),
                    "role": memory.get("metadata", {}).get("role"),
                    "context": content[:200]
                })
            
            # Extract recent activity
            if any(tag in tags for tag in ["gmail", "calendar", "drive"]):
                context["recent_activity"].append({
                    "domain": next((tag for tag in tags if tag in ["gmail", "calendar", "drive"]), "unknown"),
                    "summary": content[:300],
                    "timestamp": memory.get("timestamp")
                })
        
        return context
    
    def _parse_conversation(self, conversation_context: Any) -> List[Dict[str, str]]:
        """
        Parse recent conversation turns from ADK ToolContext.
        
        Returns list of recent turns for pronoun resolution.
        """
        # TODO: Extract from conversation_context
        # For now, return empty list
        return []

