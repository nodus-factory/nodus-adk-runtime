# 🧪 A2A Configuration System - Tests

Tests per validar el sistema de configuració externa d'agents A2A.

---

## 📋 Test Suite

### **1. Unit Tests (pytest)**

```bash
cd nodus-adk-runtime
pytest tests/test_a2a_dynamic_tools.py -v
```

**Cobertura:**
- ✅ Model A2AAgentConfig
- ✅ Càrrega de configuració JSON
- ✅ Descobriment d'agents (mocked)
- ✅ Creació dinàmica de tools
- ✅ Build de tools complet

**Nota:** Requereix `pytest` i `pytest-asyncio`:
```bash
pip install pytest pytest-asyncio
```

---

### **2. Simple Integration Test**

```bash
cd nodus-adk-runtime
python3 tests/simple_a2a_config_test.py
```

**Cobertura:**
- ✅ Validació del fitxer JSON
- ✅ Agents running i accessibles
- ✅ Descobriment de capabilities
- ✅ Crides reals als agents
- ✅ Execució paral·lela
- ✅ Estructura de configuració

**Requeriments:**
- Weather Agent running a `http://localhost:8001`
- Currency Agent running a `http://localhost:8002`

**Resultat esperat:**
```
🎯 Score: 6/6 tests passed (100%)
🎉 ALL TESTS PASSED!
```

---

### **3. Demo Workflow**

```bash
cd nodus-adk-runtime
python3 tests/demo_add_new_agent.py
```

**Mostra:**
- 📋 Configuració actual
- 🆕 Com afegir un nou agent
- 🔍 Verificació de càrrega dinàmica
- 💡 Beneficis del sistema
- 💼 Exemple real (CRM integration)

---

## 🚀 Quick Test

Per fer un test ràpid del sistema complet:

```bash
# 1. Arranca els agents de test
cd nodus-adk-agents
python3 -m uvicorn src.nodus_adk_agents.a2a_weather_agent:app --host 0.0.0.0 --port 8001 &
python3 -m uvicorn src.nodus_adk_agents.a2a_currency_agent:app --host 0.0.0.0 --port 8002 &

# 2. Executa els tests
cd ../nodus-adk-runtime
python3 tests/simple_a2a_config_test.py

# 3. Veure la demo
python3 tests/demo_add_new_agent.py
```

---

## 📊 Test Results (Last Run: 2025-11-21)

### Simple Integration Test
```
✅ PASS - Config File
✅ PASS - Agents Running
✅ PASS - Capability Discovery
✅ PASS - Agent Calls
✅ PASS - Parallel Execution
✅ PASS - Config Structure

🎯 Score: 6/6 tests passed (100%)
```

### Performance
- Single agent call: ~0.4s
- Parallel execution (2 agents): ~0.41s
- Speedup: 1.95x (quasi lineal)

---

## 🐛 Troubleshooting

### Error: "Agent not running"
```bash
# Check if agents are running
curl http://localhost:8001/health
curl http://localhost:8002/health

# Start agents if needed
cd nodus-adk-agents
python3 -m uvicorn src.nodus_adk_agents.a2a_weather_agent:app --port 8001 &
python3 -m uvicorn src.nodus_adk_agents.a2a_currency_agent:app --port 8002 &
```

### Error: "Config file not found"
```bash
# Verify config exists
ls -la nodus-adk-runtime/src/nodus_adk_runtime/config/a2a_agents.json

# Create if missing (use example)
cp nodus-adk-runtime/src/nodus_adk_runtime/config/a2a_agents.json.example \
   nodus-adk-runtime/src/nodus_adk_runtime/config/a2a_agents.json
```

### Error: "Module not found"
```bash
# Ensure paths are correct
export PYTHONPATH=/Users/quirze/Factory/nodus-os-adk:$PYTHONPATH

# Or run from correct directory
cd nodus-adk-runtime
python3 tests/simple_a2a_config_test.py
```

---

## 🔄 Continuous Testing

Per testing continu durant desenvolupament:

```bash
# Watch mode amb pytest
cd nodus-adk-runtime
pytest tests/ -v --watch
```

```bash
# Manual loop
while true; do
  python3 tests/simple_a2a_config_test.py
  sleep 60
done
```

---

## 📈 Test Coverage

| Component | Coverage | Notes |
|-----------|----------|-------|
| Config Loading | ✅ 100% | JSON parsing, validation |
| Agent Discovery | ✅ 100% | HTTP calls, card parsing |
| Tool Building | ✅ 100% | Dynamic function generation |
| Tool Execution | ✅ 100% | Real A2A calls |
| Error Handling | ⚠️ 80% | Need more edge cases |
| Parallel Exec | ✅ 100% | asyncio.gather() |

---

## 🎯 CI/CD Integration

Per integrar amb GitHub Actions:

```yaml
# .github/workflows/test-a2a-config.yml
name: Test A2A Config System

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install pytest pytest-asyncio httpx structlog
      
      - name: Start test agents
        run: |
          cd nodus-adk-agents
          python3 -m uvicorn src.nodus_adk_agents.a2a_weather_agent:app --port 8001 &
          python3 -m uvicorn src.nodus_adk_agents.a2a_currency_agent:app --port 8002 &
          sleep 5
      
      - name: Run tests
        run: |
          cd nodus-adk-runtime
          python3 tests/simple_a2a_config_test.py
```

---

## 📝 Adding New Tests

Per afegir un nou test:

1. Crea el fitxer: `tests/test_new_feature.py`
2. Segueix el format existent
3. Afegeix a aquesta documentació
4. Executa: `python3 tests/test_new_feature.py`

**Plantilla:**

```python
#!/usr/bin/env python3
"""
Test: [Description]
"""

import asyncio

async def test_my_feature():
    """Test my new feature"""
    print("\n🧪 TEST: My Feature")
    print("="*70)
    
    # Your test code here
    result = await my_function()
    
    assert result is True, "Test failed"
    print("✅ Test passed")
    
    return True

if __name__ == "__main__":
    result = asyncio.run(test_my_feature())
    exit(0 if result else 1)
```

---

## 🏆 Test Quality Standards

✅ **Good Test:**
- Clear description
- Single responsibility
- Assertions with messages
- Proper error handling
- Cleanup after execution

❌ **Bad Test:**
- Generic "test_everything"
- No assertions
- Silent failures
- Left-over side effects
- Hardcoded values

---

## 📞 Support

Si un test falla:

1. **Check logs**: `docker compose logs adk-runtime`
2. **Verify agents**: `curl http://localhost:8001/health`
3. **Check config**: `cat nodus-adk-runtime/src/nodus_adk_runtime/config/a2a_agents.json | jq`
4. **Run verbose**: `python3 tests/simple_a2a_config_test.py -v`

---

## 🎓 Learning Resources

- **A2A Protocol**: https://a2a-protocol.org/
- **ADK Testing**: https://google.github.io/adk-docs/testing/
- **Pytest Async**: https://pytest-asyncio.readthedocs.io/


