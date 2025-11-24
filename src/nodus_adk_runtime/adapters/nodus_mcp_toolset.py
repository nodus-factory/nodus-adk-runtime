"""
Nodus MCP Toolset

Implements ADK BaseToolset interface for Nodus MCP Gateway.
This is a peripheral implementation that doesn't modify ADK core.
"""

from typing import Optional, List, Dict, Any
import structlog
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.genai import types

from .mcp_adapter import MCPAdapter
from ..middleware.auth import UserContext

logger = structlog.get_logger()


class NodusMcpTool(BaseTool):
    """A tool that wraps an MCP tool call."""
    
    def __init__(
        self,
        name: str,
        description: str,
        server: str,
        mcp_adapter: MCPAdapter,
        user_context: UserContext,
    ):
        """
        Initialize MCP tool.
        
        Args:
            name: Tool name
            description: Tool description
            server: MCP server ID
            mcp_adapter: MCP adapter instance
            user_context: User context for authentication
        """
        super().__init__(name=name, description=description)
        self.server = server
        self.mcp_adapter = mcp_adapter
        self.user_context = user_context
    
    def _get_declaration(self) -> Optional[types.FunctionDeclaration]:
        """Get function declaration for this tool."""
        # For now, use a generic schema
        # TODO: Parse actual tool parameters from MCP Gateway
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "args": types.Schema(
                        type=types.Type.OBJECT,
                        description="Tool arguments",
                    ),
                },
                required=["args"],
            ),
        )
    
    async def run_async(
        self,
        *,
        args: Dict[str, Any],
        tool_context: ToolContext,
    ) -> Any:
        """
        Execute the MCP tool.
        
        Args:
            args: Tool arguments (should contain 'args' key with actual args)
            tool_context: Tool execution context
            
        Returns:
            Tool execution result
        """
        logger.info(
            "Executing MCP tool",
            tool=self.name,
            server=self.server,
            user_id=self.user_context.sub,
        )
        
        # Extract actual args - ADK may wrap them
        tool_args = args.get("args", args)
        
        # Call MCP adapter
        result = await self.mcp_adapter.call_tool(
            server=self.server,
            tool=self.name,
            args=tool_args,
            context=self.user_context,
        )
        
        if result.get("status") == "error":
            error_info = result.get("error", {})
            error_msg = error_info.get("message", "Unknown error")
            logger.error(
                "MCP tool execution failed",
                tool=self.name,
                server=self.server,
                error=error_msg,
            )
            return {
                "error": error_msg,
                "status": "error",
            }
        
        logger.info(
            "MCP tool executed successfully",
            tool=self.name,
            server=self.server,
        )
        
        return result.get("data", result)


class NodusMcpToolset(BaseToolset):
    """
    Toolset that wraps Nodus MCP Gateway tools.
    
    Discovers tools dynamically from the Gateway and creates BaseTool instances.
    """
    
    def __init__(
        self,
        mcp_adapter: MCPAdapter,
        user_context: UserContext,
        tool_name_prefix: Optional[str] = "mcp_",
    ):
        """
        Initialize Nodus MCP Toolset.
        
        Args:
            mcp_adapter: MCP adapter instance
            user_context: User context for authentication
            tool_name_prefix: Prefix for tool names (default: "mcp_")
        """
        super().__init__(tool_name_prefix=tool_name_prefix)
        self.mcp_adapter = mcp_adapter
        self.user_context = user_context
        self._tools_cache: Optional[List[BaseTool]] = None
    
    async def get_tools(
        self,
        readonly_context: Optional[ReadonlyContext] = None,
    ) -> List[BaseTool]:
        """
        Get available MCP tools.
        
        Args:
            readonly_context: Optional context for filtering
            
        Returns:
            List of BaseTool instances
        """
        # For now, return empty list - tools will be discovered dynamically
        # TODO: Implement dynamic discovery from MCP Gateway
        # This would require calling mcp_adapter.list_tools() and creating
        # NodusMcpTool instances for each discovered tool
        
        if self._tools_cache is None:
            logger.info("Discovering MCP tools", user_id=self.user_context.sub)
            
            try:
                # Discover tools from Gateway
                servers = await self.mcp_adapter.list_tools(self.user_context)
                
                tools = []
                for server_info in servers:
                    server_id = server_info.get("server") or server_info.get("id", "unknown")
                    server_name = server_info.get("name", server_id)
                    
                    # For now, create a generic tool per server
                    # In production, we'd need to discover actual tools via tools.list JSON-RPC
                    tool = NodusMcpTool(
                        name=f"{server_id}_tool",
                        description=f"Tool for MCP server: {server_name}",
                        server=server_id,
                        mcp_adapter=self.mcp_adapter,
                        user_context=self.user_context,
                    )
                    tools.append(tool)
                
                self._tools_cache = tools
                logger.info(
                    "MCP tools discovered",
                    count=len(tools),
                    servers=[s.get("id") for s in servers],
                )
            except Exception as e:
                logger.error("Failed to discover MCP tools", error=str(e))
                self._tools_cache = []
        
        return self._tools_cache


