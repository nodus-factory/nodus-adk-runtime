"""
A2A Tool - ADK-compliant tool for Agent-to-Agent communication
Follows the official ADK pattern from McpTool
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Union
import structlog

from google.genai.types import FunctionDeclaration
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

logger = structlog.get_logger()


class A2ATool(BaseTool):
    """
    ADK-compliant wrapper for A2A (Agent-to-Agent) tools.
    
    This class follows the official ADK pattern from McpTool, using
    parameters_json_schema to pass JSON Schema directly to the LLM.
    
    Example:
        >>> tool = A2ATool(
        ...     agent_name="email_agent",
        ...     method="send_email",
        ...     method_info={
        ...         "description": "Send an email",
        ...         "parameters": {
        ...             "type": "object",
        ...             "properties": {
        ...                 "to": {"type": "string"},
        ...                 "subject": {"type": "string"},
        ...                 "body": {"type": "string"}
        ...             },
        ...             "required": ["to", "subject", "body"]
        ...         }
        ...     },
        ...     endpoint="http://email-agent:8004/a2a",
        ... )
    """
    
    def __init__(
        self,
        *,
        agent_name: str,
        method: str,
        method_info: Dict[str, Any],
        endpoint: str,
        timeout: float = 30.0,
        require_confirmation: Union[bool, Callable[..., bool]] = False,
    ):
        """
        Initialize an A2A tool.
        
        Args:
            agent_name: Name of the A2A agent (e.g., "email_agent")
            method: Method to call on the agent (e.g., "send_email")
            method_info: Method metadata including description and parameters
            endpoint: A2A agent endpoint URL
            timeout: Request timeout in seconds
            require_confirmation: Whether this tool requires HITL confirmation
        """
        tool_name = f"{agent_name}_{method}"
        description = method_info.get("description", f"Call {method} on {agent_name}")
        
        super().__init__(
            name=tool_name,
            description=description,
        )
        
        self._agent_name = agent_name
        self._method = method
        self._method_info = method_info
        self._endpoint = endpoint
        self._timeout = timeout
        self._require_confirmation = require_confirmation
        
        logger.info(
            "A2ATool created",
            name=tool_name,
            agent=agent_name,
            method=method,
            endpoint=endpoint,
        )
    
    def _get_declaration(self) -> FunctionDeclaration:
        """
        Get the function declaration for this tool.
        
        This follows the official ADK pattern from McpTool:
        - Uses parameters_json_schema to pass JSON Schema directly
        - Supports the modern ADK API for dynamic tools
        
        Returns:
            FunctionDeclaration with JSON Schema parameters
        """
        # Extract JSON Schema from method info
        json_schema = self._method_info.get("parameters", {})
        
        # Use the modern ADK API: parameters_json_schema
        # This is the same approach used by McpTool
        function_decl = FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters_json_schema=json_schema,
        )
        
        logger.debug(
            "A2ATool declaration created",
            name=self.name,
            has_parameters=bool(json_schema),
        )
        
        return function_decl
    
    async def run_async(
        self,
        *,
        args: Dict[str, Any],
        tool_context: ToolContext,
    ) -> Any:
        """
        Execute the A2A tool call.
        
        Args:
            args: LLM-provided arguments for the tool
            tool_context: ADK tool context
            
        Returns:
            Result from the A2A agent, or HITL marker if confirmation required
        """
        # Import here to avoid circular dependency
        try:
            from nodus_adk_agents.a2a_client import A2AClient
        except ImportError:
            logger.error("A2AClient not available")
            return {"error": "A2AClient not available"}
        
        logger.info(
            "A2A tool called",
            agent=self._agent_name,
            method=self._method,
            args=args,
        )
        
        try:
            # Create A2A client and call the agent
            client = A2AClient(self._endpoint, timeout=self._timeout)
            result = await client.call(self._method, args)
            
            # Check if agent requires HITL confirmation
            if isinstance(result, dict) and result.get("status") == "hitl_required":
                logger.info(
                    "A2A agent requires HITL",
                    agent=self._agent_name,
                    method=self._method,
                    action=result.get("action_description")
                )
                
                # Return HITL marker for Assistant API to detect
                hitl_marker = {
                    "_hitl_required": True,
                    "agent": self._agent_name,
                    "method": self._method,
                    "action_type": result.get("action_type"),
                    "action_description": result.get("action_description"),
                    "action_data": result.get("action_data"),
                    "metadata": result.get("metadata"),  # Pass agent's metadata (tool, input_type, etc.)
                    "question": result.get("question"),
                    "preview": result.get("preview"),
                    "message_to_user": f"⚠️ Human confirmation required: {result.get('question', 'Confirm this action?')}"
                }
                
                return hitl_marker
            
            logger.info(
                "A2A tool call successful",
                agent=self._agent_name,
                method=self._method,
            )
            
            return result
        
        except Exception as e:
            logger.error(
                "A2A tool call failed",
                agent=self._agent_name,
                method=self._method,
                error=str(e),
            )
            return {"error": f"A2A call failed: {str(e)}"}


