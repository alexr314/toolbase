"""
Example tool for Toolbase.

This is a template showing the basic structure of a tool function using
the Orchestral AI framework. All tools must follow this format.
"""

from orchestral import define_tool
import json


@define_tool
def example_tool(input_value: float, option: str = "default") -> str:
    """
    An example tool that demonstrates the basic Orchestral structure.

    This tool processes a numeric input value and returns structured
    results. It shows the recommended pattern for writing tools:
    - Use @define_tool decorator
    - Clear function signature with type hints
    - Detailed docstring explaining purpose and parameters
    - Return JSON string (not dict)
    - Include error handling

    Args:
        input_value: A numeric input value to process
        option: An optional string parameter (default: "default")

    Returns:
        JSON string with:
        - status: "ok" or "error"
        - result: The processed value (input * 2)
        - message: A status message
        - input_echo: Echo of input parameters

    Example:
        For input_value=42.0 and option="test", returns doubled value (84.0)
    """
    try:
        # Process the input
        result = input_value * 2

        # Return structured JSON output
        return json.dumps({
            "status": "ok",
            "result": result,
            "message": f"Processed successfully with option: {option}",
            "input_echo": {
                "input_value": input_value,
                "option": option
            }
        })

    except Exception as e:
        # Always return JSON even on error
        return json.dumps({
            "status": "error",
            "message": str(e)
        })


@define_tool
def text_processor(text: str, uppercase: bool = True) -> str:
    """
    Another example tool showing text processing.

    This tool demonstrates a simpler use case with boolean parameters.
    It converts text to uppercase or lowercase based on the parameter.

    Args:
        text: Input text to process
        uppercase: If True, convert to uppercase; if False, convert to lowercase

    Returns:
        JSON string with:
        - status: "ok" or "error"
        - original: Original input text
        - processed: Processed text
        - operation: Description of what was done
    """
    try:
        if uppercase:
            processed = text.upper()
            operation = "converted to uppercase"
        else:
            processed = text.lower()
            operation = "converted to lowercase"

        return json.dumps({
            "status": "ok",
            "original": text,
            "processed": processed,
            "operation": operation
        })

    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": str(e)
        })
