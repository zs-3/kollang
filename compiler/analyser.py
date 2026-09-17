# compiler/analyser.py
from typing import Dict, List, Optional, Set
from compiler.ast_nodes import *
from compiler.errors import KolError, SourceLocation

class Symbol:
    def __init__(self, name: str, type_name: str, is_mut: bool = False, is_const: bool = False):
        self.name = name
        self.type_name = type_name
        self.is_mut = is_mut
        self.is_const = is_const

class Scope:
    def __init__(self, parent: Optional['Scope'] = None):
        self.parent = parent
        self.symbols: Dict[str, Symbol] = {}

    def define(self, name: str, type_name: str, is_mut: bool = False, is_const: bool = False):
        self.symbols[name] = Symbol(name, type_name, is_mut, is_const)

    def lookup(self, name: str) -> Optional[Symbol]:
        if name in self.symbols:
            return self.symbols[name]
        if self.parent:
            return self.parent.lookup(name)
        return None

class Analyser:
    def __init__(self, filename: str):
        self.filename = filename
        self.global_scope = Scope()
        self.current_scope = self.global_scope
        self.type_defs: Dict[str, TypeDecl] = {}
        self.enum_defs: Dict[str, EnumDecl] = {}
        self.func_defs: Dict[str, FunctionDecl] = {}

    def analyse(self, program: ASTNode):
        if isinstance(program, ScriptProgram):
            for stmt in program.statements:
                self._analyse_stmt(stmt)
        elif isinstance(program, Program):
            for decl in program.declarations:
                if isinstance(decl, TypeDecl):
                    self.type_defs[decl.name] = decl
                elif isinstance(decl, EnumDecl):
                    self.enum_defs[decl.name] = decl
                elif isinstance(decl, FunctionDecl):
                    self.func_defs[decl.name] = decl

            for decl in program.declarations:
                self._analyse_stmt(decl)

    def _push_scope(self):
        self.current_scope = Scope(parent=self.current_scope)

    def _pop_scope(self):
        if self.current_scope.parent:
            self.current_scope = self.current_scope.parent

    def _analyse_stmt(self, stmt: ASTNode):
        loc = stmt.location

        if isinstance(stmt, VarDecl):
            inferred_type = "int"
            if stmt.value:
                inferred_type = self._analyse_expr(stmt.value)

            if stmt.type_annot:
                annot_type = stmt.type_annot.name
                if stmt.value and annot_type != inferred_type and inferred_type != "any":
                    raise KolError(f"Type mismatch: expected {annot_type}, got {inferred_type}", loc, hint=f"use to_{annot_type}() to convert")
                type_name = annot_type
            else:
                type_name = inferred_type

            self.current_scope.define(stmt.name, type_name, is_mut=stmt.is_mut, is_const=stmt.is_const)

        elif isinstance(stmt, Assignment):
            if isinstance(stmt.target, Ident):
                sym = self.current_scope.lookup(stmt.target.name)
                if sym:
                    if not sym.is_mut:
                        raise KolError(f"Cannot mutate immutable binding '{sym.name}'", loc, hint="declare variable with 'mut'")
                    val_type = self._analyse_expr(stmt.value)
                    if sym.type_name != val_type and val_type != "any":
                        raise KolError(f"Type mismatch on assignment: expected {sym.type_name}, got {val_type}", loc)
                else:
                    val_type = self._analyse_expr(stmt.value)
                    self.current_scope.define(stmt.target.name, val_type, is_mut=False)
            else:
                self._analyse_expr(stmt.target)
                self._analyse_expr(stmt.value)

        elif isinstance(stmt, FunctionDecl):
            self.func_defs[stmt.name] = stmt
            self._push_scope()
            for p in stmt.params:
                pt = p.type_annot.name if p.type_annot else "int"
                self.current_scope.define(p.name, pt, is_mut=False)
            for s in stmt.body:
                self._analyse_stmt(s)
            self._pop_scope()

        elif isinstance(stmt, IfStmt):
            cond_t = self._analyse_expr(stmt.condition)
            self._push_scope()
            for s in stmt.then_branch:
                self._analyse_stmt(s)
            self._pop_scope()

            for elif_cond, elif_body in stmt.elif_branches:
                self._analyse_expr(elif_cond)
                self._push_scope()
                for s in elif_body:
                    self._analyse_stmt(s)
                self._pop_scope()

            if stmt.else_branch:
                self._push_scope()
                for s in stmt.else_branch:
                    self._analyse_stmt(s)
                self._pop_scope()

        elif isinstance(stmt, ForStmt):
            self._push_scope()
            if isinstance(stmt.iterable, RangeExpr):
                self._analyse_expr(stmt.iterable.start)
                self._analyse_expr(stmt.iterable.end)
                self.current_scope.define(stmt.var_name, "int", is_mut=False)
            else:
                iter_t = self._analyse_expr(stmt.iterable)
                self.current_scope.define(stmt.var_name, "str", is_mut=False)
            if stmt.index_var:
                self.current_scope.define(stmt.index_var, "int", is_mut=False)
            for s in stmt.body:
                self._analyse_stmt(s)
            self._pop_scope()

        elif isinstance(stmt, WhileStmt):
            self._analyse_expr(stmt.condition)
            self._push_scope()
            for s in stmt.body:
                self._analyse_stmt(s)
            self._pop_scope()

        elif isinstance(stmt, LoopStmt):
            self._push_scope()
            for s in stmt.body:
                self._analyse_stmt(s)
            self._pop_scope()

        elif isinstance(stmt, ReturnStmt):
            if stmt.value:
                self._analyse_expr(stmt.value)

        elif isinstance(stmt, MatchStmt):
            target_t = self._analyse_expr(stmt.expr)
            for arm in stmt.arms:
                self._push_scope()
                self._analyse_stmt(arm.body)
                self._pop_scope()

        elif isinstance(stmt, TypeDecl):
            self.type_defs[stmt.name] = stmt

        elif isinstance(stmt, EnumDecl):
            self.enum_defs[stmt.name] = stmt

        else:
            if isinstance(stmt, ASTNode):
                self._analyse_expr(stmt)

    def _analyse_expr(self, expr: ASTNode) -> str:
        loc = expr.location

        if isinstance(expr, IntLit):
            return "int"
        if isinstance(expr, FloatLit):
            return "float"
        if isinstance(expr, StrLit) or isinstance(expr, StrInterp):
            return "str"
        if isinstance(expr, BoolLit):
            return "bool"
        if isinstance(expr, NoneLit):
            return "none"

        if isinstance(expr, Ident):
            sym = self.current_scope.lookup(expr.name)
            if not sym:
                if expr.name in self.enum_defs:
                    return expr.name
                return "any"
            return sym.type_name

        if isinstance(expr, BinOp):
            l_type = self._analyse_expr(expr.left)
            r_type = self._analyse_expr(expr.right)
            if expr.op in ["==", "!=", "<", ">", "<=", ">=", "and", "or"]:
                return "bool"
            if l_type == "float" or r_type == "float":
                return "float"
            return "int"

        if isinstance(expr, UnaryOp):
            return self._analyse_expr(expr.operand)

        if isinstance(expr, CallExpr):
            if isinstance(expr.callee, Ident):
                fn_name = expr.callee.name
                if fn_name == "print":
                    for a in expr.args: self._analyse_expr(a)
                    return "void"
                if fn_name in self.func_defs:
                    fn_decl = self.func_defs[fn_name]
                    return fn_decl.return_type.name if fn_decl.return_type else "void"
                if fn_name in self.type_defs:
                    return fn_name
                if fn_name in self.enum_defs:
                    return fn_name
            for a in expr.args: self._analyse_expr(a)
            return "any"

        if isinstance(expr, MethodCallExpr):
            obj_t = self._analyse_expr(expr.object)
            for a in expr.args: self._analyse_expr(a)
            if expr.method_name in ["upper", "lower", "trim", "replace", "to_str"]:
                return "str"
            return "any"

        if isinstance(expr, FieldAccess):
            target_t = self._analyse_expr(expr.target)
            if target_t in self.type_defs:
                td = self.type_defs[target_t]
                for f in td.fields:
                    if f.name == expr.field_name:
                        return f.type_annot.name if f.type_annot else "any"
            return "any"

        if isinstance(expr, ListExpr):
            for elem in expr.elements:
                self._analyse_expr(elem)
            return "list"

        if isinstance(expr, MapExpr):
            for entry in expr.entries:
                self._analyse_expr(entry.key)
                self._analyse_expr(entry.value)
            return "map"

        if isinstance(expr, TupleExpr):
            for elem in expr.elements:
                self._analyse_expr(elem)
            return "tuple"

        if isinstance(expr, PipeExpr):
            left_t = self._analyse_expr(expr.left)
            right_t = self._analyse_expr(expr.right)
            return right_t

        if isinstance(expr, TryExpr):
            return self._analyse_expr(expr.expr)

        if isinstance(expr, OrExpr):
            return self._analyse_expr(expr.default_val)

        if isinstance(expr, CatchExpr):
            return self._analyse_expr(expr.expr)

        return "any"
