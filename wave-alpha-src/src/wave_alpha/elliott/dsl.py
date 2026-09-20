from __future__ import annotations

import ast
import operator
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from wave_alpha.elliott.models import WaveSegment


def length(seg: WaveSegment) -> Decimal:
    return seg.length


def retrace(seg: WaveSegment, prior: WaveSegment) -> Decimal:
    if prior.length == 0:
        return Decimal(0)
    return seg.length / prior.length


def overlaps(later: WaveSegment, earlier: WaveSegment) -> bool:
    """True if `later`'s end has crossed back into `earlier`'s price range."""
    e_lo = min(earlier.start.price, earlier.end.price)
    e_hi = max(earlier.start.price, earlier.end.price)
    return e_lo <= later.end.price <= e_hi


_CALLABLES: dict[str, Callable[..., Any]] = {
    "length": length,
    "retrace": retrace,
    "overlaps": overlaps,
    "min": min,
    "max": max,
    "abs": abs,
    "Decimal": Decimal,
}

_BIN_OPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}

_CMP_OPS: dict[type[ast.cmpop], Callable[[Any, Any], Any]] = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}


class DSLError(ValueError):
    """Raised when a constraint expression contains a disallowed construct."""


def _walk(node: ast.AST, bindings: dict[str, Any]) -> Any:
    """Recursive evaluator. Only the AST nodes a constraint can legitimately
    use are allowed; anything else raises DSLError before evaluation."""
    if isinstance(node, ast.Expression):
        return _walk(node.body, bindings)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_walk(node.operand, bindings)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _walk(node.operand, bindings)
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_walk(v, bindings) for v in node.values)
        if isinstance(node.op, ast.Or):
            return any(_walk(v, bindings) for v in node.values)
        raise DSLError(f"disallowed boolean op: {type(node.op).__name__}")
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_walk(node.left, bindings), _walk(node.right, bindings))
    if isinstance(node, ast.Compare):
        left = _walk(node.left, bindings)
        for op, right_node in zip(node.ops, node.comparators, strict=False):
            if type(op) not in _CMP_OPS:
                raise DSLError(f"disallowed comparison: {type(op).__name__}")
            right = _walk(right_node, bindings)
            if not _CMP_OPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Name):
        if node.id in bindings:
            return bindings[node.id]
        if node.id in _CALLABLES:
            return _CALLABLES[node.id]
        raise DSLError(f"unknown identifier: {node.id}")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _CALLABLES:
            raise DSLError("only allowlisted top-level function calls are permitted")
        if node.keywords:
            raise DSLError("keyword arguments are not permitted in constraint expressions")
        callee = _CALLABLES[node.func.id]
        args = [_walk(a, bindings) for a in node.args]
        return callee(*args)
    raise DSLError(f"disallowed expression node: {type(node).__name__}")


def evaluate_parsed(tree: ast.AST, bindings: dict[str, WaveSegment]) -> tuple[bool, str]:
    """Evaluate a pre-parsed AST against bindings.

    Same return contract as ``evaluate(expr, bindings)`` but skips the
    ``ast.parse`` step. Used by ``validator.validate`` when the caller
    has already parsed via ``Constraint.parsed``. See spec §3.1.
    """
    namespace: dict[str, Any] = dict(bindings)
    try:
        result = _walk(tree, namespace)
        return bool(result), str(result)
    except DSLError as exc:
        return False, f"dsl error: {exc!s}"
    except Exception as exc:
        return False, f"eval error: {exc!s}"


def evaluate(expr: str, bindings: dict[str, WaveSegment]) -> tuple[bool, str]:
    """Evaluate a constraint expression against a sandboxed AST allowlist.

    Returns (passed, diagnostic). Disallowed constructs (attribute access,
    subscripts, lambdas, comprehensions, etc.) raise DSLError internally and
    are reported as failures with a diagnostic.

    Constraint expressions are Decimal-throughout: numeric literals must be
    wrapped as ``Decimal('1.618')`` to compose with the Decimal returned by
    ``length()`` and ``retrace()``. The YAML templates follow this convention.
    """
    namespace: dict[str, Any] = dict(bindings)
    try:
        tree = ast.parse(expr, mode="eval")
        result = _walk(tree, namespace)
        return bool(result), str(result)
    except DSLError as exc:
        return False, f"dsl error: {exc!s}"
    except Exception as exc:
        return False, f"eval error: {exc!s}"
