"""
Dynamic A2A Tool Builder
Generates ADK tools from A2A agent configuration without code changes
"""

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
import structlog

from nodus_adk_runtime.config import settings

logger = structlog.get_logger()

# Import A2AClient from nodus-adk-agents
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "nodus-adk-agents" / "src"))

try:
    from nodus_adk_agents.a2a_client import A2AClient
except ImportError:
    logger.warning("A2AClient not found, A2A tools will not be available")
    A2AClient = None


class A2AAgentConfig:
    """Configuration for an A2A agent"""
    
    def __init__(
        self,
        name: str,
        endpoint: str,
        card_url: str,
        enabled: bool = True,
        timeout: float = 30.0,
        description: str = "",
        capabilities: Optional[List[str]] = None,
    ):
        self.name = name
        self.endpoint = endpoint
        self.card_url = card_url
        self.enabled = enabled
        self.timeout = timeout
        self.description = description
        self.capabilities = capabilities or []
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "A2AAgentConfig":
        """Create config from dictionary"""
        return cls(
            name=data["name"],
            endpoint=data["endpoint"],
            card_url=data["card_url"],
            enabled=data.get("enabled", True),
            timeout=data.get("timeout", 30.0),
            description=data.get("description", ""),
            capabilities=data.get("capabilities", []),
        )


class A2AToolBuilder:
    """
    Builds ADK tools dynamically from A2A agent configuration
    
    Features:
    - Load agents from JSON config (no code changes needed)
    - Discover agent capabilities via Agent Cards
    - Generate Python functions as ADK tools
    - Hot reload on config changes (optional)
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize tool builder
        
        Args:
            config_path: Path to a2a_agents.json (defaults to config/a2a_agents.json)
        """
        if config_path is None:
            # Default: look in runtime config directory
            config_path = (
                Path(__file__).parent.parent / "config" / "a2a_agents.json"
            )
        
        self.config_path = Path(config_path)
        self.agents: Dict[str, A2AAgentConfig] = {}
        self.tools: List[Callable] = []
        
        if A2AClient is None:
            logger.error("A2AClient not available, cannot build A2A tools")
    
    def load_config(self) -> None:
        """Load agent configuration from JSON file"""
        if not self.config_path.exists():
            logger.warning(
                "A2A agents config not found",
                path=str(self.config_path),
            )
            return
        
        try:
            with open(self.config_path, "r") as f:
                config = json.load(f)
            
            for agent_data in config.get("agents", []):
                agent_config = A2AAgentConfig.from_dict(agent_data)
                
                if agent_config.enabled:
                    self.agents[agent_config.name] = agent_config
                    logger.info(
                        "Loaded A2A agent config",
                        name=agent_config.name,
                        endpoint=agent_config.endpoint,
                    )
                else:
                    logger.debug(
                        "Skipped disabled A2A agent",
                        name=agent_config.name,
                    )
            
            logger.info(
                "A2A agents config loaded",
                total=len(config.get("agents", [])),
                enabled=len(self.agents),
            )
        
        except Exception as e:
            logger.error(
                "Failed to load A2A agents config",
                error=str(e),
                path=str(self.config_path),
            )
    
    async def discover_capabilities(self, agent_config: A2AAgentConfig) -> Dict[str, Any]:
        """
        Discover agent capabilities via Agent Card
        
        Args:
            agent_config: Agent configuration
            
        Returns:
            Agent Card dictionary
        """
        if A2AClient is None:
            return {}
        
        try:
            client = A2AClient(agent_config.endpoint, timeout=10.0)
            card = await client.discover()
            
            logger.info(
                "Discovered A2A agent capabilities",
                name=agent_config.name,
                capabilities=list(card.get("capabilities", {}).keys()),
            )
            
            return card
        
        except Exception as e:
            logger.error(
                "Failed to discover A2A agent",
                name=agent_config.name,
                error=str(e),
            )
            return {}
    
    def _create_tool_function(
        self,
        agent_name: str,
        endpoint: str,
        method: str,
        method_info: Dict[str, Any],
        timeout: float,
    ) -> Callable:
        """
        Create a Python function that calls an A2A agent method
        
        This function will be used as an ADK tool
        """
        if A2AClient is None:
            async def dummy_tool(**kwargs):
                return {"error": "A2AClient not available"}
            return dummy_tool
        
        # Generate function dynamically
        async def a2a_tool(**kwargs):
            """
            Auto-generated A2A tool
            
            This tool calls a remote A2A agent via HTTP
            """
            try:
                client = A2AClient(endpoint, timeout=timeout)
                result = await client.call(method, kwargs)
                
                logger.info(
                    "A2A tool call successful",
                    agent=agent_name,
                    method=method,
                )
                
                return result
            
            except Exception as e:
                logger.error(
                    "A2A tool call failed",
                    agent=agent_name,
                    method=method,
                    error=str(e),
                )
                return {"error": f"A2A call failed: {str(e)}"}
        
        # Set function metadata for ADK
        a2a_tool.__name__ = f"{agent_name}_{method}"
        a2a_tool.__doc__ = method_info.get("description", f"Call {method} on {agent_name}")
        
        # Add parameter info as function attributes (ADK can read these)
        a2a_tool.__annotations__ = {}
        
        # Handle parameters (can be either direct dict or JSON Schema format)
        parameters = method_info.get("parameters", {})
        if isinstance(parameters, dict):
            # Check if it's JSON Schema format (has "properties")
            if "properties" in parameters:
                param_properties = parameters.get("properties", {})
            else:
                # Direct format (old style)
                param_properties = parameters
            
            for param_name, param_info in param_properties.items():
                # Map JSON Schema types to Python types
                param_type = param_info.get("type", "string")
                python_type = {
                    "string": str,
                    "integer": int,
                    "number": float,
                    "boolean": bool,
                    "array": list,
                    "object": dict,
                }.get(param_type, Any)
                
                a2a_tool.__annotations__[param_name] = python_type
        
        return a2a_tool
    
    async def build_tools(self) -> List[Callable]:
        """
        Build all ADK tools from configured A2A agents
        
        Returns:
            List of tool functions ready for ADK Agent
        """
        self.tools = []
        
        for agent_config in self.agents.values():
            # Discover capabilities
            card = await self.discover_capabilities(agent_config)
            
            if not card:
                logger.warning(
                    "Skipping A2A agent due to discovery failure",
                    name=agent_config.name,
                )
                continue
            
            # Create tool for each capability
            capabilities = card.get("capabilities", {})
            
            for method, method_info in capabilities.items():
                tool = self._create_tool_function(
                    agent_name=agent_config.name,
                    endpoint=agent_config.endpoint,
                    method=method,
                    method_info=method_info,
                    timeout=agent_config.timeout,
                )
                
                self.tools.append(tool)
                
                logger.info(
                    "Created A2A tool",
                    name=tool.__name__,
                    agent=agent_config.name,
                    method=method,
                )
        
        logger.info("A2A tools built", count=len(self.tools))
        return self.tools
    
    async def reload(self) -> List[Callable]:
        """
        Reload configuration and rebuild tools
        
        Useful for hot reload without restarting the service
        """
        logger.info("Reloading A2A agents configuration")
        self.agents.clear()
        self.tools.clear()
        
        self.load_config()
        return await self.build_tools()


# Singleton instance
_tool_builder: Optional[A2AToolBuilder] = None


async def get_a2a_tools(config_path: Optional[str] = None) -> List[Callable]:
    """
    Get all A2A tools for the ADK agent
    
    Usage:
        a2a_tools = await get_a2a_tools()
        
        agent = Agent(
            name="root",
            tools=[...other_tools, *a2a_tools],
        )
    
    Args:
        config_path: Optional path to config file
        
    Returns:
        List of tool functions
    """
    global _tool_builder
    
    if _tool_builder is None:
        _tool_builder = A2AToolBuilder(config_path)
        _tool_builder.load_config()
    
    return await _tool_builder.build_tools()


async def reload_a2a_tools() -> List[Callable]:
    """
    Reload A2A tools from configuration
    
    Useful for hot reload without restarting
    """
    global _tool_builder
    
    if _tool_builder is None:
        _tool_builder = A2AToolBuilder()
        _tool_builder.load_config()
    
    return await _tool_builder.reload()

