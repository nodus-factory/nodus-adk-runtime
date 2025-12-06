"""
Workspace Executor

Executes structured plans via MCP Gateway (Google Workspace).

Handles:
- Step-by-step execution
- State management (save_as variables)
- Error recovery
- Result summarization
"""

from typing import Any, Dict, List, Optional
import json
import structlog
from google.adk.models.lite_llm import LiteLlm

logger = structlog.get_logger()


class WorkspaceExecutor:
    """
    Executes Workspace plans via MCP Gateway.
    """
    
    def __init__(self, mcp_adapter: Any, user_context: Any):
        self.mcp_adapter = mcp_adapter
        self.user_context = user_context
        self.model = LiteLlm(model="gpt-4o")  # For final summarization
        
        logger.info("WorkspaceExecutor initialized")
    
    async def execute(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a structured plan.
        
        Args:
            plan: Plan from Planner with steps
            
        Returns:
            Dict with:
                - summary: Human-readable summary
                - data: Structured results
                - suggested_actions: Follow-up actions
                - successful_steps: Count
                - failed_steps: Count
        """
        logger.info(
            "Executing Workspace plan",
            clarified_task=plan.get("clarified_task", "")[:100],
            steps=len(plan.get("steps", []))
        )
        
        steps = plan.get("steps", [])
        state = {}  # Store intermediate results
        results = []
        failed_steps = []
        
        # Execute each step
        for i, step in enumerate(steps):
            logger.info(
                f"Executing step {i+1}/{len(steps)}",
                domain=step.get("domain"),
                tool=step.get("tool"),
                description=step.get("description", "")[:100]
            )
            
            try:
                # Resolve params using state
                resolved_params = self._resolve_params(step.get("params", {}), state)
                
                # Call MCP tool
                result = await self.mcp_adapter.call_tool(
                    server_id="google-workspace",
                    tool_name=step["tool"],
                    params=resolved_params,
                    context=self.user_context
                )
                
                # Save result to state
                save_as = step.get("save_as")
                if save_as:
                    state[save_as] = result
                
                results.append({
                    "step": i + 1,
                    "description": step.get("description"),
                    "success": True,
                    "result": result
                })
                
                logger.info(
                    f"Step {i+1} completed successfully",
                    save_as=save_as
                )
                
            except Exception as e:
                logger.error(
                    f"Step {i+1} failed",
                    error=str(e),
                    error_type=type(e).__name__
                )
                
                failed_steps.append({
                    "step": i + 1,
                    "description": step.get("description"),
                    "error": str(e)
                })
                
                results.append({
                    "step": i + 1,
                    "description": step.get("description"),
                    "success": False,
                    "error": str(e)
                })
        
        # Generate human-readable summary
        summary = await self._generate_summary(
            plan=plan,
            results=results,
            failed_steps=failed_steps
        )
        
        logger.info(
            "Plan execution completed",
            successful_steps=len(steps) - len(failed_steps),
            failed_steps=len(failed_steps)
        )
        
        return {
            "summary": summary,
            "data": state,
            "results": results,
            "suggested_actions": self._extract_suggested_actions(results),
            "successful_steps": len(steps) - len(failed_steps),
            "failed_steps": len(failed_steps)
        }
    
    def _resolve_params(
        self,
        params: Dict[str, Any],
        state: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Resolve parameters using state variables.
        
        Example:
        - params = {"message_id": "$results.messages[0].id"}
        - state = {"results": {"messages": [{"id": "123"}]}}
        - resolved = {"message_id": "123"}
        """
        resolved = {}
        
        for key, value in params.items():
            if isinstance(value, str) and value.startswith("$"):
                # Reference to state variable
                var_path = value[1:].split(".")
                resolved_value = state
                
                for part in var_path:
                    # Handle array indexing
                    if "[" in part:
                        array_name = part[:part.index("[")]
                        index = int(part[part.index("[")+1:part.index("]")])
                        resolved_value = resolved_value.get(array_name, [])[index]
                    else:
                        resolved_value = resolved_value.get(part)
                    
                    if resolved_value is None:
                        break
                
                resolved[key] = resolved_value
            else:
                resolved[key] = value
        
        return resolved
    
    async def _generate_summary(
        self,
        plan: Dict[str, Any],
        results: List[Dict[str, Any]],
        failed_steps: List[Dict[str, Any]]
    ) -> str:
        """
        Generate human-readable summary of execution.
        """
        try:
            # Build summary prompt
            summary_prompt = f"""
Generate a concise, natural summary of the following Workspace operation:

TASK: {plan.get("clarified_task")}

RESULTS:
{json.dumps(results, indent=2, ensure_ascii=False)}

FAILED STEPS:
{json.dumps(failed_steps, indent=2, ensure_ascii=False) if failed_steps else "None"}

Generate a summary in the same language as the task.
Focus on what was accomplished and any important findings.
If there were failures, mention them briefly.
Keep it conversational and helpful.
"""
            
            from google.genai import types
            
            response = await self.model.generate_content_async(
                contents=[
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=summary_prompt)]
                    )
                ]
            )
            
            return response.text.strip()
            
        except Exception as e:
            logger.warning(
                "Failed to generate summary",
                error=str(e)
            )
            
            # Fallback summary
            if failed_steps:
                return f"He executat {len(results) - len(failed_steps)}/{len(results)} passos correctament. Hi ha hagut {len(failed_steps)} errors."
            else:
                return f"He completat la tasca: {plan.get('clarified_task')}"
    
    def _extract_suggested_actions(self, results: List[Dict[str, Any]]) -> List[str]:
        """
        Extract suggested follow-up actions from results.
        """
        actions = []
        
        # Simple heuristics for now
        for result in results:
            if result.get("success"):
                result_data = result.get("result", {})
                
                # If we found emails, suggest reading them
                if "messages" in str(result_data):
                    actions.append("Llegir el contingut dels emails trobats")
                
                # If we found events, suggest details
                if "events" in str(result_data):
                    actions.append("Veure detalls dels esdeveniments")
                
                # If we found documents, suggest opening
                if "files" in str(result_data):
                    actions.append("Obrir els documents trobats")
        
        return list(set(actions))[:3]  # Max 3 unique actions



