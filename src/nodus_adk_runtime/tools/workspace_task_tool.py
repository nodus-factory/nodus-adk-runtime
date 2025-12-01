"""
Workspace Task Tool

Unified tool for Google Workspace operations with context-aware planning.

Architecture:
1. Context Builder: Queries OpenMemory + recent conversation
2. Planner: Mini-agent that creates structured execution plan
3. Executor: Executes plan via MCP Workspace
4. Memory Saver: Stores relevant results in OpenMemory

This tool exposes a single interface to the Root Agent:
- workspace_task(task, scope, constraints)

Internally, it handles all complexity of Gmail, Calendar, Drive, Docs, Sheets.
"""

from typing import Any, Dict, Optional, List
import json
import structlog

logger = structlog.get_logger()


class _WorkspaceTaskToolImpl:
    """
    Unified Workspace tool with context + planning + execution.
    
    This tool encapsulates all Google Workspace complexity behind a single interface.
    The Root Agent only needs to call workspace_task() with a natural language task.
    """
    
    name = "workspace_task"
    description = """
    Resolve Google Workspace tasks (Gmail, Calendar, Drive, Docs, Sheets) with full context awareness.
    
    This tool:
    - Understands natural language requests
    - Resolves pronouns using conversation memory
    - Plans multi-step operations
    - Executes via MCP Gateway
    - Stores results for future reference
    
    Examples:
    - "Busca emails del projecte X" → searches Gmail with context
    - "Què tinc a l'agenda avui?" → lists Calendar events
    - "Llegeix el document del Pepe" → finds and reads Drive document
    - "Respon-li que sí" → replies to last email using memory
    """
    
    input_schema = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Natural language task description (e.g., 'Busca emails de John d'aquesta setmana')"
            },
            "scope": {
                "type": "string",
                "enum": ["gmail", "calendar", "drive", "docs", "sheets", "mixed"],
                "description": "Primary domain for the task (optional, auto-detected if not provided)",
                "default": "mixed"
            },
            "constraints": {
                "type": "string",
                "description": "Optional constraints (e.g., 'només emails no llegits', 'màxim 5 resultats')",
                "default": None
            }
        },
        "required": ["task"]
    }
    
    def __init__(
        self,
        mcp_adapter: Any,
        user_context: Any,
        prompt_service: Any,
        memory_service: Optional[Any] = None
    ):
        """
        Initialize WorkspaceTaskTool.
        
        Args:
            mcp_adapter: MCP adapter for calling external tools
            user_context: User context (tenant_id, user_id, email, token)
            prompt_service: Service for loading prompts from Langfuse
            memory_service: Optional memory service for ADK memory
        """
        self.mcp_adapter = mcp_adapter
        self.user_context = user_context
        self.prompt_service = prompt_service
        self.memory_service = memory_service
        
        # Import sub-components
        from nodus_adk_runtime.tools.workspace.context_builder import WorkspaceContextBuilder
        from nodus_adk_runtime.tools.workspace.planner import WorkspacePlanner
        from nodus_adk_runtime.tools.workspace.executor import WorkspaceExecutor
        # from nodus_adk_runtime.tools.workspace.memory_saver import WorkspaceMemorySaver  # DISABLED
        
        self.context_builder = WorkspaceContextBuilder(mcp_adapter, user_context)
        self.planner = WorkspacePlanner(prompt_service)
        self.executor = WorkspaceExecutor(mcp_adapter, user_context)
        # self.memory_saver = WorkspaceMemorySaver(mcp_adapter, user_context)  # DISABLED
        
        logger.info(
            "WorkspaceTaskTool initialized",
            user_id=user_context.sub,
            tenant_id=user_context.tenant_id
        )
    
    async def __call__(
        self,
        task: str,
        scope: str = "mixed",
        constraints: Optional[str] = None,
        context: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Execute a Workspace task with full context awareness.
        
        Args:
            task: Natural language task description
            scope: Primary domain (gmail, calendar, drive, etc.)
            constraints: Optional constraints
            context: ADK ToolContext (contains conversation history)
            
        Returns:
            Dict with:
                - summary: Human-readable summary
                - results: Structured results
                - actions: Suggested follow-up actions
        """
        logger.info(
            "WorkspaceTaskTool called",
            task=task[:100],
            scope=scope,
            constraints=constraints,
            user_id=self.user_context.sub
        )
        
        try:
            # PHASE 1: Build Context
            logger.info("Phase 1: Building context", task=task[:50])
            workspace_context = await self.context_builder.build(
                task=task,
                scope=scope,
                conversation_context=context
            )
            
            logger.info(
                "Context built",
                projects=len(workspace_context.get("projects", [])),
                people=len(workspace_context.get("people", [])),
                recent_activity=len(workspace_context.get("recent_activity", []))
            )
            
            # PHASE 2: Create Plan
            logger.info("Phase 2: Creating execution plan", task=task[:50])
            plan = await self.planner.create_plan(
                task=task,
                context=workspace_context,
                scope=scope,
                constraints=constraints
            )
            
            logger.info(
                "Plan created",
                clarified_task=plan.get("clarified_task", "")[:100],
                steps=len(plan.get("steps", []))
            )
            
            # PHASE 3: Execute Plan
            logger.info("Phase 3: Executing plan", steps=len(plan.get("steps", [])))
            execution_results = await self.executor.execute(plan)
            
            logger.info(
                "Plan executed",
                successful_steps=execution_results.get("successful_steps", 0),
                failed_steps=execution_results.get("failed_steps", 0)
            )
            
            # PHASE 4: Save to Memory (DISABLED - OpenMemory replaced by automatic Qdrant batch)
            # Memory is now automatically saved via DualWriteMemoryService background batch
            # No need for explicit save here
            logger.info("Phase 4: Memory save (automatic via background batch)")
            # await self.memory_saver.save(
            #     task=task,
            #     plan=plan,
            #     results=execution_results,
            #     context=workspace_context
            # )
            
            # Build final response
            response = {
                "summary": execution_results.get("summary", "Task completed"),
                "results": execution_results.get("data", {}),
                "actions": execution_results.get("suggested_actions", []),
                "metadata": {
                    "clarified_task": plan.get("clarified_task"),
                    "steps_executed": len(plan.get("steps", [])),
                    "scope": scope
                }
            }
            
            logger.info(
                "WorkspaceTaskTool completed successfully",
                task=task[:50],
                summary_length=len(response["summary"])
            )
            
            return response
            
        except Exception as e:
            logger.error(
                "WorkspaceTaskTool failed",
                task=task[:50],
                error=str(e),
                error_type=type(e).__name__
            )
            
            return {
                "summary": f"Ho sento, hi ha hagut un error executant la tasca: {str(e)}",
                "results": {},
                "actions": [],
                "error": str(e)
            }


def create_workspace_task_tool(
    mcp_adapter: Any,
    user_context: Any,
    prompt_service: Any,
    memory_service: Optional[Any] = None
):
    """
    Factory function to create a workspace_task tool compatible with ADK.
    
    Returns a callable that can be used as an ADK tool.
    """
    impl = _WorkspaceTaskToolImpl(
        mcp_adapter=mcp_adapter,
        user_context=user_context,
        prompt_service=prompt_service,
        memory_service=memory_service
    )
    
    async def workspace_task(
        task: str,
        scope: str = "mixed",
        constraints: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Resolve Google Workspace tasks (Gmail, Calendar, Drive, Docs, Sheets) with full context awareness.
        
        This tool:
        - Understands natural language requests
        - Resolves pronouns using conversation memory
        - Plans multi-step operations
        - Executes via MCP Gateway
        - Stores results for future reference
        
        Examples:
        - "Busca emails del projecte X" → searches Gmail with context
        - "Què tinc a l'agenda avui?" → lists Calendar events
        - "Llegeix el document del Pepe" → finds and reads Drive document
        - "Respon-li que sí" → replies to last email using memory
        
        Args:
            task: Natural language task description
            scope: Primary domain (gmail, calendar, drive, docs, sheets, mixed)
            constraints: Optional constraints (e.g., "només emails no llegits")
            
        Returns:
            Dict with summary, results, and suggested actions
        """
        return await impl(task=task, scope=scope, constraints=constraints)
    
    # Set metadata for ADK
    workspace_task.__name__ = "workspace_task"
    workspace_task.__doc__ = impl.description
    
    return workspace_task

