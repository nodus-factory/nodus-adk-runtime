#!/usr/bin/env python3
"""
Manual Test Script for A2A Dynamic Configuration
Tests the complete flow: config → discovery → tools → execution
"""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nodus_adk_runtime.tools.a2a_dynamic_tool_builder import (
    A2AToolBuilder,
    get_a2a_tools,
)


async def test_config_loading():
    """Test 1: Load configuration from JSON"""
    print("\n" + "="*70)
    print("🧪 TEST 1: Config Loading")
    print("="*70)
    
    config_path = Path(__file__).parent.parent / "src" / "nodus_adk_runtime" / "config" / "a2a_agents.json"
    
    if not config_path.exists():
        print(f"❌ Config file not found: {config_path}")
        return False
    
    builder = A2AToolBuilder(config_path=config_path)
    builder.load_config()
    
    if not builder.agents:
        print("❌ No agents loaded from config")
        return False
    
    print(f"✅ Loaded {len(builder.agents)} agents:")
    for name, config in builder.agents.items():
        print(f"   • {name}")
        print(f"     - Endpoint: {config.endpoint}")
        print(f"     - Timeout: {config.timeout}s")
        print(f"     - Description: {config.description}")
    
    return True


async def test_agent_discovery():
    """Test 2: Discover agent capabilities"""
    print("\n" + "="*70)
    print("🧪 TEST 2: Agent Discovery")
    print("="*70)
    
    config_path = Path(__file__).parent.parent / "src" / "nodus_adk_runtime" / "config" / "a2a_agents.json"
    builder = A2AToolBuilder(config_path=config_path)
    builder.load_config()
    
    discovery_success = 0
    discovery_failed = 0
    
    for name, config in builder.agents.items():
        try:
            card = await builder.discover_capabilities(config)
            
            if card:
                print(f"✅ {name}:")
                print(f"   - Name: {card.get('name')}")
                print(f"   - Description: {card.get('description')}")
                print(f"   - Capabilities: {list(card.get('capabilities', {}).keys())}")
                discovery_success += 1
            else:
                print(f"⚠️  {name}: Empty card returned")
                discovery_failed += 1
        
        except Exception as e:
            print(f"❌ {name}: Discovery failed - {str(e)}")
            discovery_failed += 1
    
    print(f"\n📊 Discovery Results: {discovery_success} succeeded, {discovery_failed} failed")
    return discovery_success > 0


async def test_tool_building():
    """Test 3: Build tools dynamically"""
    print("\n" + "="*70)
    print("🧪 TEST 3: Dynamic Tool Building")
    print("="*70)
    
    try:
        tools = await get_a2a_tools()
        
        if not tools:
            print("❌ No tools were built")
            return False
        
        print(f"✅ Built {len(tools)} tools:")
        for tool in tools:
            print(f"   • {tool.__name__}")
            print(f"     - Doc: {tool.__doc__[:60]}..." if tool.__doc__ else "")
        
        return True
    
    except Exception as e:
        print(f"❌ Tool building failed: {e}")
        return False


async def test_tool_execution():
    """Test 4: Execute a real tool"""
    print("\n" + "="*70)
    print("🧪 TEST 4: Tool Execution")
    print("="*70)
    
    try:
        tools = await get_a2a_tools()
        
        # Test Weather Agent
        weather_tool = next((t for t in tools if "weather" in t.__name__.lower() and "forecast" in t.__name__.lower()), None)
        
        if weather_tool:
            print(f"\n🌤️  Testing: {weather_tool.__name__}")
            print("   Calling with: city='Barcelona', days=1")
            
            result = await weather_tool(city="Barcelona", days=1)
            
            if "error" in result:
                print(f"   ❌ Error: {result['error']}")
            else:
                print(f"   ✅ Success!")
                if "forecasts" in result:
                    forecast = result["forecasts"][0]
                    print(f"   📅 Date: {forecast.get('date')}")
                    print(f"   🌡️  Temperature: {forecast.get('temp_min')}°C - {forecast.get('temp_max')}°C")
                    print(f"   ☁️  Condition: {forecast.get('condition')}")
        else:
            print("⚠️  Weather tool not found")
        
        # Test Currency Agent
        currency_tool = next((t for t in tools if "currency" in t.__name__.lower() and "convert" in t.__name__.lower() and "multiple" not in t.__name__.lower()), None)
        
        if currency_tool:
            print(f"\n💱 Testing: {currency_tool.__name__}")
            print("   Calling with: from_currency='EUR', to_currency='USD', amount=100")
            
            result = await currency_tool(from_currency="EUR", to_currency="USD", amount=100)
            
            if "error" in result:
                print(f"   ❌ Error: {result['error']}")
            else:
                print(f"   ✅ Success!")
                print(f"   💰 {result.get('amount')} {result.get('from_currency')} = {result.get('converted_amount'):.2f} {result.get('to_currency')}")
                print(f"   📈 Rate: {result.get('rate'):.4f}")
        else:
            print("⚠️  Currency tool not found")
        
        return True
    
    except Exception as e:
        print(f"❌ Tool execution failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_parallel_execution():
    """Test 5: Execute multiple tools in parallel"""
    print("\n" + "="*70)
    print("🧪 TEST 5: Parallel Tool Execution")
    print("="*70)
    
    try:
        import time
        
        tools = await get_a2a_tools()
        
        weather_tool = next((t for t in tools if "weather" in t.__name__.lower() and "forecast" in t.__name__.lower()), None)
        currency_tool = next((t for t in tools if "currency" in t.__name__.lower() and "convert" in t.__name__.lower() and "multiple" not in t.__name__.lower()), None)
        
        if not weather_tool or not currency_tool:
            print("⚠️  Required tools not found")
            return False
        
        print("\n🚀 Executing 2 tools in parallel...")
        start = time.monotonic()
        
        weather_task = weather_tool(city="Madrid", days=1)
        currency_task = currency_tool(from_currency="EUR", to_currency="GBP", amount=50)
        
        weather_result, currency_result = await asyncio.gather(weather_task, currency_task)
        
        end = time.monotonic()
        
        print(f"✅ Parallel execution completed in {end - start:.2f}s")
        print(f"\n🌤️  Weather: {weather_result['forecasts'][0]['condition']}")
        print(f"💱 Currency: {currency_result['converted_amount']:.2f} {currency_result['to_currency']}")
        
        return True
    
    except Exception as e:
        print(f"❌ Parallel execution failed: {e}")
        return False


async def test_disabled_agent():
    """Test 6: Verify disabled agents are not loaded"""
    print("\n" + "="*70)
    print("🧪 TEST 6: Disabled Agents")
    print("="*70)
    
    config_path = Path(__file__).parent.parent / "src" / "nodus_adk_runtime" / "config" / "a2a_agents.json"
    
    import json
    with open(config_path) as f:
        config = json.load(f)
    
    disabled_agents = [a["name"] for a in config.get("agents", []) if not a.get("enabled", True)]
    
    if not disabled_agents:
        print("ℹ️  No disabled agents in config")
        return True
    
    builder = A2AToolBuilder(config_path=config_path)
    builder.load_config()
    
    for name in disabled_agents:
        if name in builder.agents:
            print(f"❌ Disabled agent '{name}' was loaded!")
            return False
        else:
            print(f"✅ Disabled agent '{name}' correctly skipped")
    
    return True


async def main():
    """Run all tests"""
    print("\n" + "="*70)
    print("🧪 A2A DYNAMIC CONFIGURATION - MANUAL TEST SUITE")
    print("="*70)
    print("\nThis test requires Weather and Currency agents running:")
    print("  - Weather Agent: http://localhost:8001")
    print("  - Currency Agent: http://localhost:8002")
    print("\nStarting tests...\n")
    
    results = {
        "Config Loading": False,
        "Agent Discovery": False,
        "Tool Building": False,
        "Tool Execution": False,
        "Parallel Execution": False,
        "Disabled Agents": False,
    }
    
    # Run tests
    results["Config Loading"] = await test_config_loading()
    await asyncio.sleep(0.5)
    
    results["Agent Discovery"] = await test_agent_discovery()
    await asyncio.sleep(0.5)
    
    results["Tool Building"] = await test_tool_building()
    await asyncio.sleep(0.5)
    
    results["Tool Execution"] = await test_tool_execution()
    await asyncio.sleep(0.5)
    
    results["Parallel Execution"] = await test_parallel_execution()
    await asyncio.sleep(0.5)
    
    results["Disabled Agents"] = await test_disabled_agent()
    
    # Summary
    print("\n" + "="*70)
    print("📊 TEST SUMMARY")
    print("="*70)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    print(f"\n🎯 Score: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED! Configuration system is working correctly.")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Check the output above.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

