"""
Workspace Memory Saver

Saves relevant Workspace operation results to OpenMemory.

Stores:
- Important emails (with project/person tags)
- Calendar events (with attendee tags)
- Documents accessed (with project tags)
- Decisions made

This allows future operations to leverage past context.
"""

from typing import Any, Dict, List, Optional
import json
import structlog
from datetime import datetime

logger = structlog.get_logger()


class WorkspaceMemorySaver:
    """
    Saves Workspace operation results to OpenMemory.
    """
    
    def __init__(self, mcp_adapter: Any, user_context: Any):
        self.mcp_adapter = mcp_adapter
        self.user_context = user_context
        
        logger.info("WorkspaceMemorySaver initialized")
    
    async def save(
        self,
        task: str,
        plan: Dict[str, Any],
        results: Dict[str, Any],
        context: Dict[str, Any]
    ) -> None:
        """
        Save relevant results to OpenMemory.
        
        Args:
            task: Original task
            plan: Execution plan
            results: Execution results
            context: Original context
        """
        logger.info(
            "Saving Workspace results to memory",
            task=task[:100]
        )
        
        try:
            # Extract important information from results
            memories_to_save = self._extract_memories(task, plan, results, context)
            
            # Save each memory to OpenMemory
            for memory in memories_to_save:
                try:
                    await self._save_memory(memory)
                    logger.debug(
                        "Memory saved",
                        type=memory.get("type"),
                        tags=memory.get("tags", [])
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to save memory",
                        error=str(e),
                        memory_type=memory.get("type")
                    )
            
            logger.info(
                "Memories saved",
                count=len(memories_to_save)
            )
            
        except Exception as e:
            logger.error(
                "Failed to save memories",
                error=str(e)
            )
    
    def _extract_memories(
        self,
        task: str,
        plan: Dict[str, Any],
        results: Dict[str, Any],
        context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Extract memories worth saving from results.
        
        Returns list of memory objects to save.
        """
        memories = []
        
        # Save the operation itself
        memories.append({
            "type": "workspace_operation",
            "content": f"Task: {plan.get('clarified_task')}\nSummary: {results.get('summary')}",
            "tags": ["workspace", "operation"],
            "metadata": {
                "task": task,
                "clarified_task": plan.get("clarified_task"),
                "timestamp": datetime.utcnow().isoformat()
            }
        })
        
        # Extract domain-specific memories
        data = results.get("data", {})
        
        # Gmail memories
        if "messages" in data or "message" in data:
            memories.extend(self._extract_gmail_memories(data, context))
        
        # Calendar memories
        if "events" in data or "event" in data:
            memories.extend(self._extract_calendar_memories(data, context))
        
        # Drive memories
        if "files" in data or "file" in data:
            memories.extend(self._extract_drive_memories(data, context))
        
        return memories
    
    def _extract_gmail_memories(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Extract memories from Gmail results.
        """
        memories = []
        
        # Get messages from data
        messages = data.get("messages", [])
        if not isinstance(messages, list):
            messages = [data.get("message")] if "message" in data else []
        
        for msg in messages[:5]:  # Limit to 5 most relevant
            if not msg:
                continue
            
            # Extract tags from context
            tags = ["gmail", "workspace"]
            
            # Add project tags
            for project in context.get("projects", []):
                if project.get("name", "").lower() in str(msg).lower():
                    tags.append(f"project:{project['name']}")
            
            # Add person tags
            for person in context.get("people", []):
                if person.get("email", "").lower() in str(msg).lower():
                    tags.append(f"person:{person['name']}")
            
            memories.append({
                "type": "gmail_message",
                "content": f"Email: {msg.get('subject', 'No subject')}\nFrom: {msg.get('from', 'Unknown')}\nSnippet: {msg.get('snippet', '')}",
                "tags": tags,
                "metadata": {
                    "message_id": msg.get("id"),
                    "subject": msg.get("subject"),
                    "from": msg.get("from"),
                    "timestamp": msg.get("date")
                }
            })
        
        return memories
    
    def _extract_calendar_memories(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Extract memories from Calendar results.
        """
        memories = []
        
        # Get events from data
        events = data.get("events", [])
        if not isinstance(events, list):
            events = [data.get("event")] if "event" in data else []
        
        for event in events[:5]:  # Limit to 5 most relevant
            if not event:
                continue
            
            tags = ["calendar", "workspace"]
            
            # Add attendee tags
            attendees = event.get("attendees", [])
            for attendee in attendees:
                email = attendee.get("email", "")
                # Match with context people
                for person in context.get("people", []):
                    if person.get("email") == email:
                        tags.append(f"person:{person['name']}")
            
            memories.append({
                "type": "calendar_event",
                "content": f"Event: {event.get('summary', 'No title')}\nWhen: {event.get('start', {}).get('dateTime', 'Unknown')}\nAttendees: {', '.join([a.get('email', '') for a in attendees])}",
                "tags": tags,
                "metadata": {
                    "event_id": event.get("id"),
                    "summary": event.get("summary"),
                    "start": event.get("start", {}).get("dateTime"),
                    "end": event.get("end", {}).get("dateTime")
                }
            })
        
        return memories
    
    def _extract_drive_memories(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Extract memories from Drive results.
        """
        memories = []
        
        # Get files from data
        files = data.get("files", [])
        if not isinstance(files, list):
            files = [data.get("file")] if "file" in data else []
        
        for file in files[:5]:  # Limit to 5 most relevant
            if not file:
                continue
            
            tags = ["drive", "workspace"]
            
            # Add project tags
            for project in context.get("projects", []):
                if project.get("name", "").lower() in file.get("name", "").lower():
                    tags.append(f"project:{project['name']}")
            
            memories.append({
                "type": "drive_file",
                "content": f"File: {file.get('name', 'Unknown')}\nType: {file.get('mimeType', 'Unknown')}\nOwner: {file.get('owners', [{}])[0].get('emailAddress', 'Unknown')}",
                "tags": tags,
                "metadata": {
                    "file_id": file.get("id"),
                    "name": file.get("name"),
                    "mime_type": file.get("mimeType"),
                    "web_view_link": file.get("webViewLink")
                }
            })
        
        return memories
    
    async def _save_memory(self, memory: Dict[str, Any]) -> None:
        """
        Save a single memory to OpenMemory via MCP.
        """
        try:
            await self.mcp_adapter.call_tool(
                server_id="openmemory",
                tool_name="openmemory_store",
                params={
                    "content": memory["content"],
                    "tags": memory.get("tags", []),
                    "metadata": memory.get("metadata", {}),
                    "user_id": f"{self.user_context.tenant_id}:{self.user_context.sub}"  # Format: tenant:user (e.g., "default:12")
                },
                context=self.user_context
            )
        except Exception as e:
            logger.warning(
                "Failed to save memory to OpenMemory",
                error=str(e),
                memory_type=memory.get("type")
            )
            raise

