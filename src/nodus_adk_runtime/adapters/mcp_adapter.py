"""
MCP Gateway Adapter

Integrates Google ADK with Nodus MCP Gateway for tool execution.
"""

from typing import Any, Dict, List, Optional
import httpx
import structlog
import uuid

from ..middleware.auth import UserContext

logger = structlog.get_logger()


class MCPAdapter:
    """Adapter for MCP Gateway integration."""

    def __init__(self, gateway_url: str):
        """
        Initialize MCP adapter.

        Args:
            gateway_url: URL of the MCP Gateway service
        """
        self.gateway_url = gateway_url
        self.client = httpx.AsyncClient(timeout=30.0)

    async def list_tools(self, context: UserContext) -> List[Dict[str, Any]]:
        """
        List available MCP tools from the gateway.

        Args:
            context: User context with authentication token

        Returns:
            List of tool definitions
        """
        logger.info("Discovering MCP tools", gateway=self.gateway_url)
        
        try:
            response = await self.client.get(
                f"{self.gateway_url}/mcp/tools",
                headers={
                    "Authorization": f"Bearer {context.raw_token}",
                },
            )
            response.raise_for_status()
            data = response.json()
            
            # Extract tools from response
            # Response format: { "servers": [...], "user_scopes": [...], "tenant": "..." }
            tools = []
            for server in data.get("servers", []):
                server_id = server.get("id")
                # For now, return server info - actual tools would come from tools.list JSON-RPC
                tools.append({
                    "server": server_id,
                    "name": server.get("name", server_id),
                    "protocol": server.get("protocol", "unknown"),
                    "scopes": server.get("scopes", []),
                })
            
            logger.info("Discovered MCP tools", count=len(tools))
            return tools
            
        except httpx.RequestError as e:
            logger.error("Failed to fetch MCP tools", error=str(e))
            return []
        except httpx.HTTPStatusError as e:
            logger.error("MCP Gateway returned error", status=e.response.status_code)
            return []

    async def list_server_tools(
        self, 
        server_id: str, 
        context: UserContext
    ) -> List[Dict[str, Any]]:
        """
        List available tools for a specific MCP server.
        
        Args:
            server_id: MCP server ID (e.g., 'b2brouter', 'openmemory')
            context: User context with authentication token
            
        Returns:
            List of tool definitions with name, description, and inputSchema
        """
        logger.info("Discovering tools for MCP server", server=server_id)
        
        # Gateway expects: { server_id, method, params, id }
        request_body = {
            "server_id": server_id,
            "method": "tools/list",  # MCP protocol format
            "params": {},
            "id": str(uuid.uuid4()),
        }
        
        try:
            response = await self.client.post(
                f"{self.gateway_url}/mcp/call",
                json=request_body,
                headers={
                    "Authorization": f"Bearer {context.raw_token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",  # Required by some MCP servers (OpenMemory)
                },
            )
            response.raise_for_status()
            result = response.json()
            
            # Handle JSON-RPC response
            if "error" in result:
                logger.error(
                    "Failed to list tools for server",
                    server=server_id,
                    error=result["error"],
                )
                return []
            
            # Extract tools from result
            if "result" in result:
                result_data = result["result"]
                
                # Handle different response formats
                if isinstance(result_data, dict):
                    # Format 1: { "data": { "tools": [...] } }
                    if "data" in result_data and isinstance(result_data["data"], dict):
                        tools = result_data["data"].get("tools", [])
                    # Format 2: { "tools": [...] }
                    elif "tools" in result_data:
                        tools = result_data["tools"]
                    # Format 3: Direct list
                    elif isinstance(result_data, list):
                        tools = result_data
                    else:
                        logger.warning(
                            "Unexpected tools.list response format",
                            server=server_id,
                            result_keys=list(result_data.keys())
                        )
                        tools = []
                elif isinstance(result_data, list):
                    tools = result_data
                else:
                    tools = []
                
                logger.info(
                    "Discovered tools for server",
                    server=server_id,
                    count=len(tools)
                )
                return tools
            
            return []
            
        except httpx.RequestError as e:
            logger.error(
                "Failed to fetch tools for server",
                error=str(e),
                server=server_id
            )
            return []
        except httpx.HTTPStatusError as e:
            logger.error(
                "MCP Gateway returned error for tools.list",
                status=e.response.status_code,
                server=server_id
            )
            return []

    async def call_tool(
        self,
        server: str,
        tool: str,
        args: Dict[str, Any],
        context: UserContext,
    ) -> Dict[str, Any]:
        """
        Call an MCP tool through the gateway.

        Args:
            server: MCP server ID
            tool: Tool name
            args: Tool arguments
            context: User context with authentication token

        Returns:
            Tool execution result
        """
        logger.info("Calling MCP tool", server=server, tool=tool, user_id=context.sub)
        
        # Gateway expects: { server_id, method, params, id }
        # Method should be 'tools/call' (MCP protocol format)
        request_body = {
            "server_id": server,
            "method": "tools/call",
            "params": {
                "name": tool,
                "arguments": args,
            },
            "id": str(uuid.uuid4()),
        }
        
        try:
            response = await self.client.post(
                f"{self.gateway_url}/mcp/call",
                json=request_body,
                headers={
                    "Authorization": f"Bearer {context.raw_token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",  # Required by some MCP servers (OpenMemory)
                },
            )
            response.raise_for_status()
            result = response.json()
            
            # Handle JSON-RPC response
            if "error" in result:
                logger.error(
                    "MCP tool call error",
                    server=server,
                    tool=tool,
                    error=result["error"],
                )
                return {
                    "status": "error",
                    "error": result["error"],
                }
            
            # Extract result data
            if "result" in result:
                result_data = result["result"]
                if isinstance(result_data, dict) and "data" in result_data:
                    return {
                        "status": "ok",
                        "data": result_data["data"],
                        "execution_id": result_data.get("executionId"),
                    }
                return {
                    "status": "ok",
                    "data": result_data,
                }
            
            return {
                "status": "ok",
                "data": result,
            }
            
        except httpx.RequestError as e:
            logger.error("Failed to call MCP tool", error=str(e), server=server, tool=tool)
            return {
                "status": "error",
                "error": {"message": str(e), "code": -32603},
            }
        except httpx.HTTPStatusError as e:
            logger.error(
                "MCP Gateway returned error",
                status=e.response.status_code,
                server=server,
                tool=tool,
            )
            try:
                error_data = e.response.json()
                return {
                    "status": "error",
                    "error": error_data,
                }
            except:
                return {
                    "status": "error",
                    "error": {"message": f"HTTP {e.response.status_code}", "code": e.response.status_code},
                }

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()


