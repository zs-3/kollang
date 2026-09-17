from typing import List, Set, Dict
from compiler.ast_nodes import *

class Codegen:
    def __init__(self, filename: str):
        self.filename = filename
        self.code: List[str] = []
        self.type_decls: List[str] = []
        self.indent_level = 0
        self.temp_var_count = 0
        self.var_types: Dict[str, str] = {}
        self.struct_fields: Dict[str, List[tuple]] = {}
        self.defer_stack: List[List[ASTNode]] = []

    def _emit(self, line: str):
        indent = "    " * self.indent_level
        self.code.append(f"{indent}{line}")

    def _temp_var(self, prefix: str = "tmp") -> str:
        self.temp_var_count += 1
        return f"_kol_{prefix}_{self.temp_var_count}"

    def generate(self, node: ASTNode) -> str:
        self.code = []
        self.type_decls = []

        all_nodes = []
        if isinstance(node, Program):
            all_nodes = node.declarations
        elif isinstance(node, ScriptProgram):
            all_nodes = node.statements

        for decl in all_nodes:
            if isinstance(decl, TypeDecl):
                fields = []
                field_decls = []
                for f in decl.fields:
                    fn = f.name
                    ft = "int64_t"
                    if f.type_annot:
                        if f.type_annot.name == "str": ft = "KolStr"
                        elif f.type_annot.name == "float": ft = "double"
                        elif f.type_annot.name == "bool": ft = "bool"
                    fields.append((fn, ft))
                    field_decls.append(f"    {ft} {fn};")
                self.struct_fields[decl.name] = fields
                self.type_decls.append(f"typedef struct {{\n" + "\n".join(field_decls) + f"\n}} {decl.name};")

        body_code = []
        self.code = body_code

        if isinstance(node, ScriptProgram):
            self._emit("int main(void) {")
            self.indent_level += 1
            self.defer_stack.append([])
            for stmt in node.statements:
                if not isinstance(stmt, TypeDecl):
                    self._gen_statement(stmt)
            self._emit_defers()
            self.defer_stack.pop()
            self._emit("return 0;")
            self.indent_level -= 1
            self._emit("}")
        elif isinstance(node, Program):
            for decl in node.declarations:
                if isinstance(decl, FunctionDecl):
                    self._gen_function_decl(decl)
                elif isinstance(decl, VarDecl):
                    self._gen_var_decl(decl, global_scope=True)
                elif not isinstance(decl, TypeDecl):
                    self._gen_statement(decl)

            has_main = any(isinstance(d, FunctionDecl) and d.name == "main" for d in node.declarations)
            if not has_main:
                self._emit("int main(void) {")
                self._emit("    return 0;")
                self._emit("}")

        self.code = ['#include "kol_runtime.h"', ""] + self.type_decls + [""] + body_code
        return "\n".join(self.code)

    def _emit_defers(self):
        if self.defer_stack and self.defer_stack[-1]:
            for defer_stmt in reversed(self.defer_stack[-1]):
                expr_c = self._gen_expr(defer_stmt.call)
                if expr_c:
                    self._emit(f"{expr_c};")

    def _gen_statement(self, stmt: ASTNode, current_fn_fallible: bool = False):
        if isinstance(stmt, VarDecl):
            self._gen_var_decl(stmt)
        elif isinstance(stmt, Assignment):
            target = self._gen_expr(stmt.target)
            val = self._gen_expr(stmt.value)
            self._emit(f"{target} {stmt.op} {val};")
            if self.var_types.get(stmt.target.name) == "str":
                self._emit(f"kol_arc_retain_str({target});")
        elif isinstance(stmt, DeferStmt):
            if self.defer_stack:
                self.defer_stack[-1].append(stmt)
        elif isinstance(stmt, ArenaStmt):
            arena_var = self._temp_var(f"arena_{stmt.name}")
            self._emit(f"KolArena {arena_var};")
            self._emit(f"kol_arena_init(&{arena_var}, 4096);")
            for s in stmt.body:
                self._gen_statement(s, current_fn_fallible)
            self._emit(f"kol_arena_free(&{arena_var});")
        elif isinstance(stmt, IfStmt):
            cond = self._gen_expr(stmt.condition)
            self._emit(f"if ({cond}) {{")
            self.indent_level += 1
            for s in stmt.then_branch:
                self._gen_statement(s, current_fn_fallible)
            self.indent_level -= 1

            for elif_cond, elif_body in stmt.elif_branches:
                e_cond = self._gen_expr(elif_cond)
                self._emit(f"}} else if ({e_cond}) {{")
                self.indent_level += 1
                for s in elif_body:
                    self._gen_statement(s, current_fn_fallible)
                self.indent_level -= 1

            if stmt.else_branch is not None:
                self._emit("} else {")
                self.indent_level += 1
                for s in stmt.else_branch:
                    self._gen_statement(s, current_fn_fallible)
                self.indent_level -= 1

            self._emit("}")

        elif isinstance(stmt, MatchStmt):
            val_var = self._temp_var("match")
            target_expr = self._gen_expr(stmt.expr)
            self._emit(f"int64_t {val_var} = {target_expr};")
            is_first = True
            for arm in stmt.arms:
                if isinstance(arm.pattern, Ident) and arm.pattern.name == "_":
                    self._emit("} else {")
                else:
                    pat_val = self._gen_expr(arm.pattern)
                    if is_first:
                        self._emit(f"if ({val_var} == {pat_val}) {{")
                        is_first = False
                    else:
                        self._emit(f"}} else if ({val_var} == {pat_val}) {{")
                self.indent_level += 1
                self._gen_statement(arm.body, current_fn_fallible)
                self.indent_level -= 1
            self._emit("}")

        elif isinstance(stmt, ForStmt):
            if isinstance(stmt.iterable, RangeExpr):
                start = self._gen_expr(stmt.iterable.start)
                end = self._gen_expr(stmt.iterable.end)
                op = "<=" if stmt.iterable.inclusive else "<"
                step = "1"
                if stmt.step:
                    step = self._gen_expr(stmt.step)
                v = stmt.var_name
                self.var_types[v] = "int"
                self._emit(f"for (int64_t {v} = {start}; {v} {op} {end}; {v} += {step}) {{")
                self.indent_level += 1
                for s in stmt.body:
                    self._gen_statement(s, current_fn_fallible)
                self.indent_level -= 1
                self._emit("}")

        elif isinstance(stmt, WhileStmt):
            cond = self._gen_expr(stmt.condition)
            self._emit(f"while ({cond}) {{")
            self.indent_level += 1
            for s in stmt.body:
                self._gen_statement(s, current_fn_fallible)
            self.indent_level -= 1
            self._emit("}")

        elif isinstance(stmt, LoopStmt):
            self._emit("while (1) {")
            self.indent_level += 1
            for s in stmt.body:
                self._gen_statement(s, current_fn_fallible)
            self.indent_level -= 1
            self._emit("}")

        elif isinstance(stmt, ReturnStmt):
            self._emit_defers()
            if stmt.value:
                val = self._gen_expr(stmt.value)
                if current_fn_fallible:
                    self._emit(f"return kol_result_ok_int({val});")
                else:
                    self._emit(f"return {val};")
            else:
                self._emit("return;")

        elif isinstance(stmt, BreakStmt):
            self._emit("break;")

        elif isinstance(stmt, ContinueStmt):
            self._emit("continue;")

        elif isinstance(stmt, FailStmt):
            msg = self._gen_expr(stmt.message)
            self._emit_defers()
            self._emit(f"return kol_result_err({msg});")

        elif isinstance(stmt, FunctionDecl):
            self._gen_function_decl(stmt)

        else:
            expr = self._gen_expr(stmt)
            if expr:
                self._emit(f"{expr};")

    def _gen_var_decl(self, decl: VarDecl, global_scope: bool = False):
        type_str = "int64_t"
        type_key = "int"

        if decl.type_annot:
            tname = decl.type_annot.name
            type_key = tname
            if tname == "int": type_str = "int64_t"
            elif tname == "float": type_str = "double"
            elif tname == "bool": type_str = "bool"
            elif tname == "str": type_str = "KolStr"
            else: type_str = tname
        elif decl.value:
            if isinstance(decl.value, StrLit) or isinstance(decl.value, StrInterp):
                type_key = "str"
                type_str = "KolStr"
            elif isinstance(decl.value, FloatLit):
                type_key = "float"
                type_str = "double"
            elif isinstance(decl.value, BoolLit):
                type_key = "bool"
                type_str = "bool"
            elif isinstance(decl.value, IntLit):
                type_key = "int"
                type_str = "int64_t"
            elif isinstance(decl.value, Ident):
                type_key = self.var_types.get(decl.value.name, "int")
                if type_key == "str": type_str = "KolStr"
                elif type_key == "float": type_str = "double"
                elif type_key == "bool": type_str = "bool"
                elif type_key == "int": type_str = "int64_t"
                else: type_str = type_key
            elif isinstance(decl.value, CallExpr) and isinstance(decl.value.callee, Ident) and decl.value.callee.name in self.struct_fields:
                type_key = decl.value.callee.name
                type_str = type_key
            elif isinstance(decl.value, ListExpr):
                type_key = "kol_array_t"
                type_str = "kol_array_t"

        self.var_types[decl.name] = type_key

        if decl.value:
            if isinstance(decl.value, CallExpr) and isinstance(decl.value.callee, Ident) and decl.value.callee.name in self.struct_fields:
                struct_name = decl.value.callee.name
                self._emit(f"{struct_name} {decl.name};")
                for arg_name, arg_expr in decl.value.named_args:
                    val_c = self._gen_expr(arg_expr)
                    self._emit(f"{decl.name}.{arg_name} = {val_c};")
            else:
                val_str = self._gen_expr(decl.value)
                self._emit(f"{type_str} {decl.name} = {val_str};")
                if type_key == "str" and isinstance(decl.value, Ident):
                    self._emit(f"kol_arc_retain_str({decl.name});")
        else:
            self._emit(f"{type_str} {decl.name};")

    def _gen_function_decl(self, decl: FunctionDecl):
        ret_type = "void"
        is_fallible = False
        if decl.return_type:
            rt = decl.return_type.name
            if decl.return_type.is_fallible:
                ret_type = "KolResult"
                is_fallible = True
            elif rt == "int": ret_type = "int64_t"
            elif rt == "float": ret_type = "double"
            elif rt == "bool": ret_type = "bool"
            elif rt == "str": ret_type = "KolStr"
            else: ret_type = rt

        c_fn_name = "main" if decl.name == "main" else f"_kol_fn_{decl.name}"

        params_code = []
        for p in decl.params:
            pt = "int64_t"
            pk = "int"
            if p.type_annot:
                pn = p.type_annot.name
                pk = pn
                if pn == "int": pt = "int64_t"
                elif pn == "float": pt = "double"
                elif pn == "bool": pt = "bool"
                elif pn == "str": pt = "KolStr"
            self.var_types[p.name] = pk
            params_code.append(f"{pt} {p.name}")

        param_str = ", ".join(params_code) if params_code else "void"

        self._emit(f"{ret_type} {c_fn_name}({param_str}) {{")
        self.indent_level += 1
        self.defer_stack.append([])

        body = decl.body
        for idx, stmt in enumerate(body):
            if idx == len(body) - 1 and ret_type != "void" and not isinstance(stmt, ReturnStmt):
                if not isinstance(stmt, (VarDecl, Assignment, IfStmt, ForStmt, WhileStmt, LoopStmt)):
                    val = self._gen_expr(stmt)
                    self._emit_defers()
                    if is_fallible:
                        self._emit(f"return kol_result_ok_int({val});")
                    else:
                        self._emit(f"return {val};")
                    continue
            self._gen_statement(stmt, current_fn_fallible=is_fallible)

        self._emit_defers()
        self.defer_stack.pop()

        self.indent_level -= 1
        self._emit("}")
        self._emit("")

    def _gen_expr(self, expr: ASTNode) -> str:
        if isinstance(expr, IntLit):
            return str(expr.value)

        if isinstance(expr, FloatLit):
            return str(expr.value)

        if isinstance(expr, StrLit):
            escaped = expr.value.replace('"', '\\"').replace('\n', '\\n')
            return f'kol_str_create("{escaped}")'

        if isinstance(expr, BoolLit):
            return "true" if expr.value else "false"

        if isinstance(expr, NoneLit):
            return "0"

        if isinstance(expr, Ident):
            return expr.name

        if isinstance(expr, PipeExpr):
            # left |> right
            left_c = self._gen_expr(expr.left)
            if isinstance(expr.right, Ident):
                fn_name = f"_kol_fn_{expr.right.name}"
                return f"{fn_name}({left_c})"
            elif isinstance(expr.right, CallExpr) and isinstance(expr.right.callee, Ident):
                fn_name = f"_kol_fn_{expr.right.callee.name}"
                args = [left_c] + [self._gen_expr(a) for a in expr.right.args]
                return f"{fn_name}({', '.join(args)})"
            return f"0"

        if isinstance(expr, StrInterp):
            fmt_parts = []
            args = []
            for part in expr.parts:
                if isinstance(part, str):
                    fmt_parts.append(part.replace('%', '%%'))
                else:
                    sub_expr = self._gen_expr(part)
                    t = self._infer_expr_type(part)
                    if t == "str":
                        fmt_parts.append("%s")
                        args.append(f"kol_str_cstr(&{sub_expr})")
                    elif t == "float":
                        fmt_parts.append("%g")
                        args.append(f"({sub_expr})")
                    elif t == "bool":
                        fmt_parts.append("%s")
                        args.append(f"(({sub_expr}) ? \"true\" : \"false\")")
                    else:
                        fmt_parts.append("%lld")
                        args.append(f"(long long)({sub_expr})")

            fmt_str = "".join(fmt_parts)
            arg_str = ", ".join(args)
            if arg_str:
                return f'kol_str_format("{fmt_str}", {arg_str})'
            return f'kol_str_create("{fmt_str}")'

        if isinstance(expr, BinOp):
            left = self._gen_expr(expr.left)
            right = self._gen_expr(expr.right)
            op = expr.op
            if op == "and": op = "&&"
            elif op == "or": op = "||"
            elif op == "mod": op = "%"
            return f"({left} {op} {right})"

        if isinstance(expr, UnaryOp):
            operand = self._gen_expr(expr.operand)
            op = expr.op
            if op == "not": op = "!"
            return f"({op}{operand})"

        if isinstance(expr, OrExpr):
            e_str = self._gen_expr(expr.expr)
            d_str = self._gen_expr(expr.default_val)
            res_var = self._temp_var("res")
            self._emit(f"KolResult {res_var} = {e_str};")
            return f"({res_var}.ok ? {res_var}.val_int : {d_str})"

        if isinstance(expr, FieldAccess):
            target = self._gen_expr(expr.target)
            return f"({target}.{expr.field_name})"

        if isinstance(expr, IndexExpr):
            target = self._gen_expr(expr.target)
            idx = self._gen_expr(expr.index)
            return f"(*((int64_t*)kol_array_get(&{target}, {idx}, \"{self.filename}\", {expr.location.line if expr.location else 0})))"

        if isinstance(expr, ListExpr):
            arr_var = self._temp_var("arr")
            self._emit(f"kol_array_t {arr_var} = kol_array_create(sizeof(int64_t), {len(expr.elements)});")
            for elem in expr.elements:
                elem_c = self._gen_expr(elem)
                tmp_elem = self._temp_var("elem")
                self._emit(f"int64_t {tmp_elem} = {elem_c};")
                self._emit(f"kol_array_push(&{arr_var}, &{tmp_elem});")
            return arr_var

        if isinstance(expr, CallExpr):
            if isinstance(expr.callee, Ident) and expr.callee.name == "print":
                if expr.args:
                    arg = expr.args[0]
                    arg_c = self._gen_expr(arg)
                    t = self._infer_expr_type(arg)
                    if t == "str":
                        return f"kol_print_str({arg_c})"
                    elif t == "float":
                        return f"kol_print_float({arg_c})"
                    elif t == "bool":
                        return f"kol_print_bool({arg_c})"
                    return f"kol_print_int({arg_c})"
                return 'printf("\\n")'

            callee_name = self._gen_expr(expr.callee)
            if isinstance(expr.callee, Ident):
                callee_name = f"_kol_fn_{expr.callee.name}"

            arg_strs = [self._gen_expr(a) for a in expr.args]
            return f"{callee_name}({', '.join(arg_strs)})"

        if isinstance(expr, MethodCallExpr):
            obj = self._gen_expr(expr.object)
            if expr.method_name == "upper":
                return f"kol_str_upper({obj})"
            elif expr.method_name == "lower":
                return f"kol_str_lower({obj})"
            elif expr.method_name == "trim":
                return f"kol_str_trim({obj})"
            arg_strs = [self._gen_expr(a) for a in expr.args]
            args_all = [f"&{obj}"] + arg_strs
            return f"_kol_method_{expr.method_name}({', '.join(args_all)})"

        return "0"

    def _infer_expr_type(self, expr: ASTNode) -> str:
        if isinstance(expr, StrLit) or isinstance(expr, StrInterp):
            return "str"
        if isinstance(expr, FloatLit):
            return "float"
        if isinstance(expr, BoolLit):
            return "bool"
        if isinstance(expr, IntLit):
            return "int"
        if isinstance(expr, FieldAccess):
            if isinstance(expr.target, Ident):
                st_name = self.var_types.get(expr.target.name, "")
                if st_name in self.struct_fields:
                    for fn, ft in self.struct_fields[st_name]:
                        if fn == expr.field_name:
                            if ft == "KolStr": return "str"
                            elif ft == "double": return "float"
                            elif ft == "bool": return "bool"
                            return "int"
        if isinstance(expr, Ident):
            return self.var_types.get(expr.name, "int")
        return "int"
