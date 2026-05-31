import math

import pytest

from moira.tools.base import ToolDefinition
from moira.tools.builtin.calculator import CalculatorTool


@pytest.fixture
def calc():
    defn = ToolDefinition(name="calculator", description="", implementation="")
    return CalculatorTool(defn)


class TestCalculatorBasic:
    @pytest.mark.asyncio
    async def test_addition(self, calc):
        r = await calc.execute({"expression": "2 + 3"})
        assert r.success
        assert r.output == "5"

    @pytest.mark.asyncio
    async def test_complex_expression(self, calc):
        r = await calc.execute({"expression": "sqrt(2) + 3**4"})
        assert r.success

    @pytest.mark.asyncio
    async def test_missing_expression(self, calc):
        r = await calc.execute({})
        assert not r.success
        assert "Missing required parameter" in r.error

    @pytest.mark.asyncio
    async def test_empty_expression(self, calc):
        r = await calc.execute({"expression": ""})
        assert not r.success
        assert "Missing required parameter" in r.error


class TestCalculatorBuiltinSafety:
    """Verify that Python builtins and dangerous functions are blocked."""

    @pytest.mark.asyncio
    async def test_open_blocked(self, calc):
        r = await calc.execute({"expression": "open('/etc/passwd')"})
        assert not r.success

    @pytest.mark.asyncio
    async def test_exec_blocked(self, calc):
        r = await calc.execute({"expression": "exec('print(1)')"})
        assert not r.success

    @pytest.mark.asyncio
    async def test_eval_blocked(self, calc):
        r = await calc.execute({"expression": "eval('1+1')"})
        assert not r.success

    @pytest.mark.asyncio
    async def test_import_blocked(self, calc):
        r = await calc.execute({"expression": "__import__('os')"})
        assert not r.success

    @pytest.mark.asyncio
    async def test_type_constructor_blocked(self, calc):
        r = await calc.execute({"expression": "type(1)"})
        assert not r.success

    @pytest.mark.asyncio
    async def test_str_constructor_blocked(self, calc):
        r = await calc.execute({"expression": "str(1)"})
        assert not r.success

    @pytest.mark.asyncio
    async def test_int_constructor_blocked(self, calc):
        r = await calc.execute({"expression": "int('1')"})
        assert not r.success

    @pytest.mark.asyncio
    async def test_len_blocked(self, calc):
        r = await calc.execute({"expression": "len('abc')"})
        assert not r.success

    @pytest.mark.asyncio
    async def test_print_blocked(self, calc):
        r = await calc.execute({"expression": "print(1)"})
        assert not r.success

    @pytest.mark.asyncio
    async def test_globals_blocked(self, calc):
        r = await calc.execute({"expression": "globals()"})
        assert not r.success

    @pytest.mark.asyncio
    async def test_locals_blocked(self, calc):
        r = await calc.execute({"expression": "locals()"})
        assert not r.success


class TestCalculatorAstSafety:
    """Verify that dangerous AST node types (attribute access, subscripts,
    comprehensions, etc.) are rejected at the validation layer before eval."""

    @pytest.mark.asyncio
    async def test_attribute_access_blocked(self, calc):
        r = await calc.execute({"expression": "1 .__class__"})
        assert not r.success

    @pytest.mark.asyncio
    async def test_subscript_blocked(self, calc):
        r = await calc.execute({"expression": "[1,2,3][0]"})
        assert not r.success

    @pytest.mark.asyncio
    async def test_list_literal_blocked(self, calc):
        r = await calc.execute({"expression": "[1, 2, 3]"})
        assert not r.success

    @pytest.mark.asyncio
    async def test_dict_literal_blocked(self, calc):
        r = await calc.execute({"expression": "{'a': 1}"})
        assert not r.success

    @pytest.mark.asyncio
    async def test_lambda_blocked(self, calc):
        r = await calc.execute({"expression": "lambda x: x"})
        assert not r.success

    @pytest.mark.asyncio
    async def test_comprehension_blocked(self, calc):
        r = await calc.execute({"expression": "[x for x in range(10)]"})
        assert not r.success

    @pytest.mark.asyncio
    async def test_boolean_operator(self, calc):
        r = await calc.execute({"expression": "1 and 0"})
        assert not r.success

    @pytest.mark.asyncio
    async def test_comparison_blocked(self, calc):
        r = await calc.execute({"expression": "1 < 2"})
        assert not r.success


class TestCalculatorMathFunctions:
    """Verify every function in _MATH_FUNCTIONS works correctly."""

    @pytest.mark.asyncio
    async def test_power(self, calc):
        r = await calc.execute({"expression": "power(2, 10)"})
        assert r.success
        assert abs(float(r.output) - 1024.0) < 1e-10

    @pytest.mark.asyncio
    async def test_pow(self, calc):
        r = await calc.execute({"expression": "pow(3, 4)"})
        assert r.success
        assert abs(float(r.output) - 81.0) < 1e-10

    @pytest.mark.asyncio
    async def test_sqrt(self, calc):
        r = await calc.execute({"expression": "sqrt(16)"})
        assert r.success
        assert r.output == "4.0"

    @pytest.mark.asyncio
    async def test_root(self, calc):
        r = await calc.execute({"expression": "root(27, 3)"})
        assert r.success
        assert abs(float(r.output) - 3.0) < 1e-10

    @pytest.mark.asyncio
    async def test_exp(self, calc):
        r = await calc.execute({"expression": "exp(1)"})
        assert r.success
        assert abs(float(r.output) - math.e) < 1e-10

    @pytest.mark.asyncio
    async def test_ln(self, calc):
        r = await calc.execute({"expression": "ln(e)"})
        assert r.success
        assert abs(float(r.output) - 1.0) < 1e-10

    @pytest.mark.asyncio
    async def test_log(self, calc):
        r = await calc.execute({"expression": "log(e)"})
        assert r.success
        assert abs(float(r.output) - 1.0) < 1e-10

    @pytest.mark.asyncio
    async def test_log10(self, calc):
        r = await calc.execute({"expression": "log10(100)"})
        assert r.success
        assert abs(float(r.output) - 2.0) < 1e-10

    @pytest.mark.asyncio
    async def test_log2(self, calc):
        r = await calc.execute({"expression": "log2(8)"})
        assert r.success
        assert abs(float(r.output) - 3.0) < 1e-10

    @pytest.mark.asyncio
    async def test_abs(self, calc):
        r = await calc.execute({"expression": "abs(-42)"})
        assert r.success
        assert r.output == "42"

    @pytest.mark.asyncio
    async def test_floor(self, calc):
        r = await calc.execute({"expression": "floor(3.7)"})
        assert r.success
        assert r.output == "3"

    @pytest.mark.asyncio
    async def test_ceil(self, calc):
        r = await calc.execute({"expression": "ceil(3.2)"})
        assert r.success
        assert r.output == "4"

    @pytest.mark.asyncio
    async def test_round(self, calc):
        r = await calc.execute({"expression": "round(3.14159)"})
        assert r.success
        assert r.output == "3"

    @pytest.mark.asyncio
    async def test_trunc(self, calc):
        r = await calc.execute({"expression": "trunc(-3.7)"})
        assert r.success
        assert r.output == "-3"

    @pytest.mark.asyncio
    async def test_min(self, calc):
        r = await calc.execute({"expression": "min(5, 2, 8)"})
        assert r.success
        assert r.output == "2"

    @pytest.mark.asyncio
    async def test_max(self, calc):
        r = await calc.execute({"expression": "max(5, 2, 8)"})
        assert r.success
        assert r.output == "8"

    @pytest.mark.asyncio
    async def test_sin(self, calc):
        r = await calc.execute({"expression": "sin(pi / 2)"})
        assert r.success
        assert abs(float(r.output) - 1.0) < 1e-10

    @pytest.mark.asyncio
    async def test_cos(self, calc):
        r = await calc.execute({"expression": "cos(0)"})
        assert r.success
        assert abs(float(r.output) - 1.0) < 1e-10

    @pytest.mark.asyncio
    async def test_tan(self, calc):
        r = await calc.execute({"expression": "tan(0)"})
        assert r.success
        assert abs(float(r.output)) < 1e-10

    @pytest.mark.asyncio
    async def test_sec(self, calc):
        r = await calc.execute({"expression": "sec(0)"})
        assert r.success
        assert abs(float(r.output) - 1.0) < 1e-10

    @pytest.mark.asyncio
    async def test_cosec(self, calc):
        r = await calc.execute({"expression": "cosec(pi / 2)"})
        assert r.success
        assert abs(float(r.output) - 1.0) < 1e-10

    @pytest.mark.asyncio
    async def test_cotan(self, calc):
        r = await calc.execute({"expression": "cotan(pi / 4)"})
        assert r.success
        assert abs(float(r.output) - 1.0) < 1e-10

    @pytest.mark.asyncio
    async def test_asin(self, calc):
        r = await calc.execute({"expression": "asin(1)"})
        assert r.success
        assert abs(float(r.output) - math.pi / 2) < 1e-10

    @pytest.mark.asyncio
    async def test_acos(self, calc):
        r = await calc.execute({"expression": "acos(1)"})
        assert r.success
        assert abs(float(r.output)) < 1e-10

    @pytest.mark.asyncio
    async def test_atan(self, calc):
        r = await calc.execute({"expression": "atan(1)"})
        assert r.success
        assert abs(float(r.output) - math.pi / 4) < 1e-10

    @pytest.mark.asyncio
    async def test_sinh(self, calc):
        r = await calc.execute({"expression": "sinh(0)"})
        assert r.success
        assert abs(float(r.output)) < 1e-10

    @pytest.mark.asyncio
    async def test_cosh(self, calc):
        r = await calc.execute({"expression": "cosh(0)"})
        assert r.success
        assert abs(float(r.output) - 1.0) < 1e-10

    @pytest.mark.asyncio
    async def test_tanh(self, calc):
        r = await calc.execute({"expression": "tanh(0)"})
        assert r.success
        assert abs(float(r.output)) < 1e-10

    @pytest.mark.asyncio
    async def test_asinh(self, calc):
        r = await calc.execute({"expression": "asinh(0)"})
        assert r.success
        assert abs(float(r.output)) < 1e-10

    @pytest.mark.asyncio
    async def test_acosh(self, calc):
        r = await calc.execute({"expression": "acosh(1)"})
        assert r.success
        assert abs(float(r.output)) < 1e-10

    @pytest.mark.asyncio
    async def test_atanh(self, calc):
        r = await calc.execute({"expression": "atanh(0)"})
        assert r.success
        assert abs(float(r.output)) < 1e-10

    @pytest.mark.asyncio
    async def test_degrees(self, calc):
        r = await calc.execute({"expression": "degrees(pi)"})
        assert r.success
        assert abs(float(r.output) - 180.0) < 1e-10

    @pytest.mark.asyncio
    async def test_radians(self, calc):
        r = await calc.execute({"expression": "radians(180)"})
        assert r.success
        assert abs(float(r.output) - math.pi) < 1e-10

    @pytest.mark.asyncio
    async def test_factorial(self, calc):
        r = await calc.execute({"expression": "factorial(6)"})
        assert r.success
        assert r.output == "720"

    @pytest.mark.asyncio
    async def test_gcd(self, calc):
        r = await calc.execute({"expression": "gcd(12, 8)"})
        assert r.success
        assert r.output == "4"

    @pytest.mark.asyncio
    async def test_lcm(self, calc):
        r = await calc.execute({"expression": "lcm(4, 6)"})
        assert r.success
        assert r.output == "12"

    @pytest.mark.asyncio
    async def test_comb(self, calc):
        r = await calc.execute({"expression": "comb(10, 3)"})
        assert r.success
        assert r.output == "120"

    @pytest.mark.asyncio
    async def test_perm(self, calc):
        r = await calc.execute({"expression": "perm(5, 2)"})
        assert r.success
        assert r.output == "20"

    @pytest.mark.asyncio
    async def test_tau(self, calc):
        r = await calc.execute({"expression": "tau"})
        assert r.success
        assert abs(float(r.output) - 2 * math.pi) < 1e-10

    @pytest.mark.asyncio
    async def test_pi(self, calc):
        r = await calc.execute({"expression": "pi"})
        assert r.success
        assert abs(float(r.output) - math.pi) < 1e-10

    @pytest.mark.asyncio
    async def test_e(self, calc):
        r = await calc.execute({"expression": "e"})
        assert r.success
        assert abs(float(r.output) - math.e) < 1e-10

    @pytest.mark.asyncio
    async def test_exponentiation(self, calc):
        r = await calc.execute({"expression": "2**10"})
        assert r.success
        assert r.output == "1024"

    @pytest.mark.asyncio
    async def test_xor_now_allowed(self, calc):
        """^ is BitXor in Python, not power. It should now work as XOR."""
        r = await calc.execute({"expression": "5 ^ 3"})
        assert r.success
        assert r.output == "6"

    @pytest.mark.asyncio
    async def test_bitwise_and(self, calc):
        r = await calc.execute({"expression": "12 & 10"})
        assert r.success
        assert r.output == "8"

    @pytest.mark.asyncio
    async def test_bitwise_or(self, calc):
        r = await calc.execute({"expression": "12 | 10"})
        assert r.success
        assert r.output == "14"

    @pytest.mark.asyncio
    async def test_left_shift(self, calc):
        r = await calc.execute({"expression": "1 << 4"})
        assert r.success
        assert r.output == "16"

    @pytest.mark.asyncio
    async def test_right_shift(self, calc):
        r = await calc.execute({"expression": "64 >> 2"})
        assert r.success
        assert r.output == "16"

    @pytest.mark.asyncio
    async def test_bitwise_not(self, calc):
        r = await calc.execute({"expression": "~0"})
        assert r.success
        assert r.output == "-1"

    @pytest.mark.asyncio
    async def test_unary_negation(self, calc):
        r = await calc.execute({"expression": "-5 + 3"})
        assert r.success
        assert r.output == "-2"

    @pytest.mark.asyncio
    async def test_nested_functions(self, calc):
        r = await calc.execute({"expression": "sqrt(sin(pi/2) + cos(0))"})
        assert r.success
        assert abs(float(r.output) - math.sqrt(2.0)) < 1e-10
