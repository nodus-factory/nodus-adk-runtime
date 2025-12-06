"""
Generic HITL Tool using ADK's ToolConfirmation standard.

This tool allows any agent to request user input via HITL in a generic way,
using ADK's built-in ToolConfirmation mechanism.
"""

from typing import Optional, Dict, Any, List
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from typing_extensions import override
import structlog

logger = structlog.get_logger()


def request_user_input(
    question: str,
    input_type: str = "text",  # "text" | "number" | "choice"
    default_value: Optional[Any] = None,
    choices: Optional[List[str]] = None,  # Per opcions múltiples
    tool_context: Optional[ToolContext] = None
) -> Dict[str, Any]:
    """
    Request user input via HITL (genèric, utilitzant ToolConfirmation d'ADK).
    
    This tool uses ADK's standard ToolConfirmation mechanism to pause the invocation
    and request user input. The input value is then available in the tool_context
    when the invocation is resumed.
    
    Args:
        question: Pregunta per mostrar a l'usuari
        input_type: Tipus d'input ("text", "number", "choice")
        default_value: Valor per defecte
        choices: Llista d'opcions (si input_type="choice")
        tool_context: Context del tool (injectat per ADK)
    
    Returns:
        Dict amb el valor introduït per l'usuari o status "waiting_for_input"
    
    Example:
        # Primera crida (demana input):
        result = request_user_input(
            question="Per quin número vols multiplicar?",
            input_type="number",
            default_value=1
        )
        # Returns: {"status": "waiting_for_input", "question": "...", ...}
        
        # Segona crida (després de confirmació):
        # tool_context.tool_confirmation.payload["value"] conté el valor de l'usuari
        result = request_user_input(...)
        # Returns: {"status": "ok", "value": 5, ...}
    """
    if not tool_context:
        logger.error("ToolContext not available for request_user_input")
        return {"error": "ToolContext not available"}
    
    tool_confirmation = tool_context.tool_confirmation
    
    # Primera vegada: demanar confirmació amb payload
    if not tool_confirmation:
        payload = {
            "value": default_value,
            "input_type": input_type,
            "choices": choices,
        }
        
        logger.info(
            "Requesting user input via ToolConfirmation",
            question=question,
            input_type=input_type,
            default_value=default_value
        )
        
        tool_context.request_confirmation(
            hint=question,
            payload=payload
        )
        
        return {
            "status": "waiting_for_input",
            "question": question,
            "input_type": input_type,
            "default_value": default_value,
            "choices": choices,
        }
    
    # Segona vegada: obtenir valor de l'usuari
    if not tool_confirmation.confirmed:
        logger.info("User rejected the input request")
        return {"error": "User rejected the input request"}
    
    # Obtenir valor del payload
    user_value = None
    if tool_confirmation.payload:
        user_value = tool_confirmation.payload.get("value")
    
    logger.info(
        "User input received",
        value=user_value,
        input_type=input_type
    )
    
    return {
        "status": "ok",
        "value": user_value,
        "input_type": input_type,
    }


class RequestUserInputTool(FunctionTool):
    """
    Custom FunctionTool with explicit schema definition for Groq compatibility.
    
    This ensures that the 'question' parameter is clearly marked as required
    in the JSON schema, preventing validation errors with Groq and other LLMs.
    
    Follows the same pattern as QueryMemoryTool and A2ATool.
    """
    
    def __init__(self):
        super().__init__(
            request_user_input,
            require_confirmation=False  # Ho fem manualment dins de la funció
        )
        # Marcar com long_running per activar pausa automàtica quan es demana confirmació
        self.is_long_running = True
    
    @override
    def _get_declaration(self) -> types.FunctionDeclaration:
        """
        Define explicit JSON schema for request_user_input tool.
        
        This ensures Groq and other LLMs understand that 'question' is required.
        Uses parameters_json_schema following the same pattern as QueryMemoryTool and A2ATool.
        """
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question or prompt to show to the user. This is REQUIRED and must be provided.",
                    },
                    "input_type": {
                        "type": "string",
                        "enum": ["text", "number", "choice"],
                        "description": "Type of input expected: 'text' for text input, 'number' for numeric input, 'choice' for selecting from choices. Default: 'text'",
                        "default": "text",
                    },
                    "default_value": {
                        "type": ["string", "number", "null"],
                        "description": "Optional default value to pre-fill in the input field",
                    },
                    "choices": {
                        "type": ["array", "null"],
                        "items": {"type": "string"},
                        "description": "List of choices (required if input_type='choice')",
                    },
                },
                "required": ["question"],  # Explicitly mark question as required
            },
        )


# Crear tool instance
request_user_input_tool = RequestUserInputTool()

