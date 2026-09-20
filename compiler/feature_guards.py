# compiler/feature_guards.py
from typing import List
from compiler.errors import KolError
from compiler.ast_nodes import (
    ASTNode,
    Program,
    ScriptProgram,
    VarDecl,
    Param,
    FunctionDecl,
    TaskDecl,
    TypeDecl,
    EnumDecl,
    EnumVariant,
    InterfaceDecl,
    ImplBlock,
    Assignment,
    IfStmt,
    MatchArm,
    MatchStmt,
    ForStmt,
    WhileStmt,
    LoopStmt,
    ReturnStmt,
    FailStmt,
    DeferStmt,
    ArenaStmt,
    TestBlock,
    AssertStmt,
    WhenStmt,
    SystemBlock,
    RawBlock,
    ExternDecl,
    BinOp,
    UnaryOp,
    PipeExpr,
    CallExpr,
    MethodCallExpr,
    IndexExpr,
    FieldAccess,
    StrInterp,
    TupleExpr,
    ListExpr,
    MapEntry,
    MapExpr,
    LambdaExpr,
    TryExpr,
    OrExpr,
    CatchExpr,
    RangeExpr,
    AwaitExpr,
    AllExpr,
    MultiAssignStmt,
)

# raw blocks already fail at the lexer (raw C syntax like ';' is not a valid Kol token) and don't need a guard here.
# channel has no AST node class (TokenType.CHANNEL exists in the lexer but no parser rule constructs an AST node for it), so it already fails at parse time.


def check_unimplemented_features(ast: ASTNode) -> List[KolError]:
    """
    Walks the AST recursively and returns a list of KolError objects for
    unimplemented features (all, system blocks).
    Does NOT raise exceptions.
    """
    errors: List[KolError] = []

    def _walk(node: ASTNode):
        if node is None:
            return

        # Check guards on the node itself
        if isinstance(node, AllExpr):
            errors.append(
                KolError(
                    "bare 'all(...)' is not yet implemented; use multi-variable let, e.g. 'let a, b = await all(...)'",
                    node.location,
                )
            )

        # Recurse into children based on node type
        if isinstance(node, Program):
            for decl in node.declarations:
                _walk(decl)

        elif isinstance(node, ScriptProgram):
            for stmt in node.statements:
                _walk(stmt)

        elif isinstance(node, VarDecl):
            if node.value:
                _walk(node.value)

        elif isinstance(node, Param):
            if node.default_val:
                _walk(node.default_val)

        elif isinstance(node, FunctionDecl):
            for p in node.params:
                _walk(p)
            for stmt in node.body:
                _walk(stmt)

        elif isinstance(node, TaskDecl):
            _walk(node.fn_decl)

        elif isinstance(node, TypeDecl):
            for m in node.methods:
                _walk(m)
            for imp in node.impls:
                _walk(imp)
            for c in node.constants:
                _walk(c)
            for nt in node.nested_types:
                _walk(nt)

        elif isinstance(node, EnumDecl):
            pass  # Enum variants do not contain statements or expressions

        elif isinstance(node, InterfaceDecl):
            for m in node.methods:
                _walk(m)

        elif isinstance(node, ImplBlock):
            for m in node.methods:
                _walk(m)

        elif isinstance(node, Assignment):
            _walk(node.target)
            _walk(node.value)

        elif isinstance(node, IfStmt):
            _walk(node.condition)
            for stmt in node.then_branch:
                _walk(stmt)
            for elif_cond, elif_body in node.elif_branches:
                _walk(elif_cond)
                for stmt in elif_body:
                    _walk(stmt)
            if node.else_branch:
                for stmt in node.else_branch:
                    _walk(stmt)

        elif isinstance(node, MatchArm):
            _walk(node.pattern)
            if node.guard:
                _walk(node.guard)
            _walk(node.body)

        elif isinstance(node, MatchStmt):
            _walk(node.expr)
            for arm in node.arms:
                _walk(arm)

        elif isinstance(node, ForStmt):
            _walk(node.iterable)
            if node.step:
                _walk(node.step)
            for stmt in node.body:
                _walk(stmt)

        elif isinstance(node, WhileStmt):
            _walk(node.condition)
            for stmt in node.body:
                _walk(stmt)

        elif isinstance(node, LoopStmt):
            for stmt in node.body:
                _walk(stmt)

        elif isinstance(node, ReturnStmt):
            if node.value:
                _walk(node.value)

        elif isinstance(node, FailStmt):
            _walk(node.message)

        elif isinstance(node, DeferStmt):
            _walk(node.call)

        elif isinstance(node, ArenaStmt):
            for stmt in node.body:
                _walk(stmt)

        elif isinstance(node, TestBlock):
            for stmt in node.body:
                _walk(stmt)

        elif isinstance(node, AssertStmt):
            _walk(node.condition)

        elif isinstance(node, WhenStmt):
            _walk(node.condition)
            for stmt in node.then_branch:
                _walk(stmt)
            if node.else_branch:
                for stmt in node.else_branch:
                    _walk(stmt)

        elif isinstance(node, SystemBlock):
            for stmt in node.body:
                _walk(stmt)

        elif isinstance(node, RawBlock):
            for stmt in node.body:
                _walk(stmt)

        elif isinstance(node, ExternDecl):
            _walk(node.fn_decl)

        elif isinstance(node, BinOp):
            _walk(node.left)
            _walk(node.right)

        elif isinstance(node, UnaryOp):
            _walk(node.operand)

        elif isinstance(node, PipeExpr):
            _walk(node.left)
            _walk(node.right)

        elif isinstance(node, CallExpr):
            _walk(node.callee)
            for arg in node.args:
                _walk(arg)
            for _, arg_val in node.named_args:
                _walk(arg_val)

        elif isinstance(node, MethodCallExpr):
            _walk(node.object)
            for arg in node.args:
                _walk(arg)

        elif isinstance(node, IndexExpr):
            _walk(node.target)
            _walk(node.index)

        elif isinstance(node, FieldAccess):
            _walk(node.target)

        elif isinstance(node, StrInterp):
            for part in node.parts:
                if isinstance(part, ASTNode):
                    _walk(part)

        elif isinstance(node, TupleExpr):
            for elem in node.elements:
                _walk(elem)

        elif isinstance(node, ListExpr):
            for elem in node.elements:
                _walk(elem)

        elif isinstance(node, MapEntry):
            _walk(node.key)
            _walk(node.value)

        elif isinstance(node, MapExpr):
            for entry in node.entries:
                _walk(entry)

        elif isinstance(node, LambdaExpr):
            _walk(node.body)

        elif isinstance(node, TryExpr):
            _walk(node.expr)

        elif isinstance(node, OrExpr):
            _walk(node.expr)
            _walk(node.default_val)

        elif isinstance(node, CatchExpr):
            _walk(node.expr)
            _walk(node.handler)

        elif isinstance(node, RangeExpr):
            _walk(node.start)
            _walk(node.end)

        elif isinstance(node, AwaitExpr):
            _walk(node.task_expr)

        elif isinstance(node, AllExpr):
            for t in node.tasks:
                _walk(t)

        elif isinstance(node, MultiAssignStmt):
            if isinstance(node.value, AwaitExpr) and isinstance(node.value.task_expr, AllExpr):
                pass
            elif isinstance(node.value, AllExpr):
                pass
            else:
                _walk(node.value)

    _walk(ast)
    return errors
