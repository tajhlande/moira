"""Calculator tool: safely evaluates mathematical expressions.

Uses a restricted AST parser to ensure only mathematical operations are
permitted — no function calls with side effects, no string literals, no
variable access."""

import ast
import math
import time
from typing import Any

from moira.tools.base import BaseTool, ToolResult

# Allowed AST node types — anything else raises SyntaxError.
_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.Pow,
    ast.BitAnd,
    ast.BitOr,
    ast.BitXor,
    ast.LShift,
    ast.RShift,
    ast.Invert,
    ast.USub,
    ast.UAdd,
    ast.Call,
    ast.Name,
    ast.Load,
)

# Math functions exposed to expressions. Keys are lowercase function names.
_MATH_FUNCTIONS: dict[str, Any] = {
    "power": math.pow,
    "pow": math.pow,
    "sqrt": math.sqrt,
    "root": lambda x, n: x ** (1.0 / n),
    "exp": math.exp,
    "ln": math.log,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "abs": abs,
    "floor": math.floor,
    "ceil": math.ceil,
    "round": round,
    "trunc": math.trunc,
    "min": min,
    "max": max,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "sec": lambda x: 1.0 / math.cos(x),
    "cosec": lambda x: 1.0 / math.sin(x),
    "cotan": lambda x: 1.0 / math.tan(x),
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "asinh": math.asinh,
    "acosh": math.acosh,
    "atanh": math.atanh,
    "degrees": math.degrees,
    "radians": math.radians,
    "factorial": math.factorial,
    "gcd": math.gcd,
    "lcm": math.lcm,
    "comb": math.comb,
    "perm": math.perm,
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}


def _validate_ast(node: ast.AST) -> None:
    """Walk the AST and reject any disallowed node types."""
    if not isinstance(node, _ALLOWED_NODES):
        raise ValueError(f"Disallowed expression element: {type(node).__name__}")
    for child in ast.iter_child_nodes(node):
        _validate_ast(child)


def _build_description() -> str:
    """Generate a description listing all available functions, operators,
    and constants. This keeps the LLM-facing description in sync with
    _MATH_FUNCTIONS automatically."""
    functions = sorted(k for k, v in _MATH_FUNCTIONS.items() if callable(v))
    constants = sorted(k for k, v in _MATH_FUNCTIONS.items() if not callable(v))
    return (
        "Evaluate a mathematical expression. "
        "Operators: + - * / % ** (power) & | ^ << >> ~ (bitwise). "
        f"Functions: {', '.join(functions)}. "
        f"Constants: {', '.join(constants)}. "
        "Expressions use infix notation (e.g. sqrt(2) + 3**4)."
    )


class CalculatorTool(BaseTool):
    """Calculator tool: safely evaluates mathematical expressions using a
    restricted AST parser. Instantiated by the executor with the tool's
    ToolDefinition from the database."""

    tool_name = "calculator"
    tool_description = _build_description()
    tool_group = "standard"
    tool_argument_schema = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Mathematical expression in infix notation",
            },
        },
        "required": ["expression"],
    }

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        start = time.monotonic()
        expression = args.get("expression", "")
        if not expression:
            return ToolResult(
                tool_name="calculator",
                output="",
                success=False,
                error="Missing required parameter: expression",
                duration_ms=0,
            )

        try:
            tree = ast.parse(expression, mode="eval")
            _validate_ast(tree)

            # Evaluate with only math functions available — no builtins.
            code = compile(tree, "<calculator>", "eval")
            result = eval(code, {"__builtins__": {}}, _MATH_FUNCTIONS)

            elapsed = int((time.monotonic() - start) * 1000)
            return ToolResult(
                tool_name="calculator",
                output=str(result),
                success=True,
                duration_ms=elapsed,
            )
        except (SyntaxError, ValueError) as e:
            elapsed = int((time.monotonic() - start) * 1000)
            return ToolResult(
                tool_name="calculator",
                output="",
                success=False,
                duration_ms=elapsed,
                error=f"Invalid expression: {e}",
            )
        except Exception as e:
            elapsed = int((time.monotonic() - start) * 1000)
            return ToolResult(
                tool_name="calculator",
                output="",
                success=False,
                duration_ms=elapsed,
                error=str(e),
            )
