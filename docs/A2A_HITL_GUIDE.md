# 🔗 Guia A2A + HITL: Agent-to-Agent amb Human-In-The-Loop

**Data:** 24 novembre 2025  
**Versió:** 1.0  
**Autor:** Nodus OS Team

---

## 📋 Taula de Continguts

1. [Introducció](#introducció)
2. [Arquitectura A2A](#arquitectura-a2a)
3. [HITL amb A2A Agents](#hitl-amb-a2a-agents)
4. [Flow Complet](#flow-complet)
5. [Implementació Pràctica](#implementació-pràctica)
6. [Troubleshooting](#troubleshooting)

---

## 🎯 Introducció

### Què és A2A (Agent-to-Agent)?

**A2A** és el sistema de comunicació entre agents de Nodus ADK que permet:
- **Agents distribuïts**: Cada agent és un servei independent (Python FastAPI)
- **JSON-RPC**: Protocol estàndard per comunicació
- **Descobriment dinàmic**: El Runtime descobreix agents via HTTP
- **Escalabilitat**: Agents poden córrer en diferents màquines/contenidors

### Què és HITL (Human-In-The-Loop)?

**HITL** és el mecanisme que permet:
- **Pausa automàtica**: Un agent pot pausar-se per esperar confirmació humana
- **SSE (Server-Sent Events)**: Comunicació en temps real amb el frontend
- **Input dinàmic**: Demanar informació a l'usuari (números, text, seleccions)
- **Context preservat**: L'agent manté l'estat mentre espera

---

## 🏗️ Arquitectura A2A

### Components Clau

```
┌─────────────────────────────────────────────────────────────┐
│                     NODUS ADK RUNTIME                        │
│  ┌───────────────┐       ┌──────────────┐                   │
│  │  Root Agent   │───────│ A2A Adapter  │                   │
│  └───────────────┘       └──────┬───────┘                   │
│         │                       │                            │
│         │                       │ JSON-RPC                   │
│         ▼                       ▼                            │
│  ┌────────────────────────────────────┐                     │
│  │      A2A Dynamic Tool Builder      │                     │
│  │  (Descobreix i crea tools per      │                     │
│  │   cada mètode dels A2A agents)     │                     │
│  └────────────────────────────────────┘                     │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        │ HTTP Discovery + JSON-RPC
                        │
        ┌───────────────┴───────────────┬───────────────┐
        │                               │               │
        ▼                               ▼               ▼
┌──────────────────┐         ┌──────────────────┐   ┌─────────────┐
│ Weather Agent    │         │ Currency Agent   │   │ HITL Math   │
│ (port 8003)      │         │ (port 8004)      │   │ Agent       │
│                  │         │                  │   │ (port 8005) │
│ - get_forecast   │         │ - convert        │   │ - multiply  │
│                  │         │ - supported      │   │   _with_    │
│                  │         │   _currencies    │   │   confirm   │
└──────────────────┘         └──────────────────┘   │ - execute_  │
                                                     │   multiply  │
                                                     └─────────────┘
```

### Configuració A2A

**Fitxer:** `nodus-adk-runtime/src/nodus_adk_runtime/config/a2a_agents.json`

```json
{
  "hitl_math_agent": {
    "enabled": true,
    "name": "hitl_math_agent",
    "description": "Mathematical operations with human confirmation",
    "endpoint": "http://localhost:8005/a2a",
    "timeout": 30
  },
  "weather_agent": {
    "enabled": true,
    "name": "weather_agent",
    "description": "Weather forecast information",
    "endpoint": "http://localhost:8003/a2a",
    "timeout": 30
  }
}
```

### Descobriment Dinàmic

**1. Runtime inicia:**
```python
# nodus-adk-runtime/src/nodus_adk_runtime/tools/a2a_dynamic_tool_builder.py

async def discover_and_build_tools():
    """Descobreix tots els A2A agents i crea tools per cada mètode"""
    for agent_name, agent_config in a2a_agents.items():
        if not agent_config["enabled"]:
            continue
        
        # GET http://localhost:8005/ -> retorna JSON amb mètodes
        agent_card = await fetch_agent_card(agent_config["endpoint"])
        
        # Crear un A2ATool per cada mètode
        for method in agent_card["methods"]:
            tool = A2ATool(
                agent_name=agent_name,
                method=method["name"],
                endpoint=agent_config["endpoint"],
                ...
            )
            tools.append(tool)
```

**2. A2A Agent exposa la seva card:**
```python
# nodus-adk-agents/src/nodus_adk_agents/a2a_hitl_math_agent.py

@app.get("/")
async def get_agent_card():
    """Discovery endpoint - retorna la card de l'agent"""
    return {
        "name": "hitl_math_agent",
        "description": "Mathematical operations with HITL",
        "version": "1.0.0",
        "methods": [
            {
                "name": "multiply_with_confirmation",
                "description": "Multiply with human confirmation",
                "parameters": {...}
            },
            {
                "name": "execute_multiplication",
                "description": "Execute confirmed multiplication",
                "parameters": {...}
            }
        ]
    }
```

**3. Root Agent crida el tool:**
```python
# El LLM decideix cridar: hitl_math_agent_multiply_with_confirmation(base_number=21.5)

# A2ATool.run_async():
#   1. Fa POST http://localhost:8005/a2a amb JSON-RPC
#   2. Rep resposta de l'agent
#   3. Si resposta conté "status": "hitl_required", retorna marker HITL
```

---

## 🤝 HITL amb A2A Agents

### Flow HITL Complet

```
USER: "Multiplica 21.5 per un número que demani HITL"
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│ 1. ROOT AGENT (LLM decideix cridar tool)                │
│    hitl_math_agent_multiply_with_confirmation(21.5)     │
└─────────────────┬───────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────┐
│ 2. A2A TOOL (a2a_tool.py)                               │
│    - POST http://localhost:8005/a2a                     │
│    - JSON-RPC: multiply_with_confirmation(21.5)         │
└─────────────────┬───────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────┐
│ 3. HITL MATH AGENT                                      │
│    - Rep: base_number=21.5                              │
│    - Retorna: {                                         │
│        "status": "hitl_required",                       │
│        "action_description": "Multiplicar 21.5 per...", │
│        "action_data": {                                 │
│          "base_number": 21.5,                           │
│          "factor": 2.0,                                 │
│          "input_type": "number"                         │
│        },                                               │
│        "metadata": {                                    │
│          "tool": "request_user_input",                  │
│          "input_type": "number"                         │
│        }                                                │
│      }                                                  │
└─────────────────┬───────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────┐
│ 4. A2A TOOL detecta HITL                                │
│    - if result.get("status") == "hitl_required":        │
│    - Crea hitl_marker amb TOTA la info (incl. metadata)│
│    - Retorna marker al Assistant API                    │
└─────────────────┬───────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────┐
│ 5. ASSISTANT API (assistant.py)                         │
│    - Detecta: if "_hitl_required" in tool_response      │
│    - Crea HITLEvent amb metadata                        │
│    - Envia via SSE al frontend                          │
│    - PAUSA i espera decisió (asyncio.Future)            │
└─────────────────┬───────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────┐
│ 6. FRONTEND (Llibreta)                                  │
│    - SSE rep event amb metadata                         │
│    - AdkHitlCard renderitza:                            │
│      * Si metadata.tool === "request_user_input":       │
│        mostra INPUT FIELD                               │
│      * Sinó: només botons Approve/Reject                │
│    - User entra "5" i clica Approve                     │
│    - POST /v1/hitl/{event_id}/decision                  │
│      { approved: true, reason: "5" }                    │
└─────────────────┬───────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────┐
│ 7. ASSISTANT API rep decisió                            │
│    - asyncio.Future resolt amb decision                 │
│    - Crida A2A agent: execute_multiplication(21.5, 5)   │
│    - Rep resultat: 107.5                                │
│    - Torna a Root Agent amb resultat                    │
└─────────────────┬───────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────┐
│ 8. ROOT AGENT genera resposta final                     │
│    "El resultat és 107.5"                               │
└─────────────────────────────────────────────────────────┘
```

### Components Crític: metadata

El `metadata` és **essencial** perquè el frontend sàpiga què mostrar:

**Backend (HITL Math Agent):**
```python
return {
    "status": "hitl_required",
    "metadata": {
        "tool": "request_user_input",  # ← Indica que necessita input
        "input_type": "number"          # ← Tipus d'input
    }
}
```

**A2ATool (CRÍTIC!):**
```python
# ✅ CORRECTE: Passar metadata
hitl_marker = {
    "_hitl_required": True,
    "metadata": result.get("metadata"),  # ← NO OBLIDAR!
    ...
}

# ❌ INCORRECTE: Sense metadata
hitl_marker = {
    "_hitl_required": True,
    # metadata falta! ← El frontend no mostrarà input field
    ...
}
```

**Frontend (AdkHitlCard.tsx):**
```typescript
const needsInput = event.metadata?.tool === "request_user_input";

{needsInput && (
  <input
    type="text"
    inputMode={inputType === "number" ? "numeric" : "text"}
    value={userInput}
    onChange={(e) => setUserInput(e.target.value)}
    ...
  />
)}
```

---

## 💻 Implementació Pràctica

### Crear un Nou A2A Agent amb HITL

**1. Estructura del Agent:**

```python
# nodus-adk-agents/src/nodus_adk_agents/a2a_my_agent.py

from fastapi import FastAPI
import uvicorn

app = FastAPI()

# 1️⃣ DISCOVERY ENDPOINT (obligatori!)
@app.get("/")
async def get_agent_card():
    return {
        "name": "my_agent",
        "description": "My custom agent",
        "version": "1.0.0",
        "methods": [
            {
                "name": "action_with_confirmation",
                "description": "Action that needs confirmation",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "param1": {"type": "string"}
                    }
                }
            }
        ]
    }

# 2️⃣ JSON-RPC ENDPOINT
@app.post("/a2a")
async def handle_jsonrpc(request: JSONRPCRequest):
    if request.method == "action_with_confirmation":
        return await action_with_confirmation(**request.params)
    # ...

# 3️⃣ MÈTODE AMB HITL
async def action_with_confirmation(param1: str) -> dict:
    """
    Primera fase: Retorna marker HITL
    """
    return {
        "status": "hitl_required",
        "action_type": "custom_action",
        "action_description": f"Executar acció amb {param1}",
        "action_data": {
            "param1": param1,
            "input_type": "text"  # Si vols input field
        },
        "metadata": {
            "tool": "request_user_input",  # ← Per mostrar input
            "input_type": "text"            # ← Tipus d'input
        },
        "question": f"Vols executar l'acció amb {param1}?",
        "preview": f"Acció: {param1}"
    }

# 4️⃣ MÈTODE D'EXECUCIÓ (després d'aprovació)
async def execute_action(param1: str, user_input: str = None) -> dict:
    """
    Segona fase: Executa l'acció aprovada
    """
    result = do_something(param1, user_input)
    return {
        "status": "success",
        "result": result
    }
```

**2. Configurar al Runtime:**

```json
// nodus-adk-runtime/src/nodus_adk_runtime/config/a2a_agents.json
{
  "my_agent": {
    "enabled": true,
    "name": "my_agent",
    "description": "My custom agent",
    "endpoint": "http://localhost:8006/a2a",
    "timeout": 30
  }
}
```

**3. Actualitzar Root Agent Prompt:**

```python
# nodus-adk-agents/src/nodus_adk_agents/root_agent.py

instruction = """
...
- **my_agent**: Custom actions
  * `my_agent_action_with_confirmation`: Action with HITL
  
**Exemple d'ús de my_agent:**
User: "Fes una acció custom amb 'test'"
Raonament: Necessito fer una acció custom que requereix confirmació.
Accions:
  1. my_agent_action_with_confirmation(param1="test")
     → System: ✓ HITL confirmation request sent
     → User approves with input: "confirmed"
  2. Després de l'aprovació, el sistema executa automàticament l'acció
Final: "L'acció s'ha executat correctament amb el teu input."
"""
```

**4. Executar l'Agent:**

```bash
cd /Users/quirze/Factory/nodus-os-adk/nodus-adk-agents
nohup python3 -m nodus_adk_agents.a2a_my_agent > /tmp/my_agent.log 2>&1 &
```

---

## 🔧 Troubleshooting

### Problema: HITL card sense input field

**Símptomes:**
- La HITL card apareix
- Només mostra botons Approve/Reject
- No hi ha input field per entrar dades

**Causa:**
El `metadata` no està arribant al frontend.

**Solució:**
1. **Verificar que l'agent retorna metadata:**
   ```python
   # A2A Agent
   return {
       "status": "hitl_required",
       "metadata": {  # ← VERIFICAR!
           "tool": "request_user_input",
           "input_type": "number"
       }
   }
   ```

2. **Verificar que A2ATool passa metadata:**
   ```python
   # nodus-adk-runtime/src/nodus_adk_runtime/tools/a2a_tool.py
   hitl_marker = {
       "_hitl_required": True,
       "metadata": result.get("metadata"),  # ← VERIFICAR!
       ...
   }
   ```

3. **Verificar logs del Runtime:**
   ```bash
   docker logs nodus-adk-runtime --since 2m | grep "Sending HITL event"
   # Hauria de mostrar: metadata={'tool': 'request_user_input', ...}
   ```

### Problema: A2A Agent no descobert

**Símptomes:**
- El tool no apareix al Root Agent
- Errors de "tool not found"

**Solució:**
1. **Verificar que l'agent està corrent:**
   ```bash
   curl http://localhost:8005/
   # Hauria de retornar la agent card JSON
   ```

2. **Verificar configuració:**
   ```json
   // a2a_agents.json
   {
     "hitl_math_agent": {
       "enabled": true,  // ← VERIFICAR!
       "endpoint": "http://localhost:8005/a2a"  // ← PORT CORRECTE!
     }
   }
   ```

3. **Verificar logs de descobriment:**
   ```bash
   docker logs nodus-adk-runtime --since 1m | grep "A2ATool created"
   ```

### Problema: SSE desconnectat

**Símptomes:**
- HITL card no apareix
- Logs mostren "HITL event queued" però no "Sending"

**Solució:**
1. **Refresh del navegador** (F5) per reconnectar SSE
2. **Verificar token vàlid:**
   - Si JWT ha expirat, fer logout/login
3. **Verificar logs:**
   ```bash
   docker logs nodus-adk-runtime | grep "HITL SSE"
   # Hauria de mostrar: "HITL SSE client connected"
   ```

### Problema: Resultat final no es mostra

**Símptomes:**
- HITL card apareix i s'aprova
- Però no hi ha resposta final amb el resultat

**Causa:**
El mapping entre `multiply_with_confirmation` i `execute_multiplication` pot estar incorrecte.

**Solució:**
Verificar `assistant.py`:
```python
# nodus-adk-runtime/src/nodus_adk_runtime/api/assistant.py

if agent_name == "hitl_math_agent" and execution_method == "execute_multiplication":
    # Extreure user input
    user_factor = decision.get("reason")
    if user_factor:
        try:
            factor = float(user_factor)
        except (ValueError, TypeError):
            factor = action_data.get("factor", 2.0)
    else:
        factor = action_data.get("factor", 2.0)
    
    execution_params = {
        "base_number": action_data.get("base_number"),
        "factor": factor
    }
```

---

## 📚 Referències

- **ADK Python**: `/Users/quirze/Factory/nodus-os-adk/adk-python`
- **A2A Agents**: `/Users/quirze/Factory/nodus-os-adk/nodus-adk-agents/src/nodus_adk_agents`
- **ADK Runtime**: `/Users/quirze/Factory/nodus-os-adk/nodus-adk-runtime`
- **Llibreta Frontend**: `/Users/quirze/Factory/nodus-os-adk/nodus-llibreta/client`

---

## ✅ Checklist per Nou A2A Agent amb HITL

- [ ] Crear fitxer agent a `nodus-adk-agents/src/nodus_adk_agents/`
- [ ] Implementar endpoint `GET /` (discovery)
- [ ] Implementar endpoint `POST /a2a` (JSON-RPC)
- [ ] Mètode retorna `status: "hitl_required"` amb `metadata`
- [ ] Mètode d'execució (`execute_*`) implementat
- [ ] Afegir a `a2a_agents.json` amb `enabled: true`
- [ ] Actualitzar prompt del Root Agent amb exemples
- [ ] Executar agent: `python3 -m nodus_adk_agents.a2a_my_agent`
- [ ] Verificar descobriment: `curl http://localhost:PORT/`
- [ ] Test complet: missatge → HITL card → input → aprovació → resultat

---

**Última actualització:** 24 novembre 2025  
**Versió Nodus ADK:** 0.1.0  
**Estat:** ✅ Production Ready

