"""
Workspace Planner

Mini-agent that creates structured execution plans for Workspace tasks.

Uses a specialized prompt from Langfuse that includes:
- Gmail search syntax
- Calendar date formats
- Drive query syntax
- Pronoun resolution strategies
- Multi-step operation patterns

Output: JSON plan with steps to execute
"""

from typing import Any, Dict, List, Optional
import json
import structlog
from google.adk.models.lite_llm import LiteLlm

logger = structlog.get_logger()


# Fallback prompt if Langfuse is unavailable
FALLBACK_PLANNER_PROMPT = """
You are a Google Workspace planning specialist.

Your job is to create a structured execution plan for Workspace tasks.

INPUT:
- task: Natural language task description
- context: Structured context (projects, people, recent activity, conversation)
- scope: Primary domain (gmail, calendar, drive, etc.)
- constraints: Optional constraints

OUTPUT (JSON):
{
  "clarified_task": "Clear task with pronouns resolved using context",
  "steps": [
    {
      "domain": "gmail" | "calendar" | "drive" | "docs" | "sheets",
      "tool": "exact_mcp_tool_name",
      "params": { /* tool parameters */ },
      "save_as": "variable_name",
      "description": "Human-readable step description"
    }
  ],
  "expected_outcome": "What the user should expect"
}

GMAIL SEARCH SYNTAX:
- "emails no llegits" → query="is:unread in:inbox"
- "emails d'avui" → query="newer_than:1d"
- "emails de [person]" → query="from:person@email.com"

CALENDAR:
- "avui" → time_min=today_start, time_max=today_end (ISO 8601)
- "aquesta setmana" → time_min=week_start, time_max=week_end

DRIVE:
- "documents sobre X" → query="name contains 'X'"
- "fitxers PDF" → query="mimeType='application/pdf'"

PRONOUN RESOLUTION:
Use context.people and context.recent_activity to resolve:
- "el Pepe" → find email in context.people
- "aquell document" → find in context.recent_activity
- "el projecte" → use context.projects[0]

MULTI-STEP PATTERNS:
- Search → Read: First search, then read specific items
- Read → Reply: First read email, then reply to thread
- Search → Summarize: Search multiple items, then summarize

Always output valid JSON.
"""


class WorkspacePlanner:
    """
    Creates structured execution plans for Workspace tasks.
    """
    
    def __init__(self, prompt_service: Any):
        self.prompt_service = prompt_service
        self.model = LiteLlm(model="gpt-4o")  # Use GPT-4 for planning
        
        logger.info("WorkspacePlanner initialized")
    
    async def create_plan(
        self,
        task: str,
        context: Dict[str, Any],
        scope: str = "mixed",
        constraints: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a structured execution plan.
        
        Args:
            task: Natural language task
            context: Structured context from Context Builder
            scope: Primary domain
            constraints: Optional constraints
            
        Returns:
            Dict with:
                - clarified_task: Task with pronouns resolved
                - steps: List of execution steps
                - expected_outcome: What user should expect
        """
        logger.info(
            "Creating Workspace plan",
            task=task[:100],
            scope=scope
        )
        
        # Load prompt from Langfuse
        try:
            instruction = self.prompt_service.get_prompt(
                name="workspace-planner-instruction",
                label="production",
                fallback=FALLBACK_PLANNER_PROMPT
            )
            logger.info("Planner prompt loaded from Langfuse")
        except Exception as e:
            logger.warning(
                "Failed to load prompt from Langfuse, using fallback",
                error=str(e)
            )
            instruction = FALLBACK_PLANNER_PROMPT
        
        # Build user message with task + context
        user_message = self._build_planning_message(task, context, scope, constraints)
        
        logger.debug(
            "Planning message built",
            message_length=len(user_message)
        )
        
        # Call LLM for planning
        try:
            from google.genai import types
            
            # Request structured JSON output
            response = await self.model.generate_content_async(
                contents=[
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=f"{instruction}\n\n{user_message}")]
                    )
                ]
            )
            
            # Extract plan from response
            response_text = response.text
            
            # Try to parse as JSON
            try:
                plan = json.loads(response_text)
                logger.info(
                    "Plan created successfully",
                    clarified_task=plan.get("clarified_task", "")[:100],
                    steps=len(plan.get("steps", []))
                )
                return plan
            except json.JSONDecodeError:
                # If not valid JSON, try to extract JSON from markdown
                if "```json" in response_text:
                    json_start = response_text.find("```json") + 7
                    json_end = response_text.find("```", json_start)
                    json_text = response_text[json_start:json_end].strip()
                    plan = json.loads(json_text)
                    return plan
                else:
                    raise ValueError("LLM did not return valid JSON")
        
        except Exception as e:
            logger.error(
                "Planning failed",
                error=str(e),
                error_type=type(e).__name__
            )
            
            # Return a simple fallback plan
            return {
                "clarified_task": task,
                "steps": [
                    {
                        "domain": scope,
                        "tool": f"{scope}_search",
                        "params": {"query": task},
                        "save_as": "results",
                        "description": f"Search {scope} for: {task}"
                    }
                ],
                "expected_outcome": "Search results"
            }
    
    def _build_planning_message(
        self,
        task: str,
        context: Dict[str, Any],
        scope: str,
        constraints: Optional[str]
    ) -> str:
        """
        Build the planning message for the LLM.
        """
        message_parts = [
            f"TASK: {task}",
            f"SCOPE: {scope}",
        ]
        
        if constraints:
            message_parts.append(f"CONSTRAINTS: {constraints}")
        
        message_parts.append("\nCONTEXT:")
        message_parts.append(json.dumps(context, indent=2, ensure_ascii=False))
        
        message_parts.append("\nCreate a structured execution plan (JSON) to accomplish this task.")
        
        return "\n".join(message_parts)

