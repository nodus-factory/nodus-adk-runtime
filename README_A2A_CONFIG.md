# 🔧 Configuració Externa d'Agents A2A

## 🎯 Objectiu

Afegir/eliminar agents A2A **sense rebuild ni restart** del sistema.

---

## 📝 Com Funciona

### 1. **Configuració JSON**

Edita `nodus-adk-runtime/src/nodus_adk_runtime/config/a2a_agents.json`:

```json
{
  "agents": [
    {
      "name": "weather_agent",
      "endpoint": "http://localhost:8001/a2a",
      "card_url": "http://localhost:8001/",
      "enabled": true,
      "timeout": 30,
      "description": "Weather forecasts",
      "capabilities": ["get_forecast"]
    },
    {
      "name": "nou_agent",
      "endpoint": "http://localhost:8003/a2a",
      "card_url": "http://localhost:8003/",
      "enabled": true,
      "timeout": 30,
      "description": "Descripció del nou agent"
    }
  ]
}
```

### 2. **Hot Reload (sense restart)**

Crida l'endpoint de reload:

```bash
curl -X POST http://localhost:8000/api/a2a/reload \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 3. **Automàtic (amb restart)**

Simplement edita el JSON i reinicia el servei. Els tools es carregaran automàticament.

---

## 🚀 Afegir un Nou Agent A2A

### Pas 1: Arrancar el nou agent

```bash
# Exemple: nou agent de traducció al port 8003
python -m my_translation_agent --port 8003
```

### Pas 2: Afegir al config JSON

```json
{
  "name": "translation_agent",
  "endpoint": "http://localhost:8003/a2a",
  "card_url": "http://localhost:8003/",
  "enabled": true,
  "timeout": 30,
  "description": "Translate text between languages"
}
```

### Pas 3: Reload (o restart)

```bash
# Opció A: Hot reload
curl -X POST http://localhost:8000/api/a2a/reload

# Opció B: Restart
docker compose restart adk-runtime
```

### Pas 4: Verificar

```bash
# Comprovar que el tool s'ha creat
curl http://localhost:8000/api/tools | jq '.[] | select(.name | contains("translation"))'
```

---

## ✏️ Modificar un Agent Existent

### Deshabilitar temporalment:

```json
{
  "name": "weather_agent",
  "enabled": false,  // ← Canvia a false
  ...
}
```

### Canviar endpoint:

```json
{
  "name": "weather_agent",
  "endpoint": "http://new-server:8001/a2a",  // ← Nou servidor
  ...
}
```

### Ajustar timeout:

```json
{
  "name": "slow_agent",
  "timeout": 120,  // ← Més temps per agents lents
  ...
}
```

Després: Hot reload o restart.

---

## 🔍 Descobriment Automàtic

El sistema descobreix automàticament les capacitats de cada agent via **Agent Card**:

```
GET http://localhost:8001/
→ Agent Card amb capabilities
→ Auto-genera tools Python
→ Afegeix al root agent
```

Si l'agent exposa nous mètodes, el reload els detectarà automàticament.

---

## 📊 Monitorització

### Logs al startup:

```
[info] Loaded A2A agent config       name=weather_agent endpoint=http://localhost:8001/a2a
[info] Discovered A2A agent capabilities name=weather_agent capabilities=['get_forecast']
[info] Created A2A tool               name=weather_agent_get_forecast
[info] A2A tools loaded from config   count=2 tools=['weather_agent_get_forecast', 'currency_agent_convert']
```

### Verificar tools actius:

```bash
# Via API (si tens endpoint de debug)
curl http://localhost:8000/api/debug/tools

# Via logs
docker compose logs adk-runtime | grep "A2A tools"
```

---

## 🛡️ Seguretat

### Autenticació (futur):

```json
{
  "name": "secure_agent",
  "endpoint": "https://secure-server:8001/a2a",
  "auth": {
    "type": "bearer",
    "token_env": "SECURE_AGENT_TOKEN"
  }
}
```

### Rate limiting (futur):

```json
{
  "name": "rate_limited_agent",
  "rate_limit": {
    "calls_per_minute": 10,
    "burst": 5
  }
}
```

---

## 🧪 Testing

### Test d'un agent individual:

```bash
# Descobrir capabilities
curl http://localhost:8001/

# Cridar directament
curl -X POST http://localhost:8001/a2a \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"get_forecast","params":{"city":"barcelona"},"id":1}'
```

### Test via root agent:

```bash
# Pregunta a Llibreta
"Quin temps farà demà a Barcelona?"

# Comprova logs per veure la crida A2A
docker compose logs adk-runtime | grep "A2A tool call"
```

---

## 📦 Estructura de Fitxers

```
nodus-adk-runtime/
├── src/nodus_adk_runtime/
│   ├── config/
│   │   └── a2a_agents.json          ← Configuració agents
│   └── tools/
│       └── a2a_dynamic_tool_builder.py  ← Builder automàtic
│
nodus-adk-agents/
└── src/nodus_adk_agents/
    ├── root_agent.py                 ← Carrega tools automàticament
    ├── a2a_weather_agent.py          ← Agent exemple
    └── a2a_currency_agent.py         ← Agent exemple
```

---

## 🔄 Workflow de Desenvolupament

1. **Desenvolupar nou agent** (Python, Go, JS, etc.)
2. **Implementar A2A Protocol** (JSON-RPC 2.0 + Agent Card)
3. **Arrancar agent** en un port separat
4. **Afegir al JSON** de configuració
5. **Hot reload** o restart
6. **Testejar** via Llibreta

**No cal tocar codi del Root Agent! 🎉**

---

## 💡 Millores Futures

- [ ] Hot reload sense reiniciar sessions d'usuari
- [ ] Descobriment automàtic via service discovery (Consul, etcd)
- [ ] Health checks periòdics dels agents
- [ ] Retry automàtic si un agent falla
- [ ] Caching de respostes A2A
- [ ] Métriques per agent (latència, errors, etc.)
- [ ] Circuit breaker per agents problemàtics
- [ ] A/B testing entre diferents versions d'agents

---

## 🆘 Troubleshooting

### L'agent no apareix als tools:

1. Comprova que `enabled: true` al JSON
2. Comprova que l'agent està running (`curl http://localhost:PORT/health`)
3. Comprova logs: `docker compose logs adk-runtime | grep A2A`
4. Prova descobrir manualment: `curl http://localhost:PORT/`

### Error "Failed to discover A2A agent":

- L'agent no està running
- URL incorrecte al JSON
- Agent no exposa Agent Card a `/`
- Firewall/networking issue

### El reload no funciona:

- Endpoint de reload no implementat encara (fer-ho!)
- Permissos insuficients
- JSON malformat (comprova amb `jq`)

---

## 📚 Referències

- **A2A Protocol**: https://a2a-protocol.org/
- **Agent Cards Spec**: https://a2a-protocol.org/dev/specification/#agent-cards
- **JSON-RPC 2.0**: https://www.jsonrpc.org/specification

