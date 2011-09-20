"""
Utilities for dealing with some AST features.

TODO:
- Find a way to deal with the 'sizeof' operator
"""
import operator
from objective.cparser import c_ast

OPERATORS = {
    '+':    operator.add,
    '-':    operator.sub,
    '*':    operator.mul,
    '/':    operator.floordiv,
    '<<':   operator.lshift,
    '>>':   operator.rshift,
    '==':   operator.eq,
    '<':   operator.lt,
    '>':   operator.gt,
    '<=':   operator.le,
    '>=':   operator.ge,
    '!=':   operator.ne,
}

def parse_int(value):
    """ Parse a C integer literal and return its value """
    value = value.lower().rstrip('l').rstrip('u')
    return int(value, 0)

def format_expr (node):
    """
    Return a string representation of an expression node 
    """
    if isinstance(node, c_ast.Constant):
        return node.value

    elif isinstance(node, c_ast.BinaryOp):
        return '( %s %s %s )'%(format_expr(node.left), node.op, format_expr(node.right))

    elif isinstance(node, c_ast.UnaryOp):
        return '( %s %s )'%(node.op, format_expr(node.expr))

    else:
        return repr(node)

def constant_fold(node):
    """
    Try to constant-fold an expression. 

    Returns the same node when no folding can be done, or a replacement 
    node when there is (some) folding.
    """
    if isinstance(node, c_ast.Constant):
        return node

    if isinstance(node, c_ast.UnaryOp):
        expr = constant_fold(node.expr)
        if isinstance(expr, c_ast.Constant):
            return c_ast.Constant(expr.type, node.op + expr.value, node.coord)
        elif expr is not node.expr:
            return c_ast.UnaryOp(node.op, expr, node.coord)

    elif isinstance(node, c_ast.BinaryOp):
        left  = constant_fold(node.left)
        right = constant_fold(node.right)
        if isinstance(left, c_ast.Constant) and isinstance(right, c_ast.Constant):
            if left.type == 'int' and right.type == 'int':
                left = parse_int(left.value)
                right = parse_int(right.value)

                fun = OPERATORS.get(node.op)
                if fun is not None:
                    return c_ast.Constant('int', str(fun(left, right)), node.coord)

        if left is not node.left or right is not node.right:
            return c_ast.BinaryOp(node.op, left, right, node.coord)

    elif isinstance(node, c_ast.TernaryOp):
        cond = constant_fold(node.cond)
        iftrue = constant_fold(node.iftrue)
        iffalse = constant_fold(node.iffalse)

        if isinstance(cond, c_ast.Constant):
            if eval(cond.value):
                return iftrue
            else:
                return iffalse

        else:
            if cond is node.cond and iftrue is node.iftrue and iffalse is node.iffalse:
                return node

            else:
                return c_ast.TernaryOp(cond, iftrue, iffalse, node.coord)


    return node
