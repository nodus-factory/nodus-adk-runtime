#!/usr/bin/env python3
"""
Simple standalone test for A2A config system
No dependencies on ADK or other complex imports
"""

import asyncio
import json
import sys
from pathlib import Path

# Add agents path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "nodus-adk-agents" / "src"))

from nodus_adk_agents.a2a_client import A2AClient


async def test_1_config_file_exists():
    """Test 1: Config file exists and is valid JSON"""
    print("\n" + "="*70)
    print("🧪 TEST 1: Config File Validation")
    print("="*70)
    
    config_path = Path(__file__).parent.parent / "src" / "nodus_adk_runtime" / "config" / "a2a_agents.json"
    
    if not config_path.exists():
        print(f"❌ Config file not found: {config_path}")
        return False
    
    try:
        with open(config_path) as f:
            config = json.load(f)
        
        print(f"✅ Config file is valid JSON")
        print(f"   Path: {config_path}")
        
        agents = config.get("agents", [])
        print(f"   Agents defined: {len(agents)}")
        
        for agent in agents:
            status = "🟢 enabled" if agent.get("enabled", True) else "🔴 disabled"
            print(f"     • {agent['name']} ({status})")
            print(f"       - Endpoint: {agent['endpoint']}")
        
        return True
    
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


async def test_2_agents_running():
    """Test 2: Check if agents are running"""
    print("\n" + "="*70)
    print("🧪 TEST 2: Agent Availability")
    print("="*70)
    
    config_path = Path(__file__).parent.parent / "src" / "nodus_adk_runtime" / "config" / "a2a_agents.json"
    
    with open(config_path) as f:
        config = json.load(f)
    
    enabled_agents = [a for a in config.get("agents", []) if a.get("enabled", True)]
    
    results = []
    
    for agent_config in enabled_agents:
        name = agent_config["name"]
        endpoint = agent_config["endpoint"]
        
        try:
            client = A2AClient(endpoint, timeout=5.0)
            card = await client.discover()
            
            print(f"✅ {name}")
            print(f"   - Endpoint: {endpoint}")
            print(f"   - Capabilities: {list(card.get('capabilities', {}).keys())}")
            
            results.append(True)
        
        except Exception as e:
            print(f"❌ {name}")
            print(f"   - Endpoint: {endpoint}")
            print(f"   - Error: {str(e)[:80]}")
            
            results.append(False)
    
    return all(results)


async def test_3_discover_capabilities():
    """Test 3: Discover agent capabilities"""
    print("\n" + "="*70)
    print("🧪 TEST 3: Capability Discovery")
    print("="*70)
    
    config_path = Path(__file__).parent.parent / "src" / "nodus_adk_runtime" / "config" / "a2a_agents.json"
    
    with open(config_path) as f:
        config = json.load(f)
    
    enabled_agents = [a for a in config.get("agents", []) if a.get("enabled", True)]
    
    all_capabilities = {}
    
    for agent_config in enabled_agents:
        name = agent_config["name"]
        endpoint = agent_config["endpoint"]
        
        try:
            client = A2AClient(endpoint, timeout=5.0)
            card = await client.discover()
            
            capabilities = card.get("capabilities", {})
            all_capabilities[name] = capabilities
            
            print(f"\n📦 {name}:")
            for method, info in capabilities.items():
                print(f"   • {method}")
                print(f"     - {info.get('description', 'No description')}")
                
                params = info.get("parameters", {})
                if isinstance(params, dict) and "properties" in params:
                    param_names = list(params["properties"].keys())
                    print(f"     - Parameters: {', '.join(param_names) if param_names else 'none'}")
        
        except Exception as e:
            print(f"❌ {name}: {str(e)[:80]}")
    
    return len(all_capabilities) > 0


async def test_4_call_agent():
    """Test 4: Call agent methods"""
    print("\n" + "="*70)
    print("🧪 TEST 4: Agent Method Calls")
    print("="*70)
    
    tests_passed = []
    
    # Test Weather Agent
    try:
        print("\n🌤️  Testing Weather Agent:")
        client = A2AClient("http://localhost:8001/a2a", timeout=10.0)
        
        result = await client.call("get_forecast", {"city": "Barcelona", "days": 1})
        
        if "forecasts" in result:
            forecast = result["forecasts"][0]
            print(f"   ✅ get_forecast")
            print(f"      Date: {forecast['date']}")
            print(f"      Temp: {forecast['temp_min']}°C - {forecast['temp_max']}°C")
            print(f"      Condition: {forecast['condition']}")
            tests_passed.append(True)
        else:
            print(f"   ❌ Unexpected result format")
            tests_passed.append(False)
    
    except Exception as e:
        print(f"   ❌ Error: {str(e)[:80]}")
        tests_passed.append(False)
    
    # Test Currency Agent
    try:
        print("\n💱 Testing Currency Agent:")
        client = A2AClient("http://localhost:8002/a2a", timeout=10.0)
        
        result = await client.call("convert", {
            "from_currency": "EUR",
            "to_currency": "USD",
            "amount": 100
        })
        
        if "converted_amount" in result:
            print(f"   ✅ convert")
            print(f"      {result['amount']} {result['from_currency']} = {result['converted_amount']:.2f} {result['to_currency']}")
            print(f"      Rate: {result['rate']:.4f}")
            tests_passed.append(True)
        else:
            print(f"   ❌ Unexpected result format")
            tests_passed.append(False)
    
    except Exception as e:
        print(f"   ❌ Error: {str(e)[:80]}")
        tests_passed.append(False)
    
    return all(tests_passed)


async def test_5_parallel_calls():
    """Test 5: Parallel agent calls"""
    print("\n" + "="*70)
    print("🧪 TEST 5: Parallel Execution")
    print("="*70)
    
    import time
    
    try:
        weather_client = A2AClient("http://localhost:8001/a2a", timeout=10.0)
        currency_client = A2AClient("http://localhost:8002/a2a", timeout=10.0)
        
        print("\n🚀 Executing 2 calls in parallel...")
        
        start = time.monotonic()
        
        weather_task = weather_client.call("get_forecast", {"city": "Madrid", "days": 1})
        currency_task = currency_client.call("convert", {
            "from_currency": "EUR",
            "to_currency": "GBP",
            "amount": 50
        })
        
        weather_result, currency_result = await asyncio.gather(weather_task, currency_task)
        
        end = time.monotonic()
        
        print(f"✅ Completed in {end - start:.2f}s")
        print(f"\n   Weather: {weather_result['forecasts'][0]['condition']}")
        print(f"   Currency: {currency_result['converted_amount']:.2f} {currency_result['to_currency']}")
        
        return True
    
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False


async def test_6_config_structure():
    """Test 6: Validate config structure for dynamic tool building"""
    print("\n" + "="*70)
    print("🧪 TEST 6: Config Structure for Tool Building")
    print("="*70)
    
    config_path = Path(__file__).parent.parent / "src" / "nodus_adk_runtime" / "config" / "a2a_agents.json"
    
    with open(config_path) as f:
        config = json.load(f)
    
    required_fields = ["name", "endpoint", "card_url", "enabled"]
    optional_fields = ["timeout", "description", "capabilities"]
    
    all_valid = True
    
    for agent in config.get("agents", []):
        name = agent.get("name", "UNKNOWN")
        print(f"\n📋 {name}:")
        
        # Check required fields
        missing = [f for f in required_fields if f not in agent]
        if missing:
            print(f"   ❌ Missing required fields: {', '.join(missing)}")
            all_valid = False
        else:
            print(f"   ✅ All required fields present")
        
        # Check optional fields
        present_optional = [f for f in optional_fields if f in agent]
        if present_optional:
            print(f"   ℹ️  Optional fields: {', '.join(present_optional)}")
        
        # Validate URLs
        if "endpoint" in agent and not agent["endpoint"].startswith("http"):
            print(f"   ⚠️  Endpoint should be a full URL: {agent['endpoint']}")
        
        if "card_url" in agent and not agent["card_url"].startswith("http"):
            print(f"   ⚠️  Card URL should be a full URL: {agent['card_url']}")
    
    return all_valid


async def main():
    """Run all tests"""
    print("\n" + "="*70)
    print("🧪 A2A CONFIG SYSTEM - SIMPLE TEST SUITE")
    print("="*70)
    print("\n⚠️  This test requires agents running:")
    print("   - Weather Agent: http://localhost:8001")
    print("   - Currency Agent: http://localhost:8002")
    print("\nStarting tests...\n")
    
    results = {}
    
    # Test 1: Config file
    results["Config File"] = await test_1_config_file_exists()
    await asyncio.sleep(0.3)
    
    # Test 2: Agents running
    results["Agents Running"] = await test_2_agents_running()
    await asyncio.sleep(0.3)
    
    # Test 3: Discover capabilities
    results["Capability Discovery"] = await test_3_discover_capabilities()
    await asyncio.sleep(0.3)
    
    # Test 4: Call agents
    results["Agent Calls"] = await test_4_call_agent()
    await asyncio.sleep(0.3)
    
    # Test 5: Parallel
    results["Parallel Execution"] = await test_5_parallel_calls()
    await asyncio.sleep(0.3)
    
    # Test 6: Config structure
    results["Config Structure"] = await test_6_config_structure()
    
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
        print("\n🎉 ALL TESTS PASSED!")
        print("   The A2A configuration system is working correctly.")
        print("   You can now add new agents by editing the JSON config!")
        return 0
    elif passed >= total * 0.5:
        print(f"\n⚠️  {total - passed} test(s) failed, but system is partially functional.")
        return 1
    else:
        print(f"\n❌ {total - passed} test(s) failed. System may not be working correctly.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)


