#!/usr/bin/env python3
"""
Demo: How to add a new A2A agent without code rebuild
This simulates the entire workflow
"""

import asyncio
import json
import sys
from pathlib import Path

# Add agents path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "nodus-adk-agents" / "src"))

from nodus_adk_agents.a2a_client import A2AClient


def show_current_config():
    """Show current configuration"""
    config_path = Path(__file__).parent.parent / "src" / "nodus_adk_runtime" / "config" / "a2a_agents.json"
    
    with open(config_path) as f:
        config = json.load(f)
    
    print("\n📋 Current Configuration:")
    print("="*70)
    
    for agent in config.get("agents", []):
        status = "🟢 enabled" if agent.get("enabled", True) else "🔴 disabled"
        print(f"\n{status} {agent['name']}")
        print(f"   Endpoint: {agent['endpoint']}")
        print(f"   Description: {agent.get('description', 'N/A')}")


def simulate_new_agent_config():
    """Simulate adding a new agent to config"""
    print("\n" + "="*70)
    print("🆕 SIMULATING: Adding a new agent")
    print("="*70)
    
    new_agent = {
        "name": "translation_agent",
        "endpoint": "http://localhost:8003/a2a",
        "card_url": "http://localhost:8003/",
        "enabled": True,
        "timeout": 30,
        "description": "Translate text between languages",
        "capabilities": ["translate", "detect_language"]
    }
    
    print("\n📝 New agent configuration:")
    print(json.dumps(new_agent, indent=2))
    
    print("\n✅ To add this agent:")
    print("   1. Edit: nodus-adk-runtime/src/nodus_adk_runtime/config/a2a_agents.json")
    print("   2. Add the JSON above to the 'agents' array")
    print("   3. Restart ADK Runtime: docker compose restart adk-runtime")
    print("   4. OR (future): POST /api/a2a/reload for hot reload")
    
    return new_agent


async def verify_agents_load_dynamically():
    """Verify that config changes would be picked up"""
    print("\n" + "="*70)
    print("🔍 VERIFICATION: Dynamic Loading Test")
    print("="*70)
    
    config_path = Path(__file__).parent.parent / "src" / "nodus_adk_runtime" / "config" / "a2a_agents.json"
    
    # Simulate what the tool builder does
    with open(config_path) as f:
        config = json.load(f)
    
    enabled_agents = [a for a in config.get("agents", []) if a.get("enabled", True)]
    
    print(f"\n✅ Currently {len(enabled_agents)} agents would be loaded:")
    
    for agent in enabled_agents:
        try:
            client = A2AClient(agent["endpoint"], timeout=5.0)
            card = await client.discover()
            
            capabilities = list(card.get("capabilities", {}).keys())
            
            print(f"\n   📦 {agent['name']}")
            print(f"      Capabilities: {capabilities}")
            print(f"      → Would generate {len(capabilities)} tools")
            
            # Show what tools would be created
            for cap in capabilities:
                tool_name = f"{agent['name']}_{cap}"
                print(f"         • {tool_name}()")
        
        except Exception as e:
            print(f"\n   ⚠️  {agent['name']}: Not running ({str(e)[:50]}...)")
    
    return True


async def demo_workflow():
    """Complete demo workflow"""
    print("\n" + "="*70)
    print("🎬 DEMO: Adding a New A2A Agent Without Rebuild")
    print("="*70)
    print("\nThis demo shows how to extend Nodus OS with new agents")
    print("without touching the Root Agent code or rebuilding containers.\n")
    
    # Step 1: Show current config
    print("\n" + "="*70)
    print("STEP 1: Current State")
    print("="*70)
    show_current_config()
    
    # Step 2: Simulate adding new agent
    print("\n" + "="*70)
    print("STEP 2: Add New Agent")
    print("="*70)
    new_agent = simulate_new_agent_config()
    
    # Step 3: Verify dynamic loading
    await verify_agents_load_dynamically()
    
    # Step 4: Show benefits
    print("\n" + "="*70)
    print("STEP 3: Benefits")
    print("="*70)
    
    benefits = [
        ("⚡ No Code Changes", "Root agent code stays untouched"),
        ("🚀 Fast Deployment", "2 minutes vs 10-30 minutes rebuild"),
        ("🔧 Easy Maintenance", "Edit JSON, not Python"),
        ("🌐 Language Agnostic", "Agents in Python, Go, JS, Rust..."),
        ("📦 Modular", "Enable/disable agents with one field"),
        ("🧪 Testable", "Test agents independently before adding"),
    ]
    
    print()
    for emoji_title, desc in benefits:
        print(f"   {emoji_title}")
        print(f"      {desc}")
    
    # Step 5: Real-world example
    print("\n" + "="*70)
    print("STEP 4: Real-World Example")
    print("="*70)
    
    print("\n💼 Scenario: Customer wants CRM integration")
    print("\n   Traditional approach:")
    print("      1. Write CRM tool in root_agent.py")
    print("      2. Update imports and dependencies")
    print("      3. Rebuild Docker image")
    print("      4. Deploy to staging")
    print("      5. Test")
    print("      6. Deploy to production")
    print("      ⏱️  Time: 2-4 hours")
    
    print("\n   With A2A Config approach:")
    print("      1. Deploy CRM Agent service (separate container)")
    print("      2. Add 5 lines to a2a_agents.json")
    print("      3. Restart ADK Runtime")
    print("      ⏱️  Time: 15 minutes")
    
    print("\n" + "="*70)
    print("✅ Demo Complete")
    print("="*70)
    
    print("\n🎯 Key Takeaway:")
    print("   With this system, you can extend Nodus OS capabilities")
    print("   by deploying new agent services and editing a JSON file.")
    print("   No need to modify the core agent code!")


async def main():
    await demo_workflow()
    
    print("\n\n📚 Next Steps:")
    print("   1. Review: nodus-adk-runtime/README_A2A_CONFIG.md")
    print("   2. Create: Your first custom A2A agent")
    print("   3. Add: Agent to a2a_agents.json")
    print("   4. Test: With Llibreta")
    
    print("\n")


if __name__ == "__main__":
    asyncio.run(main())

