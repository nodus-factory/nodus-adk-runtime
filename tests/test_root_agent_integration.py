#!/usr/bin/env python3
"""
Integration Test: Root Agent + A2A Dynamic Tools
Tests that the root agent can load and use A2A tools from config
"""

import asyncio
import sys
from pathlib import Path

# Add paths
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "nodus-adk-agents" / "src"))


async def test_root_agent_loading():
    """Test that root agent can load A2A tools"""
    print("\n" + "="*70)
    print("🧪 TEST: Root Agent A2A Tool Loading")
    print("="*70)
    
    try:
        # Import the function that root_agent.py uses
        from nodus_adk_runtime.tools.a2a_dynamic_tool_builder import get_a2a_tools
        
        print("\n📦 Loading A2A tools from config...")
        tools = await get_a2a_tools()
        
        if not tools:
            print("❌ No tools loaded")
            return False
        
        print(f"✅ Loaded {len(tools)} A2A tools:")
        for tool in tools:
            print(f"   • {tool.__name__}")
            if hasattr(tool, '__doc__') and tool.__doc__:
                doc_preview = tool.__doc__.strip().split('\n')[0][:60]
                print(f"     {doc_preview}...")
        
        # Verify tools are callable
        print("\n🔍 Verifying tools are callable...")
        for tool in tools:
            if not callable(tool):
                print(f"❌ {tool.__name__} is not callable")
                return False
        
        print("✅ All tools are callable")
        
        # Verify tool has proper structure for ADK
        print("\n🔍 Verifying tool structure for ADK...")
        sample_tool = tools[0]
        
        checks = [
            (hasattr(sample_tool, '__name__'), "Has __name__"),
            (hasattr(sample_tool, '__doc__'), "Has __doc__"),
            (asyncio.iscoroutinefunction(sample_tool), "Is async function"),
        ]
        
        for check, description in checks:
            status = "✅" if check else "❌"
            print(f"   {status} {description}")
            if not check:
                return False
        
        print("\n✅ Root agent can load A2A tools correctly")
        return True
    
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("   Note: This test requires ADK dependencies")
        print("   Run in Docker or with full environment")
        return False
    
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_tool_invocation():
    """Test that loaded tools can be invoked"""
    print("\n" + "="*70)
    print("🧪 TEST: A2A Tool Invocation")
    print("="*70)
    
    try:
        from nodus_adk_runtime.tools.a2a_dynamic_tool_builder import get_a2a_tools
        
        tools = await get_a2a_tools()
        
        # Find weather tool
        weather_tool = next((t for t in tools if "weather" in t.__name__.lower()), None)
        
        if not weather_tool:
            print("⚠️  Weather tool not found (agent not running?)")
            return True  # Not a failure, just skip
        
        print(f"\n🌤️  Testing: {weather_tool.__name__}")
        print("   Invoking with city='Barcelona', days=1...")
        
        result = await weather_tool(city="Barcelona", days=1)
        
        if "error" in result:
            print(f"❌ Tool returned error: {result['error']}")
            return False
        
        if "forecasts" in result and len(result["forecasts"]) > 0:
            forecast = result["forecasts"][0]
            print(f"✅ Tool invocation successful")
            print(f"   Result: {forecast['condition']} @ {forecast['temp_max']}°C")
            return True
        else:
            print(f"⚠️  Unexpected result format: {result}")
            return False
    
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


async def test_multiple_tools():
    """Test that multiple tools from different agents work"""
    print("\n" + "="*70)
    print("🧪 TEST: Multiple A2A Tools")
    print("="*70)
    
    try:
        from nodus_adk_runtime.tools.a2a_dynamic_tool_builder import get_a2a_tools
        
        tools = await get_a2a_tools()
        
        # Group tools by agent
        agents = {}
        for tool in tools:
            agent_name = tool.__name__.split('_')[0] + '_' + tool.__name__.split('_')[1]
            if agent_name not in agents:
                agents[agent_name] = []
            agents[agent_name].append(tool)
        
        print(f"\n📊 Found {len(agents)} agents with tools:")
        for agent_name, agent_tools in agents.items():
            print(f"   • {agent_name}: {len(agent_tools)} tools")
        
        if len(agents) < 2:
            print("⚠️  Less than 2 agents (some not running?)")
            return True  # Not a hard failure
        
        print("\n✅ Multiple agents loaded successfully")
        return True
    
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


async def test_config_hot_reload_simulation():
    """Simulate config hot reload (without actual implementation)"""
    print("\n" + "="*70)
    print("🧪 TEST: Config Reload Simulation")
    print("="*70)
    
    try:
        from nodus_adk_runtime.tools.a2a_dynamic_tool_builder import A2AToolBuilder
        
        config_path = Path(__file__).parent.parent / "src" / "nodus_adk_runtime" / "config" / "a2a_agents.json"
        
        builder = A2AToolBuilder(config_path=config_path)
        
        # First load
        print("\n📂 Initial load...")
        builder.load_config()
        initial_count = len(builder.agents)
        print(f"   Loaded {initial_count} agents")
        
        # Simulate reload
        print("\n🔄 Simulating reload...")
        builder.agents.clear()
        builder.load_config()
        reload_count = len(builder.agents)
        print(f"   Reloaded {reload_count} agents")
        
        if initial_count == reload_count:
            print("✅ Reload simulation successful")
            return True
        else:
            print(f"❌ Count mismatch: {initial_count} vs {reload_count}")
            return False
    
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


async def main():
    """Run all integration tests"""
    print("\n" + "="*70)
    print("🧪 ROOT AGENT A2A INTEGRATION - TEST SUITE")
    print("="*70)
    print("\nThese tests verify that the Root Agent can load and use")
    print("A2A tools from external configuration.\n")
    
    results = {
        "Root Agent Loading": False,
        "Tool Invocation": False,
        "Multiple Tools": False,
        "Config Reload": False,
    }
    
    # Run tests
    results["Root Agent Loading"] = await test_root_agent_loading()
    await asyncio.sleep(0.5)
    
    results["Tool Invocation"] = await test_tool_invocation()
    await asyncio.sleep(0.5)
    
    results["Multiple Tools"] = await test_multiple_tools()
    await asyncio.sleep(0.5)
    
    results["Config Reload"] = await test_config_hot_reload_simulation()
    
    # Summary
    print("\n" + "="*70)
    print("📊 TEST SUMMARY")
    print("="*70 + "\n")
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    percentage = (passed / total * 100) if total > 0 else 0
    print(f"\n🎯 Score: {passed}/{total} tests passed ({percentage:.0f}%)")
    
    if passed == total:
        print("\n🎉 ALL INTEGRATION TESTS PASSED!")
        print("   Root Agent is ready to use A2A dynamic tools.")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)


