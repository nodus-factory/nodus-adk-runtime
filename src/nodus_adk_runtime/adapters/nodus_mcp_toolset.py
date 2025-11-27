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
        input_schema: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize MCP tool.
        
        Args:
            name: Tool name (will be the original tool name without any prefix)
            description: Tool description
            server: MCP server ID
            mcp_adapter: MCP adapter instance
            user_context: User context for authentication
            input_schema: Tool input schema from MCP server
        """
        super().__init__(name=name, description=description)
        self.server = server
        self.mcp_adapter = mcp_adapter
        self.user_context = user_context
        self.input_schema = input_schema or {}
        # Store the original tool name (without prefix) for MCP server calls
        self.original_tool_name = name
    
    def _get_declaration(self) -> Optional[types.FunctionDeclaration]:
        """Get function declaration for this tool."""
        # Use the actual input schema from the MCP server
        # MCP uses JSON Schema format, which is compatible with ADK
        if self.input_schema:
            # Convert JSON Schema to ADK Schema
            try:
                return types.FunctionDeclaration(
                    name=self.name,
                    description=self.description,
                    parameters_json_schema=self.input_schema,
                )
            except Exception as e:
                logger.warning(
                    "Failed to convert MCP schema to ADK",
                    tool=self.name,
                    error=str(e)
                )
        
        # Fallback to generic schema if conversion fails
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={},  # Allow any arguments
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
            args: Tool arguments (direct from ADK, already unwrapped)
            tool_context: Tool execution context
            
        Returns:
            Tool execution result
        """
        logger.info(
            "Executing MCP tool",
            tool=self.name,
            server=self.server,
            user_id=self.user_context.sub,
            args=args,
        )
        
        # Call MCP adapter with original tool name (without prefix)
        # The MCP server only knows the original tool name
        result = await self.mcp_adapter.call_tool(
            server=self.server,
            tool=self.original_tool_name,
            args=args,
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
    Toolset that wraps Nodus MCP Gateway tools for a specific server.
    
    Discovers tools dynamically from the Gateway and creates BaseTool instances.
    Supports tool filtering to limit exposed tools (following ADK McpToolset pattern).
    """
    
    def __init__(
        self,
        mcp_adapter: MCPAdapter,
        user_context: UserContext,
        server_id: Optional[str] = None,
        tool_filter: Optional[List[str]] = None,
        tool_name_prefix: Optional[str] = None,
    ):
        """
        Initialize Nodus MCP Toolset.
        
        Args:
            mcp_adapter: MCP adapter instance
            user_context: User context for authentication
            server_id: Specific MCP server ID to limit tools to (e.g., 'b2brouter')
            tool_filter: List of tool names to include (e.g., ['list_projects', 'create_invoice'])
            tool_name_prefix: Optional prefix for tool names (default: no prefix)
        """
        super().__init__(tool_name_prefix=tool_name_prefix)
        self.mcp_adapter = mcp_adapter
        self.user_context = user_context
        self.server_id = server_id
        self.tool_filter = tool_filter
        self._tools_cache: Optional[List[BaseTool]] = None
    
    async def get_tools(
        self,
        readonly_context: Optional[ReadonlyContext] = None,
    ) -> List[BaseTool]:
        """
        Get available MCP tools.
        
        Discovers individual tools from MCP servers and applies tool_filter if specified.
        
        Args:
            readonly_context: Optional context for filtering
            
        Returns:
            List of BaseTool instances (individual tools)
        """
        if self._tools_cache is None:
            logger.info(
                "Discovering MCP tools",
                user_id=self.user_context.sub,
                server_id=self.server_id,
                tool_filter=self.tool_filter,
            )
            
            try:
                tools = []
                
                # Determine which servers to discover tools from
                if self.server_id:
                    # Specific server specified
                    servers_to_discover = [{"id": self.server_id}]
                else:
                    # Discover all available servers
                    servers_to_discover = await self.mcp_adapter.list_tools(self.user_context)
                
                # Discover tools from each server
                for server_info in servers_to_discover:
                    server_id = server_info.get("server") or server_info.get("id", "unknown")
                    
                    # Get individual tools for this server
                    server_tools = await self.mcp_adapter.list_server_tools(
                        server_id=server_id,
                        context=self.user_context
                    )
                    
                    for tool_def in server_tools:
                        tool_name = tool_def.get("name")
                        if not tool_name:
                            logger.warning(
                                "Tool missing name field",
                                server=server_id,
                                tool=tool_def
                            )
                            continue
                        
                        # Apply tool_filter if specified
                        if self.tool_filter and tool_name not in self.tool_filter:
                            continue
                        
                        # Create NodusMcpTool for each individual tool
                        # NOTE: Don't apply tool_name_prefix manually - BaseToolset handles it
                        tool = NodusMcpTool(
                            name=tool_name,
                            description=tool_def.get("description", f"{tool_name} from {server_id}"),
                            server=server_id,
                            mcp_adapter=self.mcp_adapter,
                            user_context=self.user_context,
                            input_schema=tool_def.get("inputSchema"),
                        )
                        tools.append(tool)
                        
                        logger.debug(
                            "Discovered MCP tool",
                            tool=tool_name,
                            server=server_id
                        )
                
                self._tools_cache = tools
                logger.info(
                    "MCP tools discovered",
                    count=len(tools),
                    server_id=self.server_id,
                    filtered=bool(self.tool_filter),
                )
            except Exception as e:
                logger.error(
                    "Failed to discover MCP tools",
                    error=str(e),
                    server_id=self.server_id
                )
                self._tools_cache = []
        
        return self._tools_cache


