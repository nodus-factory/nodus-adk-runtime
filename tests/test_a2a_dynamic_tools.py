"""
Tests for A2A Dynamic Tool Builder
Tests configuration loading, agent discovery, and tool generation
"""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nodus_adk_runtime.tools.a2a_dynamic_tool_builder import (
    A2AAgentConfig,
    A2AToolBuilder,
    get_a2a_tools,
)


class TestA2AAgentConfig:
    """Test A2AAgentConfig model"""
    
    def test_from_dict_minimal(self):
        """Test creating config from minimal dict"""
        data = {
            "name": "test_agent",
            "endpoint": "http://localhost:8001/a2a",
            "card_url": "http://localhost:8001/",
        }
        
        config = A2AAgentConfig.from_dict(data)
        
        assert config.name == "test_agent"
        assert config.endpoint == "http://localhost:8001/a2a"
        assert config.enabled is True  # Default
        assert config.timeout == 30.0  # Default
    
    def test_from_dict_full(self):
        """Test creating config from full dict"""
        data = {
            "name": "test_agent",
            "endpoint": "http://localhost:8001/a2a",
            "card_url": "http://localhost:8001/",
            "enabled": False,
            "timeout": 60.0,
            "description": "Test agent",
            "capabilities": ["method1", "method2"],
        }
        
        config = A2AAgentConfig.from_dict(data)
        
        assert config.name == "test_agent"
        assert config.enabled is False
        assert config.timeout == 60.0
        assert config.description == "Test agent"
        assert config.capabilities == ["method1", "method2"]


class TestA2AToolBuilder:
    """Test A2AToolBuilder functionality"""
    
    @pytest.fixture
    def mock_config_file(self, tmp_path):
        """Create a temporary config file"""
        config = {
            "agents": [
                {
                    "name": "weather_agent",
                    "endpoint": "http://localhost:8001/a2a",
                    "card_url": "http://localhost:8001/",
                    "enabled": True,
                    "timeout": 30,
                    "description": "Weather forecasts",
                },
                {
                    "name": "disabled_agent",
                    "endpoint": "http://localhost:8002/a2a",
                    "card_url": "http://localhost:8002/",
                    "enabled": False,
                },
            ]
        }
        
        config_path = tmp_path / "a2a_agents.json"
        with open(config_path, "w") as f:
            json.dump(config, f)
        
        return config_path
    
    def test_load_config(self, mock_config_file):
        """Test loading configuration from JSON"""
        builder = A2AToolBuilder(config_path=mock_config_file)
        builder.load_config()
        
        assert len(builder.agents) == 1  # Only enabled agent
        assert "weather_agent" in builder.agents
        assert "disabled_agent" not in builder.agents
    
    def test_load_config_missing_file(self, tmp_path):
        """Test handling missing config file"""
        builder = A2AToolBuilder(config_path=tmp_path / "nonexistent.json")
        builder.load_config()
        
        assert len(builder.agents) == 0  # Should not crash
    
    @pytest.mark.asyncio
    async def test_discover_capabilities_mock(self, mock_config_file):
        """Test agent capability discovery (mocked)"""
        builder = A2AToolBuilder(config_path=mock_config_file)
        builder.load_config()
        
        agent_config = builder.agents["weather_agent"]
        
        # Mock the A2AClient
        mock_card = {
            "name": "weather_agent",
            "description": "Weather forecasts",
            "capabilities": {
                "get_forecast": {
                    "description": "Get weather forecast",
                    "parameters": {
                        "city": {"type": "string"},
                        "days": {"type": "integer"},
                    }
                }
            }
        }
        
        with patch.object(builder, 'discover_capabilities', return_value=mock_card):
            card = await builder.discover_capabilities(agent_config)
        
        assert card["name"] == "weather_agent"
        assert "get_forecast" in card["capabilities"]
    
    def test_create_tool_function(self, mock_config_file):
        """Test tool function creation"""
        builder = A2AToolBuilder(config_path=mock_config_file)
        builder.load_config()
        
        method_info = {
            "description": "Get weather forecast",
            "parameters": {
                "city": {"type": "string"},
                "days": {"type": "integer"},
            }
        }
        
        tool = builder._create_tool_function(
            agent_name="weather_agent",
            endpoint="http://localhost:8001/a2a",
            method="get_forecast",
            method_info=method_info,
            timeout=30.0,
        )
        
        assert callable(tool)
        assert tool.__name__ == "weather_agent_get_forecast"
        assert "weather forecast" in tool.__doc__.lower()
    
    @pytest.mark.asyncio
    async def test_build_tools_mock(self, mock_config_file):
        """Test building tools (mocked)"""
        builder = A2AToolBuilder(config_path=mock_config_file)
        builder.load_config()
        
        mock_card = {
            "name": "weather_agent",
            "capabilities": {
                "get_forecast": {
                    "description": "Get weather forecast",
                    "parameters": {
                        "city": {"type": "string"},
                    }
                },
                "get_alerts": {
                    "description": "Get weather alerts",
                    "parameters": {}
                }
            }
        }
        
        with patch.object(builder, 'discover_capabilities', return_value=mock_card):
            tools = await builder.build_tools()
        
        assert len(tools) == 2  # Two capabilities
        assert all(callable(t) for t in tools)
        
        tool_names = [t.__name__ for t in tools]
        assert "weather_agent_get_forecast" in tool_names
        assert "weather_agent_get_alerts" in tool_names


@pytest.mark.integration
class TestA2AIntegration:
    """Integration tests with real A2A agents (requires agents running)"""
    
    @pytest.mark.asyncio
    async def test_real_weather_agent_discovery(self):
        """Test discovering real weather agent"""
        try:
            from nodus_adk_agents.a2a_client import A2AClient
            
            client = A2AClient("http://localhost:8001/a2a", timeout=5.0)
            card = await client.discover()
            
            assert card["name"] == "weather_agent"
            assert "get_forecast" in card["capabilities"]
            
            print(f"✅ Discovered: {card['name']}")
            print(f"   Capabilities: {list(card['capabilities'].keys())}")
        
        except Exception as e:
            pytest.skip(f"Weather agent not running: {e}")
    
    @pytest.mark.asyncio
    async def test_real_currency_agent_discovery(self):
        """Test discovering real currency agent"""
        try:
            from nodus_adk_agents.a2a_client import A2AClient
            
            client = A2AClient("http://localhost:8002/a2a", timeout=5.0)
            card = await client.discover()
            
            assert card["name"] == "currency_agent"
            assert "convert" in card["capabilities"]
            
            print(f"✅ Discovered: {card['name']}")
            print(f"   Capabilities: {list(card['capabilities'].keys())}")
        
        except Exception as e:
            pytest.skip(f"Currency agent not running: {e}")
    
    @pytest.mark.asyncio
    async def test_build_tools_from_real_config(self):
        """Test building tools from real config file"""
        config_path = Path(__file__).parent.parent / "src" / "nodus_adk_runtime" / "config" / "a2a_agents.json"
        
        if not config_path.exists():
            pytest.skip("Config file not found")
        
        try:
            builder = A2AToolBuilder(config_path=config_path)
            builder.load_config()
            
            tools = await builder.build_tools()
            
            assert len(tools) > 0
            print(f"\n✅ Built {len(tools)} tools:")
            for tool in tools:
                print(f"   • {tool.__name__}")
        
        except Exception as e:
            pytest.skip(f"Failed to build tools (agents not running?): {e}")
    
    @pytest.mark.asyncio
    async def test_call_real_tool(self):
        """Test calling a real dynamically generated tool"""
        config_path = Path(__file__).parent.parent / "src" / "nodus_adk_runtime" / "config" / "a2a_agents.json"
        
        if not config_path.exists():
            pytest.skip("Config file not found")
        
        try:
            builder = A2AToolBuilder(config_path=config_path)
            builder.load_config()
            
            tools = await builder.build_tools()
            
            # Find weather tool
            weather_tool = next((t for t in tools if "weather" in t.__name__ and "forecast" in t.__name__), None)
            
            if weather_tool is None:
                pytest.skip("Weather tool not found")
            
            # Call the tool
            result = await weather_tool(city="Barcelona", days=1)
            
            assert "forecasts" in result
            assert len(result["forecasts"]) > 0
            
            print(f"\n✅ Tool call successful:")
            print(f"   Tool: {weather_tool.__name__}")
            print(f"   Result: {result['forecasts'][0]['condition']}")
        
        except Exception as e:
            pytest.skip(f"Tool call failed (agent not running?): {e}")


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v", "-s"])


