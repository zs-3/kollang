from typing import List, Set, Dict, Optional, Tuple
from compiler.ast_nodes import *

C_KEYWORDS = {
    "double", "float", "int", "char", "long", "short",
    "void", "auto", "register", "static", "extern",
    "const", "volatile", "signed", "unsigned", "struct",
    "union", "enum", "typedef", "goto", "sizeof",
    "return", "break", "continue", "switch", "case",
    "default", "for", "while", "do", "if", "else"
}

class Codegen:
    def __init__(self, filename: str, release_mode: bool = False):
        self.filename = filename
        self.release_mode = release_mode
        self.code: List[str] = []
        self.type_decls: List[str] = []
        self.proto_decls: List[str] = []
        self.helper_funcs: List[str] = []
        self.indent_level = 0
        self.temp_var_count = 0
        self.lambda_count = 0
        self.var_types: Dict[str, str] = {}
        self.func_ret_types: Dict[str, str] = {}
        self.struct_fields: Dict[str, List[tuple]] = {}
        self.nested_type_registry: Dict[str, str] = {}
        self.type_impl_methods: Dict[str, Dict[str, str]] = {}
        self.type_constants: Dict[str, Dict[str, str]] = {}
        self.enum_defs: Dict[str, EnumDecl] = {}
        self.generic_func_defs: Dict[str, FunctionDecl] = {}
        self.generic_instances: Set[Tuple[str, str]] = set()
        self.current_type_name: Optional[str] = None
        self.defer_stack: List[List[ASTNode]] = []

    def _emit(self, line: str):
        indent = "    " * self.indent_level
        self.code.append(f"{indent}{line}")

    def _safe_c_name(self, name: str) -> str:
        if name == "self":
            return "self"
        if name in C_KEYWORDS:
            return f"_kol_var_{name}"
        return f"_kol_{name}"

    def _temp_var(self, prefix: str = "tmp") -> str:
        self.temp_var_count += 1
        return f"_kol_{prefix}_{self.temp_var_count}"

    def _collect_type_decl(self, decl: TypeDecl, prefix: str = ""):
        effective_name = decl.c_name if decl.c_name else (f"{prefix}{decl.name}" if prefix else decl.name)
        if prefix:
            self.nested_type_registry[f"{prefix[:-1]}.{decl.name}"] = effective_name

        for n_type in decl.nested_types:
            self._collect_type_decl(n_type, prefix=f"{effective_name}_")

        fields = []
        field_decls = []
        for f in decl.fields:
            fn = f.name
            ft = "int64_t"
            if f.type_annot:
                if f.type_annot.name == "str": ft = "KolStr"
                elif f.type_annot.name == "float": ft = "double"
                elif f.type_annot.name == "bool": ft = "bool"
                elif f.type_annot.name in self.struct_fields or f.type_annot.name in self.enum_defs or f.type_annot.name in self.nested_type_registry.values(): ft = f.type_annot.name
            fields.append((fn, ft))
            field_decls.append(f"    {ft} {fn};")
        self.struct_fields[effective_name] = fields
        self.type_decls.append(f"typedef struct {{\n" + "\n".join(field_decls) + f"\n}} {effective_name};")

        if effective_name not in self.type_impl_methods:
            self.type_impl_methods[effective_name] = {}
        for m in decl.methods:
            self.type_impl_methods[effective_name][m.name] = m.return_type.name if m.return_type else "void"
        for impl_b in decl.impls:
            for m in impl_b.methods:
                self.type_impl_methods[effective_name][m.name] = m.return_type.name if m.return_type else "void"

        if effective_name not in self.type_constants:
            self.type_constants[effective_name] = {}

        for c in decl.constants:
            c_type = "double" if isinstance(c.value, FloatLit) else ("KolStr" if isinstance(c.value, (StrLit, StrInterp)) else "int64_t")
            if c.type_annot:
                cn = c.type_annot.name
                if cn == "float": c_type = "double"
                elif cn == "str": c_type = "KolStr"
                elif cn == "bool": c_type = "bool"
                elif cn == "int": c_type = "int64_t"
                else: c_type = cn
            val_c = self._gen_expr(c.value)
            const_var = f"_kol_{effective_name}_{c.name}"
            self.type_decls.append(f"static const {c_type} {const_var} = {val_c};")
            self.type_constants[effective_name][c.name] = c_type

    def _gen_proto_decls_for_type(self, decl: TypeDecl):
        for n_type in decl.nested_types:
            self._gen_proto_decls_for_type(n_type)

        effective_name = decl.c_name if decl.c_name else decl.name
        for m in decl.methods:
            rt = "void"
            if m.return_type:
                if m.return_type.name == "str": rt = "KolStr"
                elif m.return_type.name == "float": rt = "double"
                elif m.return_type.name == "bool": rt = "bool"
                elif m.return_type.name == "int": rt = "int64_t"
                else: rt = m.return_type.name

            params_code = [f"{effective_name}* self"]
            for p in m.params:
                pt = "int64_t"
                if p.type_annot:
                    if p.type_annot.name == "str": pt = "KolStr"
                    elif p.type_annot.name == "float": pt = "double"
                    elif p.type_annot.name == "bool": pt = "bool"
                    elif p.type_annot.name == "int": pt = "int64_t"
                    else: pt = p.type_annot.name
                params_code.append(f"{pt} {p.name}")
            p_str = ", ".join(params_code)
            self.proto_decls.append(f"{rt} _kol_method_{effective_name}_{m.name}({p_str});")

        for impl_b in decl.impls:
            for m in impl_b.methods:
                rt = "void"
                if m.return_type:
                    if m.return_type.name == "str": rt = "KolStr"
                    elif m.return_type.name == "float": rt = "double"
                    elif m.return_type.name == "bool": rt = "bool"
                    elif m.return_type.name == "int": rt = "int64_t"
                    else: rt = m.return_type.name

                params_code = [f"{effective_name}* self"]
                for p in m.params:
                    pt = "int64_t"
                    if p.type_annot:
                        if p.type_annot.name == "str": pt = "KolStr"
                        elif p.type_annot.name == "float": pt = "double"
                        elif p.type_annot.name == "bool": pt = "bool"
                        elif p.type_annot.name == "int": pt = "int64_t"
                        else: pt = p.type_annot.name
                    params_code.append(f"{pt} {p.name}")
                p_str = ", ".join(params_code)
                self.proto_decls.append(f"{rt} _kol_impl_{impl_b.interface_name}_{effective_name}_{m.name}({p_str});")
                self.proto_decls.append(f"{rt} _kol_method_{effective_name}_{m.name}({p_str});")

    def _gen_type_decl_methods(self, decl: TypeDecl):
        for n_type in decl.nested_types:
            self._gen_type_decl_methods(n_type)
        effective_name = decl.c_name if decl.c_name else decl.name
        self.current_type_name = effective_name
        for m in decl.methods:
            self._gen_method_decl(effective_name, m)
        for impl_b in decl.impls:
            for m in impl_b.methods:
                self._gen_method_decl(effective_name, m, impl_interface=impl_b.interface_name)
        self.current_type_name = None

    def generate(self, node: ASTNode, is_test_mode: bool = False, release_mode: bool = False) -> str:
        if release_mode:
            self.release_mode = True
        self.is_test_mode = is_test_mode
        self.code = []
        self.type_decls = []
        self.proto_decls = []
        self.helper_funcs = []

        all_nodes = []
        if isinstance(node, Program):
            all_nodes = node.declarations
        elif isinstance(node, ScriptProgram):
            all_nodes = node.statements

        # Pre-pass: collect types, enums, functions
        for decl in all_nodes:
            if isinstance(decl, FunctionDecl):
                if decl.generic_params:
                    self.generic_func_defs[decl.name] = decl
                else:
                    if decl.return_type:
                        rt_name = decl.return_type.name
                        if decl.return_type.is_optional:
                            if rt_name == "str": self.func_ret_types[decl.name] = "KolOptStr"
                            elif rt_name == "float": self.func_ret_types[decl.name] = "KolOptFloat"
                            else: self.func_ret_types[decl.name] = "KolOptInt"
                        else:
                            self.func_ret_types[decl.name] = rt_name
                    else:
                        self.func_ret_types[decl.name] = "void"

            elif isinstance(decl, EnumDecl):
                self.enum_defs[decl.name] = decl
                has_payload = any(v.fields for v in decl.variants)
                if not has_payload:
                    variants_c = [f"    {decl.name}_{v.name}" for v in decl.variants]
                    self.type_decls.append(f"typedef enum {{\n" + ",\n".join(variants_c) + f"\n}} {decl.name};")
                else:
                    tag_variants = [f"    {decl.name}_{v.name}" for v in decl.variants]
                    tag_enum = f"typedef enum {{\n" + ",\n".join(tag_variants) + f"\n}} {decl.name}Tag;"

                    union_fields = []
                    for v in decl.variants:
                        if v.fields:
                            f_decls = []
                            for f in v.fields:
                                ft = "int64_t"
                                if f.type_annot:
                                    if f.type_annot.name == "float": ft = "double"
                                    elif f.type_annot.name == "str": ft = "KolStr"
                                    elif f.type_annot.name == "bool": ft = "bool"
                                f_decls.append(f"            {ft} {f.name};")
                            union_fields.append(f"        struct {{\n" + "\n".join(f_decls) + f"\n        }} {v.name};")

                    union_str = f"    union {{\n" + "\n".join(union_fields) + f"\n    }} data;" if union_fields else ""
                    struct_enum = f"typedef struct {{\n    {decl.name}Tag tag;\n{union_str}\n}} {decl.name};"
                    self.type_decls.append(f"{tag_enum}\n{struct_enum}")

            elif isinstance(decl, TypeDecl):
                self._collect_type_decl(decl)

        # Forward declarations for functions and methods
        for decl in all_nodes:
            if isinstance(decl, FunctionDecl) and not decl.generic_params and decl.name != "main":
                rt = "void"
                if decl.return_type:
                    if decl.return_type.is_fallible:
                        rt = "KolResult"
                    elif decl.return_type.is_optional:
                        if decl.return_type.name == "str": rt = "KolOptStr"
                        elif decl.return_type.name == "float": rt = "KolOptFloat"
                        else: rt = "KolOptInt"
                    elif decl.return_type.name == "str": rt = "KolStr"
                    elif decl.return_type.name == "float": rt = "double"
                    elif decl.return_type.name == "bool": rt = "bool"
                    elif decl.return_type.name == "int": rt = "int64_t"
                    else: rt = decl.return_type.name

                params_code = []
                for p in decl.params:
                    pt = "int64_t"
                    if p.type_annot:
                        if p.type_annot.name == "str": pt = "KolStr"
                        elif p.type_annot.name == "float": pt = "double"
                        elif p.type_annot.name == "bool": pt = "bool"
                        elif p.type_annot.name == "int": pt = "int64_t"
                        else: pt = p.type_annot.name
                    params_code.append(f"{pt} {p.name}")
                p_str = ", ".join(params_code) if params_code else "void"
                prefix = "static inline " if decl.is_pure else ""
                self.proto_decls.append(f"{prefix}{rt} _kol_fn_{decl.name}({p_str});")

            elif isinstance(decl, TypeDecl):
                self._gen_proto_decls_for_type(decl)

        body_code = []
        self.code = body_code

        if isinstance(node, ScriptProgram):
            for stmt in node.statements:
                if isinstance(stmt, FunctionDecl) and not stmt.generic_params:
                    self._gen_function_decl(stmt)

            if not is_test_mode:
                self._emit("int main(void) {")
                self.indent_level += 1
                self.defer_stack.append([])
                for stmt in node.statements:
                    if not isinstance(stmt, (TypeDecl, EnumDecl, InterfaceDecl, UseStmt, FunctionDecl, TestBlock)):
                        self._gen_statement(stmt)
                self._emit_defers()
                self.defer_stack.pop()
                self._emit("return 0;")
                self.indent_level -= 1
                self._emit("}")
        elif isinstance(node, Program):
            for decl in node.declarations:
                if isinstance(decl, FunctionDecl):
                    if not decl.generic_params:
                        self._gen_function_decl(decl)
                elif isinstance(decl, TypeDecl):
                    self._gen_type_decl_methods(decl)
                elif isinstance(decl, VarDecl):
                    self._gen_var_decl(decl, global_scope=True)
                elif not isinstance(decl, (EnumDecl, InterfaceDecl, UseStmt)):
                    self._gen_statement(decl)

            has_main = any(isinstance(d, FunctionDecl) and d.name == "main" for d in node.declarations)
            if not has_main:
                self._emit("int main(void) {")
                self._emit("    return 0;")
                self._emit("}")

        header_inc = '#include "kol_runtime.h"\n\n'
        type_section = "\n".join(self.type_decls) + "\n\n" if self.type_decls else ""
        proto_section = "\n".join(self.proto_decls) + "\n\n" if self.proto_decls else ""
        helper_section = "\n".join(self.helper_funcs) + "\n\n" if self.helper_funcs else ""

        full_code = header_inc + type_section + proto_section + helper_section + "\n".join(body_code)
        return full_code

    def generate_test_runner(self, program: ASTNode, source: str) -> str:
        """Generate C code that runs test blocks."""
        self.code = []
        self.type_decls = []
        self.proto_decls = []
        self.helper_funcs = []
        self.indent_level = 0
        self.defer_stack = []

        all_nodes = []
        if isinstance(program, Program):
            all_nodes = program.declarations
        elif isinstance(program, ScriptProgram):
            all_nodes = program.statements

        # Pre-pass: collect types, enums, functions
        for decl in all_nodes:
            if isinstance(decl, FunctionDecl):
                if decl.generic_params:
                    self.generic_func_defs[decl.name] = decl
                else:
                    if decl.return_type:
                        rt_name = decl.return_type.name
                        if decl.return_type.is_optional:
                            if rt_name == "str": self.func_ret_types[decl.name] = "KolOptStr"
                            elif rt_name == "float": self.func_ret_types[decl.name] = "KolOptFloat"
                            else: self.func_ret_types[decl.name] = "KolOptInt"
                        else:
                            self.func_ret_types[decl.name] = rt_name
                    else:
                        self.func_ret_types[decl.name] = "void"
            elif isinstance(decl, EnumDecl):
                self.enum_defs[decl.name] = decl
                has_payload = any(v.fields for v in decl.variants)
                if not has_payload:
                    variants_c = [f"    {decl.name}_{v.name}" for v in decl.variants]
                    self.type_decls.append(f"typedef enum {{\n" + ",\n".join(variants_c) + f"\n}} {decl.name};")
                else:
                    tag_variants = [f"    {decl.name}_{v.name}" for v in decl.variants]
                    tag_enum = f"typedef enum {{\n" + ",\n".join(tag_variants) + f"\n}} {decl.name}Tag;"
                    union_fields = []
                    for v in decl.variants:
                        if v.fields:
                            f_decls = []
                            for f in v.fields:
                                ft = "int64_t"
                                if f.type_annot:
                                    if f.type_annot.name == "float": ft = "double"
                                    elif f.type_annot.name == "str": ft = "KolStr"
                                    elif f.type_annot.name == "bool": ft = "bool"
                                f_decls.append(f"            {ft} {f.name};")
                            union_fields.append(f"        struct {{\n" + "\n".join(f_decls) + f"\n        }} {v.name};")
                    union_str = f"    union {{\n" + "\n".join(union_fields) + f"\n    }} data;" if union_fields else ""
                    struct_enum = f"typedef struct {{\n    {decl.name}Tag tag;\n{union_str}\n}} {decl.name};"
                    self.type_decls.append(f"{tag_enum}\n{struct_enum}")
            elif isinstance(decl, TypeDecl):
                self._collect_type_decl(decl)

        # Forward declarations
        for decl in all_nodes:
            if isinstance(decl, FunctionDecl) and not decl.generic_params and decl.name != "main":
                rt = "void"
                if decl.return_type:
                    if decl.return_type.is_fallible: rt = "KolResult"
                    elif decl.return_type.is_optional:
                        if decl.return_type.name == "str": rt = "KolOptStr"
                        elif decl.return_type.name == "float": rt = "KolOptFloat"
                        else: rt = "KolOptInt"
                    elif decl.return_type.name == "str": rt = "KolStr"
                    elif decl.return_type.name == "float": rt = "double"
                    elif decl.return_type.name == "bool": rt = "bool"
                    elif decl.return_type.name == "int": rt = "int64_t"
                    else: rt = decl.return_type.name

                params_code = []
                for p in decl.params:
                    pt = "int64_t"
                    if p.type_annot:
                        if p.type_annot.name == "str": pt = "KolStr"
                        elif p.type_annot.name == "float": pt = "double"
                        elif p.type_annot.name == "bool": pt = "bool"
                        elif p.type_annot.name == "int": pt = "int64_t"
                        else: pt = p.type_annot.name
                    params_code.append(f"{pt} {p.name}")
                p_str = ", ".join(params_code) if params_code else "void"
                prefix = "static inline " if decl.is_pure else ""
                self.proto_decls.append(f"{prefix}{rt} _kol_fn_{decl.name}({p_str});")

            elif isinstance(decl, TypeDecl):
                self._gen_proto_decls_for_type(decl)

        body_code = []
        self.code = body_code

        # First pass: emit non-test declarations
        for node in all_nodes:
            if isinstance(node, FunctionDecl) and not node.generic_params:
                self._gen_function_decl(node)
            elif isinstance(node, TypeDecl):
                self._gen_type_decl_methods(node)

        # Second pass: emit test functions
        test_nodes = [n for n in all_nodes if isinstance(n, TestBlock)]

        for i, test in enumerate(test_nodes):
            self._emit(f'void _kol_test_{i}(void) {{')
            self.indent_level += 1
            self._emit(f'printf("  test \\"{test.name}\\"\\n");')
            for stmt in test.body:
                self._gen_statement(stmt)
            self.indent_level -= 1
            self._emit('}')
            self._emit('')

        # Main entry point
        self._emit('int main(void) {')
        self.indent_level += 1
        self._emit(f'printf("Running {len(test_nodes)} test(s)...\\n");')
        for i in range(len(test_nodes)):
            self._emit(f'_kol_test_{i}();')
        self._emit('if (_kol_test_failures == 0)')
        self._emit('    printf("All tests passed.\\n");')
        self._emit('else')
        self._emit('    printf("%d test(s) failed.\\n", _kol_test_failures);')
        self._emit('return _kol_test_failures > 0 ? 1 : 0;')
        self.indent_level -= 1
        self._emit('}')

        header_inc = '#include <stdio.h>\n#include <stdbool.h>\n#include "kol_runtime.h"\n\nint _kol_test_failures = 0;\n\n'
        type_section = "\n".join(self.type_decls) + "\n\n" if self.type_decls else ""
        proto_section = "\n".join(self.proto_decls) + "\n\n" if self.proto_decls else ""
        helper_section = "\n".join(self.helper_funcs) + "\n\n" if self.helper_funcs else ""

        full_code = header_inc + type_section + proto_section + helper_section + "\n".join(body_code)
        return full_code

    def _emit_defers(self):
        if self.defer_stack and self.defer_stack[-1]:
            for defer_stmt in reversed(self.defer_stack[-1]):
                expr_c = self._gen_expr(defer_stmt.call)
                if expr_c:
                    self._emit(f"{expr_c};")

    def _gen_statement(self, stmt: ASTNode, current_fn_fallible: bool = False, current_fn_optional: bool = False, opt_ret_kind: str = "int", is_return: bool = False):
        if isinstance(stmt, VarDecl):
            self._gen_var_decl(stmt)
        elif isinstance(stmt, UseStmt):
            pass
        elif isinstance(stmt, Assignment):
            if isinstance(stmt.target, Ident) and stmt.op == "=" and isinstance(stmt.value, BinOp) and stmt.value.op == "+":
                if isinstance(stmt.value.left, Ident) and stmt.value.left.name == stmt.target.name and self.var_types.get(stmt.target.name) == "str":
                    right_val = self._gen_expr(stmt.value.right)
                    target_safe = self._safe_c_name(stmt.target.name)
                    self._emit(f"kol_str_append(&{target_safe}, {right_val});")
                    return
            target = self._gen_expr(stmt.target)
            val = self._gen_expr(stmt.value)
            self._emit(f"{target} {stmt.op} {val};")
            if isinstance(stmt.target, Ident) and self.var_types.get(stmt.target.name) == "str":
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
                self._gen_statement(s, current_fn_fallible, current_fn_optional, opt_ret_kind)
            self.indent_level -= 1

            for elif_cond, elif_body in stmt.elif_branches:
                e_cond = self._gen_expr(elif_cond)
                self._emit(f"}} else if ({e_cond}) {{")
                self.indent_level += 1
                for s in elif_body:
                    self._gen_statement(s, current_fn_fallible, current_fn_optional, opt_ret_kind)
                self.indent_level -= 1

            if stmt.else_branch is not None:
                self._emit("} else {")
                self.indent_level += 1
                for s in stmt.else_branch:
                    self._gen_statement(s, current_fn_fallible, current_fn_optional, opt_ret_kind)
                self.indent_level -= 1

            self._emit("}")

        elif isinstance(stmt, MatchStmt):
            val_var = self._temp_var("match")
            target_expr = self._gen_expr(stmt.expr)
            target_type = self._infer_expr_type(stmt.expr)

            if target_type in self.enum_defs:
                enum_decl = self.enum_defs[target_type]
                has_payload = any(v.fields for v in enum_decl.variants)
                if has_payload:
                    self._emit(f"{target_type} {val_var} = {target_expr};")
                    self._emit(f"switch ({val_var}.tag) {{")
                    for arm in stmt.arms:
                        self.indent_level += 1
                        if isinstance(arm.pattern, CallExpr) and isinstance(arm.pattern.callee, Ident):
                            var_name = arm.pattern.callee.name
                            self._emit(f"case {target_type}_{var_name}: {{")
                            self.indent_level += 1
                            v_decl = next((v for v in enum_decl.variants if v.name == var_name), None)
                            if v_decl and v_decl.fields:
                                for idx_f, arg_expr in enumerate(arm.pattern.args):
                                    if isinstance(arg_expr, Ident):
                                        f_info = v_decl.fields[idx_f]
                                        ft = "int64_t"
                                        if f_info.type_annot:
                                            if f_info.type_annot.name == "float": ft = "double"
                                            elif f_info.type_annot.name == "str": ft = "KolStr"
                                        self.var_types[arg_expr.name] = f_info.type_annot.name if f_info.type_annot else "int"
                                        safe_bind = self._safe_c_name(arg_expr.name)
                                        self._emit(f"{ft} {safe_bind} = {val_var}.data.{var_name}.{f_info.name};")
                            if is_return:
                                ret_v = self._gen_expr(arm.body)
                                self._emit(f"return {ret_v};")
                            else:
                                self._gen_statement(arm.body, current_fn_fallible, current_fn_optional, opt_ret_kind)
                                self._emit("break;")
                            self.indent_level -= 1
                            self._emit("}")
                        elif isinstance(arm.pattern, Ident) and arm.pattern.name == "_":
                            self._emit("default: {")
                            self.indent_level += 1
                            if is_return:
                                ret_v = self._gen_expr(arm.body)
                                self._emit(f"return {ret_v};")
                            else:
                                self._gen_statement(arm.body, current_fn_fallible, current_fn_optional, opt_ret_kind)
                                self._emit("break;")
                            self.indent_level -= 1
                            self._emit("}")
                        self.indent_level -= 1
                    self._emit("}")
                    return

            self._emit(f"int64_t {val_var} = {target_expr};")
            is_first = True
            for arm in stmt.arms:
                if isinstance(arm.pattern, Ident) and arm.pattern.name == "_":
                    self._emit("} else {")
                elif isinstance(arm.pattern, RangeExpr):
                    start = self._gen_expr(arm.pattern.start)
                    end = self._gen_expr(arm.pattern.end)
                    op = "<=" if arm.pattern.inclusive else "<"
                    cond_str = f"({val_var} >= {start} && {val_var} {op} {end})"
                    if is_first:
                        self._emit(f"if ({cond_str}) {{")
                        is_first = False
                    else:
                        self._emit(f"}} else if ({cond_str}) {{")
                else:
                    pat_val = self._gen_expr(arm.pattern)
                    if is_first:
                        self._emit(f"if ({val_var} == {pat_val}) {{")
                        is_first = False
                    else:
                        self._emit(f"}} else if ({val_var} == {pat_val}) {{")
                self.indent_level += 1
                if is_return:
                    ret_v = self._gen_expr(arm.body)
                    self._emit(f"return {ret_v};")
                else:
                    self._gen_statement(arm.body, current_fn_fallible, current_fn_optional, opt_ret_kind)
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
                v_safe = self._safe_c_name(v)
                self._emit(f"for (int64_t {v_safe} = {start}; {v_safe} {op} {end}; {v_safe} += {step}) {{")
                self.indent_level += 1
                for s in stmt.body:
                    self._gen_statement(s, current_fn_fallible, current_fn_optional, opt_ret_kind)
                self.indent_level -= 1
                self._emit("}")
            else:
                arr_c = self._gen_expr(stmt.iterable)
                elem_v = stmt.var_name
                idx_c = self._temp_var("idx")

                list_type = "list_int"
                if isinstance(stmt.iterable, Ident):
                    list_type = self.var_types.get(stmt.iterable.name, "list_int")
                elif isinstance(stmt.iterable, ListExpr):
                    if stmt.iterable.elements:
                        first_t = self._infer_expr_type(stmt.iterable.elements[0])
                        list_type = f"list_{first_t}"

                if list_type == "list_str":
                    elem_c_type = "KolStr"
                    elem_type_key = "str"
                elif list_type == "list_float":
                    elem_c_type = "double"
                    elem_type_key = "float"
                elif list_type == "list_bool":
                    elem_c_type = "bool"
                    elem_type_key = "bool"
                elif list_type.startswith("list_"):
                    elem_c_type = list_type[5:]
                    elem_type_key = elem_c_type
                else:
                    elem_c_type = "int64_t"
                    elem_type_key = "int"

                self.var_types[elem_v] = elem_type_key
                elem_safe = self._safe_c_name(elem_v)

                self._emit(f"for (size_t {idx_c} = 0; {idx_c} < {arr_c}.len; {idx_c}++) {{")
                self.indent_level += 1
                if stmt.index_var:
                    self.var_types[stmt.index_var] = "int"
                    idx_safe = self._safe_c_name(stmt.index_var)
                    self._emit(f"int64_t {idx_safe} = (int64_t){idx_c};")

                if self.release_mode:
                    self._emit(f"{elem_c_type} {elem_safe} = (({elem_c_type}* restrict){arr_c}.data)[{idx_c}];")
                else:
                    line_num = stmt.location.line if stmt.location else 0
                    self._emit(f"{elem_c_type} {elem_safe} = *(({elem_c_type}*)kol_array_get(&{arr_c}, {idx_c}, \"{self.filename}\", {line_num}));")
                for s in stmt.body:
                    self._gen_statement(s, current_fn_fallible, current_fn_optional, opt_ret_kind)
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
                elif current_fn_optional:
                    if isinstance(stmt.value, NoneLit):
                        if opt_ret_kind == "str": self._emit("return kol_opt_str_none();")
                        elif opt_ret_kind == "float": self._emit("return kol_opt_float_none();")
                        else: self._emit("return kol_opt_int_none();")
                    else:
                        if opt_ret_kind == "str": self._emit(f"return kol_opt_str_some({val});")
                        elif opt_ret_kind == "float": self._emit(f"return kol_opt_float_some({val});")
                        else: self._emit(f"return kol_opt_int_some({val});")
                else:
                    self._emit(f"return {val};")
            else:
                if current_fn_optional:
                    if opt_ret_kind == "str": self._emit("return kol_opt_str_none();")
                    elif opt_ret_kind == "float": self._emit("return kol_opt_float_none();")
                    else: self._emit("return kol_opt_int_none();")
                else:
                    self._emit("return;")

        elif isinstance(stmt, AssertStmt):
            cond_c = self._gen_expr(stmt.condition)
            self._emit(f"if (!({cond_c})) {{")
            self.indent_level += 1
            self._emit('printf("    FAIL: assertion failed\\n");')
            self._emit('_kol_test_failures++;')
            self.indent_level -= 1
            self._emit("}")

        elif isinstance(stmt, BreakStmt):
            self._emit("break;")

        elif isinstance(stmt, ContinueStmt):
            self._emit("continue;")

        elif isinstance(stmt, FailStmt):
            msg = self._gen_expr(stmt.message)
            self._emit_defers()
            self._emit(f"return kol_result_err({msg});")

        elif isinstance(stmt, FunctionDecl):
            if not stmt.generic_params:
                self._gen_function_decl(stmt)

        else:
            expr = self._gen_expr(stmt)
            if expr:
                self._emit(f"{expr};")

    def _gen_var_decl(self, decl: VarDecl, global_scope: bool = False):
        if decl.value and isinstance(decl.value, LambdaExpr):
            self.lambda_count += 1
            lam_id = self.lambda_count
            ret_type = "int64_t"
            param_type = "int64_t"
            p_name = decl.value.params[0] if decl.value.params else "x"
            p_safe = self._safe_c_name(p_name)
            self.var_types[p_name] = "int"
            body_c = self._gen_expr(decl.value.body)

            lambda_func = f"static {ret_type} _kol_lambda_{lam_id}({param_type} {p_safe}) {{\n    return {body_c};\n}}"
            self.helper_funcs.append(lambda_func)

            safe_name = self._safe_c_name(decl.name)
            typedef_name = f"_kol_fn_t_{lam_id}"
            self.type_decls.append(f"typedef {ret_type} (*{typedef_name})({param_type});")
            self._emit(f"{typedef_name} {safe_name} = _kol_lambda_{lam_id};")
            self.var_types[decl.name] = f"lambda_{lam_id}"
            return

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
            elif isinstance(decl.value, MapExpr):
                type_key = "KolMap_si"
                type_str = "KolMap_si"
            elif isinstance(decl.value, Ident):
                type_key = self.var_types.get(decl.value.name, "int")
                if type_key == "str": type_str = "KolStr"
                elif type_key == "float": type_str = "double"
                elif type_key == "bool": type_str = "bool"
                elif type_key == "int": type_str = "int64_t"
                else: type_str = type_key
            elif isinstance(decl.value, CallExpr) and isinstance(decl.value.callee, FieldAccess) and isinstance(decl.value.callee.target, Ident):
                outer_n = decl.value.callee.target.name
                inner_n = decl.value.callee.field_name
                mangled = f"{outer_n}_{inner_n}"
                if mangled in self.struct_fields:
                    type_key = mangled
                    type_str = mangled
            elif isinstance(decl.value, CallExpr) and isinstance(decl.value.callee, Ident):
                fn_name = decl.value.callee.name
                if fn_name in self.func_ret_types:
                    type_key = self.func_ret_types[fn_name]
                    if type_key == "str": type_str = "KolStr"
                    elif type_key == "float": type_str = "double"
                    elif type_key == "bool": type_str = "bool"
                    elif type_key == "int": type_str = "int64_t"
                    else: type_str = type_key
                elif fn_name in self.struct_fields:
                    type_key = fn_name
                    type_str = fn_name
                elif fn_name in self.enum_defs:
                    type_key = fn_name
                    type_str = fn_name
                else:
                    for e_name, e_decl in self.enum_defs.items():
                        if any(v.name == fn_name for v in e_decl.variants):
                            type_key = e_name
                            type_str = e_name
                            break
            elif isinstance(decl.value, ListExpr):
                type_str = "kol_array_t"
                if decl.value.elements:
                    first_t = self._infer_expr_type(decl.value.elements[0])
                    if first_t == "str": type_key = "list_str"
                    elif first_t == "float": type_key = "list_float"
                    elif first_t == "bool": type_key = "list_bool"
                    elif first_t in self.struct_fields or first_t in self.enum_defs: type_key = f"list_{first_t}"
                    else: type_key = "list_int"
                else:
                    type_key = "list_int"

        self.var_types[decl.name] = type_key
        safe_decl_name = self._safe_c_name(decl.name)

        if decl.value:
            if isinstance(decl.value, CallExpr) and isinstance(decl.value.callee, FieldAccess) and isinstance(decl.value.callee.target, Ident) and f"{decl.value.callee.target.name}_{decl.value.callee.field_name}" in self.struct_fields:
                struct_name = f"{decl.value.callee.target.name}_{decl.value.callee.field_name}"
                self._emit(f"{struct_name} {safe_decl_name};")
                for arg_name, arg_expr in decl.value.named_args:
                    val_c = self._gen_expr(arg_expr)
                    self._emit(f"{safe_decl_name}.{arg_name} = {val_c};")
            elif isinstance(decl.value, CallExpr) and isinstance(decl.value.callee, Ident) and decl.value.callee.name in self.struct_fields:
                struct_name = decl.value.callee.name
                self._emit(f"{struct_name} {safe_decl_name};")
                for arg_name, arg_expr in decl.value.named_args:
                    val_c = self._gen_expr(arg_expr)
                    self._emit(f"{safe_decl_name}.{arg_name} = {val_c};")
            else:
                val_str = self._gen_expr(decl.value)
                self._emit(f"{type_str} {safe_decl_name} = {val_str};")
                if type_key == "str" and isinstance(decl.value, Ident):
                    self._emit(f"kol_arc_retain_str({safe_decl_name});")
        else:
            self._emit(f"{type_str} {safe_decl_name};")

    def _gen_function_decl(self, decl: FunctionDecl):
        is_main = (decl.name == "main")
        ret_type = "int" if is_main else "void"
        is_fallible = False
        is_optional = False
        opt_ret_kind = "int"
        if decl.return_type:
            rt = decl.return_type.name
            if decl.return_type.is_fallible:
                ret_type = "KolResult"
                is_fallible = True
            elif decl.return_type.is_optional:
                is_optional = True
                opt_ret_kind = rt
                if rt == "str": ret_type = "KolOptStr"
                elif rt == "float": ret_type = "KolOptFloat"
                else: ret_type = "KolOptInt"
            elif rt == "int": ret_type = "int64_t"
            elif rt == "float": ret_type = "double"
            elif rt == "bool": ret_type = "bool"
            elif rt == "str": ret_type = "KolStr"
            else: ret_type = rt

        c_fn_name = "main" if is_main else f"_kol_fn_{decl.name}"

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
                elif pn in self.struct_fields or pn in self.enum_defs: pt = pn
            self.var_types[p.name] = pk
            p_safe = self._safe_c_name(p.name)
            params_code.append(f"{pt} {p_safe}")

        param_str = ", ".join(params_code) if params_code else "void"

        inline_prefix = "static inline " if (decl.is_pure and not is_main) else ""
        self._emit(f"{inline_prefix}{ret_type} {c_fn_name}({param_str}) {{")
        self.indent_level += 1
        self.defer_stack.append([])

        body = decl.body
        for idx, stmt in enumerate(body):
            if idx == len(body) - 1 and ret_type != "void" and not is_main and not isinstance(stmt, ReturnStmt):
                if isinstance(stmt, MatchStmt):
                    self._gen_statement(stmt, current_fn_fallible=is_fallible, current_fn_optional=is_optional, opt_ret_kind=opt_ret_kind, is_return=True)
                    continue
                if not isinstance(stmt, (VarDecl, Assignment, IfStmt, ForStmt, WhileStmt, LoopStmt)):
                    val = self._gen_expr(stmt)
                    self._emit_defers()
                    if is_fallible:
                        self._emit(f"return kol_result_ok_int({val});")
                    elif is_optional:
                        if isinstance(stmt, NoneLit):
                            if opt_ret_kind == "str": self._emit("return kol_opt_str_none();")
                            elif opt_ret_kind == "float": self._emit("return kol_opt_float_none();")
                            else: self._emit("return kol_opt_int_none();")
                        else:
                            if opt_ret_kind == "str": self._emit(f"return kol_opt_str_some({val});")
                            elif opt_ret_kind == "float": self._emit(f"return kol_opt_float_some({val});")
                            else: self._emit(f"return kol_opt_int_some({val});")
                    else:
                        self._emit(f"return {val};")
                    continue
            self._gen_statement(stmt, current_fn_fallible=is_fallible, current_fn_optional=is_optional, opt_ret_kind=opt_ret_kind)

        self._emit_defers()
        if is_main:
            self._emit("return 0;")
        self.defer_stack.pop()

        self.indent_level -= 1
        self._emit("}")
        self._emit("")

    def _gen_method_decl(self, type_name: str, decl: FunctionDecl, impl_interface: Optional[str] = None):
        ret_type = "void"
        if decl.return_type:
            rt = decl.return_type.name
            if rt == "int": ret_type = "int64_t"
            elif rt == "float": ret_type = "double"
            elif rt == "bool": ret_type = "bool"
            elif rt == "str": ret_type = "KolStr"
            else: ret_type = rt

        if type_name not in self.type_impl_methods:
            self.type_impl_methods[type_name] = {}
        self.type_impl_methods[type_name][decl.name] = decl.return_type.name if decl.return_type else "void"

        c_fn_name = f"_kol_impl_{impl_interface}_{type_name}_{decl.name}" if impl_interface else f"_kol_method_{type_name}_{decl.name}"

        params_code = [f"{type_name}* self"]
        self.var_types["self"] = type_name

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

        param_str = ", ".join(params_code)

        self._emit(f"{ret_type} {c_fn_name}({param_str}) {{")
        self.indent_level += 1
        self.defer_stack.append([])

        body = decl.body
        for idx, stmt in enumerate(body):
            if idx == len(body) - 1 and ret_type != "void" and not isinstance(stmt, ReturnStmt):
                if not isinstance(stmt, (VarDecl, Assignment, IfStmt, ForStmt, WhileStmt, LoopStmt)):
                    val = self._gen_expr(stmt)
                    self._emit_defers()
                    self._emit(f"return {val};")
                    continue
            self._gen_statement(stmt)

        self._emit_defers()
        self.defer_stack.pop()

        self.indent_level -= 1
        self._emit("}")
        self._emit("")

        if impl_interface:
            alias_fn_name = f"_kol_method_{type_name}_{decl.name}"
            call_args = ["self"]
            for p in decl.params:
                p_safe = self._safe_c_name(p.name)
                call_args.append(p_safe)
            ret_prefix = "return " if ret_type != "void" else ""
            self._emit(f"{ret_type} {alias_fn_name}({param_str}) {{")
            self._emit(f"    {ret_prefix}{c_fn_name}({', '.join(call_args)});")
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
            if expr.name in self.var_types:
                return self._safe_c_name(expr.name)
            return expr.name

        if isinstance(expr, LambdaExpr):
            self.lambda_count += 1
            lambda_fn_name = f"_kol_lambda_{self.lambda_count}"
            p_name = expr.params[0] if expr.params else "x"
            p_safe = self._safe_c_name(p_name)
            body_c = self._gen_expr(expr.body)
            lambda_code = f"static int64_t {lambda_fn_name}(int64_t {p_safe}) {{\n    return {body_c};\n}}"
            self.helper_funcs.append(lambda_code)
            return lambda_fn_name

        if isinstance(expr, PipeExpr):
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
                        args.append(f"kol_str_cstr_tmp({sub_expr})")
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
            l_type = self._infer_expr_type(expr.left)
            r_type = self._infer_expr_type(expr.right)
            op = expr.op
            if l_type == "str" and r_type == "str" and op == "+":
                return f"kol_str_concat({left}, {right})"
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
            opt_var = self._temp_var("opt")

            is_opt = False
            opt_c_type = "KolOptInt"
            if isinstance(expr.expr, CallExpr) and isinstance(expr.expr.callee, Ident):
                fn_n = expr.expr.callee.name
                ret_t = self.func_ret_types.get(fn_n, "")
                if ret_t in ["KolOptStr", "KolOptInt", "KolOptFloat"] or ret_t.endswith("?"):
                    is_opt = True
                    if "str" in ret_t.lower() or "KolOptStr" in ret_t: opt_c_type = "KolOptStr"
                    elif "float" in ret_t.lower() or "KolOptFloat" in ret_t: opt_c_type = "KolOptFloat"

            if is_opt:
                self._emit(f"{opt_c_type} {opt_var} = {e_str};")
                return f"({opt_var}.has_value ? {opt_var}.value : {d_str})"
            else:
                res_var = self._temp_var("res")
                self._emit(f"KolResult {res_var} = {e_str};")
                return f"({res_var}.ok ? {res_var}.val_int : {d_str})"

        if isinstance(expr, FieldAccess):
            if isinstance(expr.target, Ident):
                t_name = expr.target.name
                if t_name in self.enum_defs:
                    return f"{t_name}_{expr.field_name}"
                elif t_name in self.type_constants and expr.field_name in self.type_constants[t_name]:
                    return f"_kol_{t_name}_{expr.field_name}"
                elif t_name in self.struct_fields:
                    if t_name in self.type_constants and expr.field_name in self.type_constants[t_name]:
                        return f"_kol_{t_name}_{expr.field_name}"
                elif t_name == "self":
                    return f"(self->{expr.field_name})"
            target = self._gen_expr(expr.target)
            return f"({target}.{expr.field_name})"

        if isinstance(expr, IndexExpr):
            target = self._gen_expr(expr.target)
            idx = self._gen_expr(expr.index)
            target_t = self._infer_expr_type(expr.target)
            if target_t == "KolMap_si":
                return f"kol_map_si_get(&{target}, {idx})"
            if self.release_mode:
                return f"(((int64_t* restrict){target}.data)[{idx}])"
            return f"(*((int64_t*)kol_array_get(&{target}, {idx}, \"{self.filename}\", {expr.location.line if expr.location else 0})))"

        if isinstance(expr, ListExpr):
            arr_var = self._temp_var("arr")
            elem_type_c = "int64_t"
            elem_size = "sizeof(int64_t)"
            if expr.elements:
                first_t = self._infer_expr_type(expr.elements[0])
                if first_t == "str":
                    elem_type_c = "KolStr"
                    elem_size = "sizeof(KolStr)"
                elif first_t == "float":
                    elem_type_c = "double"
                    elem_size = "sizeof(double)"
                elif first_t == "bool":
                    elem_type_c = "bool"
                    elem_size = "sizeof(bool)"
                elif first_t in self.struct_fields or first_t in self.enum_defs:
                    elem_type_c = first_t
                    elem_size = f"sizeof({first_t})"

            self._emit(f"kol_array_t {arr_var} = kol_array_create({elem_size}, {len(expr.elements)});")
            for elem in expr.elements:
                elem_c = self._gen_expr(elem)
                tmp_elem = self._temp_var("elem")
                self._emit(f"{elem_type_c} {tmp_elem} = {elem_c};")
                self._emit(f"kol_array_push(&{arr_var}, &{tmp_elem});")
            return arr_var

        if isinstance(expr, MapExpr):
            map_var = self._temp_var("map")
            self._emit(f"KolMap_si {map_var} = kol_map_si_create({len(expr.entries)});")
            for entry in expr.entries:
                k_c = self._gen_expr(entry.key)
                v_c = self._gen_expr(entry.value)
                self._emit(f"kol_map_si_set(&{map_var}, {k_c}, {v_c});")
            return map_var

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

            if isinstance(expr.callee, FieldAccess) and isinstance(expr.callee.target, Ident):
                outer_n = expr.callee.target.name
                inner_n = expr.callee.field_name
                mangled = f"{outer_n}_{inner_n}"
                if mangled in self.struct_fields:
                    init_var = self._temp_var(f"struct_{mangled}")
                    self._emit(f"{mangled} {init_var};")
                    if expr.named_args:
                        for arg_name, arg_expr in expr.named_args:
                            val_c = self._gen_expr(arg_expr)
                            self._emit(f"{init_var}.{arg_name} = {val_c};")
                    elif expr.args:
                        for idx_arg, arg_expr in enumerate(expr.args):
                            f_info = self.struct_fields[mangled][idx_arg]
                            val_c = self._gen_expr(arg_expr)
                            self._emit(f"{init_var}.{f_info[0]} = {val_c};")
                    return init_var

            if isinstance(expr.callee, Ident):
                fn_name = expr.callee.name
                v_type = self.var_types.get(fn_name, "")
                if v_type.startswith("lambda"):
                    safe_name = self._safe_c_name(fn_name)
                    arg_strs = [self._gen_expr(a) for a in expr.args]
                    return f"{safe_name}({', '.join(arg_strs)})"

                # Generic monomorphization call instantiation
                if fn_name in self.generic_func_defs:
                    g_decl = self.generic_func_defs[fn_name]
                    arg0 = expr.args[0] if expr.args else IntLit(0)
                    arg_t = self._infer_expr_type(arg0)
                    c_type = "int64_t"
                    suffix = "int"
                    if arg_t == "float":
                        c_type = "double"
                        suffix = "float"

                    inst_key = (fn_name, suffix)
                    inst_fn_name = f"_kol_fn_{fn_name}_{suffix}"
                    if inst_key not in self.generic_instances:
                        self.generic_instances.add(inst_key)
                        p_code = []
                        for p in g_decl.params:
                            p_code.append(f"{c_type} {p.name}")
                        p_str = ", ".join(p_code)
                        mono_code = f"{c_type} {inst_fn_name}({p_str}) {{\n"
                        for s in g_decl.body:
                            if isinstance(s, ReturnStmt) and s.value:
                                mono_code += f"    return ({self._gen_expr(s.value)});\n"
                            elif isinstance(s, IfStmt):
                                cond_c = self._gen_expr(s.condition)
                                then_v = self._gen_expr(s.then_branch[0].value) if isinstance(s.then_branch[0], ReturnStmt) else "0"
                                mono_code += f"    if ({cond_c}) return {then_v};\n"
                        mono_code += "}\n"
                        self.helper_funcs.append(mono_code)

                    arg_strs = [self._gen_expr(a) for a in expr.args]
                    return f"{inst_fn_name}({', '.join(arg_strs)})"

                if fn_name in self.struct_fields:
                    init_var = self._temp_var(f"struct_{fn_name}")
                    self._emit(f"{fn_name} {init_var};")
                    if expr.named_args:
                        for arg_name, arg_expr in expr.named_args:
                            val_c = self._gen_expr(arg_expr)
                            self._emit(f"{init_var}.{arg_name} = {val_c};")
                    elif expr.args:
                        for idx_arg, arg_expr in enumerate(expr.args):
                            f_info = self.struct_fields[fn_name][idx_arg]
                            val_c = self._gen_expr(arg_expr)
                            self._emit(f"{init_var}.{f_info[0]} = {val_c};")
                    return init_var

                # Tagged union enum constructor
                for enum_name, enum_decl in self.enum_defs.items():
                    for v in enum_decl.variants:
                        if v.name == fn_name:
                            init_var = self._temp_var(f"enum_{fn_name}")
                            self._emit(f"{enum_name} {init_var};")
                            self._emit(f"{init_var}.tag = {enum_name}_{fn_name};")
                            if expr.named_args:
                                for arg_name, arg_expr in expr.named_args:
                                    val_c = self._gen_expr(arg_expr)
                                    self._emit(f"{init_var}.data.{fn_name}.{arg_name} = {val_c};")
                            elif expr.args:
                                for idx_arg, arg_expr in enumerate(expr.args):
                                    f_info = v.fields[idx_arg]
                                    val_c = self._gen_expr(arg_expr)
                                    self._emit(f"{init_var}.data.{fn_name}.{f_info.name} = {val_c};")
                            return init_var

                callee_name = f"_kol_fn_{fn_name}"

            else:
                callee_name = self._gen_expr(expr.callee)

            arg_strs = [self._gen_expr(a) for a in expr.args]
            return f"{callee_name}({', '.join(arg_strs)})"

        if isinstance(expr, MethodCallExpr):
            obj = self._gen_expr(expr.object)
            obj_t = self._infer_expr_type(expr.object)

            if obj_t == "str":
                arg_strs = [self._gen_expr(a) for a in expr.args]
                m = expr.method_name
                if m == "upper": return f"kol_str_upper({obj})"
                elif m == "lower": return f"kol_str_lower({obj})"
                elif m == "trim": return f"kol_str_trim({obj})"
                elif m == "len": return f"((int64_t)kol_str_len({obj}))"
                elif m == "contains": return f"kol_str_contains({obj}, {arg_strs[0]})"
                elif m == "starts_with": return f"kol_str_starts_with({obj}, {arg_strs[0]})"
                elif m == "ends_with": return f"kol_str_ends_with({obj}, {arg_strs[0]})"
                elif m == "replace": return f"kol_str_replace({obj}, {arg_strs[0]}, {arg_strs[1]})"
                elif m == "repeat": return f"kol_str_repeat({obj}, {arg_strs[0]})"
                elif m == "slice": return f"kol_str_slice({obj}, {arg_strs[0]}, {arg_strs[1]})"

            if obj_t.startswith("list") or obj_t == "kol_array_t":
                m = expr.method_name
                arg_strs = [self._gen_expr(a) for a in expr.args]
                if m == "len":
                    return f"((int64_t){obj}.len)"
                elif m == "reverse":
                    return f"kol_array_reverse(&{obj})"
                elif m == "pop":
                    return f"kol_array_pop(&{obj})"
                elif m == "contains":
                    if obj_t == "list_str":
                        return f"kol_array_contains_str(&{obj}, {arg_strs[0]})"
                    else:
                        return f"kol_array_contains_int(&{obj}, {arg_strs[0]})"
                elif m == "push":
                    tmp_v = self._temp_var("push_elem")
                    elem_t = "int64_t"
                    if obj_t == "list_str": elem_t = "KolStr"
                    elif obj_t == "list_float": elem_t = "double"
                    elif obj_t == "list_bool": elem_t = "bool"
                    elif obj_t == "list_int": elem_t = "int64_t"
                    elif obj_t.startswith("list_"): elem_t = obj_t[5:]
                    self._emit(f"{elem_t} {tmp_v} = {arg_strs[0]};")
                    return f"kol_array_push(&{obj}, &{tmp_v})"

            if obj_t in self.struct_fields or (obj_t in self.type_impl_methods and expr.method_name in self.type_impl_methods[obj_t]):
                arg_strs = [self._gen_expr(a) for a in expr.args]
                obj_ptr = obj if (isinstance(expr.object, Ident) and expr.object.name == "self") else f"&{obj}"
                args_all = [obj_ptr] + arg_strs
                return f"_kol_method_{obj_t}_{expr.method_name}({', '.join(args_all)})"

            arg_strs = [self._gen_expr(a) for a in expr.args]
            obj_ptr = obj if (isinstance(expr.object, Ident) and expr.object.name == "self") else f"&{obj}"
            args_all = [obj_ptr] + arg_strs
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
                st_name = expr.target.name
                if st_name in self.type_constants and expr.field_name in self.type_constants[st_name]:
                    ct = self.type_constants[st_name][expr.field_name]
                    if ct == "double": return "float"
                    elif ct == "KolStr": return "str"
                    elif ct == "bool": return "bool"
                    elif ct == "int64_t": return "int"
                    return ct
                v_st_name = self.var_types.get(expr.target.name, "")
                if v_st_name in self.struct_fields:
                    for fn, ft in self.struct_fields[v_st_name]:
                        if fn == expr.field_name:
                            if ft == "KolStr": return "str"
                            elif ft == "double": return "float"
                            elif ft == "bool": return "bool"
                            return "int"
        if isinstance(expr, Ident):
            return self.var_types.get(expr.name, "int")
        if isinstance(expr, CallExpr):
            if isinstance(expr.callee, Ident):
                fn_name = expr.callee.name
                if fn_name in self.func_ret_types:
                    rt = self.func_ret_types[fn_name]
                    if rt == "KolOptStr": return "str"
                    elif rt == "KolOptInt": return "int"
                    elif rt == "KolOptFloat": return "float"
                    return rt
                if fn_name in self.generic_func_defs:
                    if expr.args:
                        return self._infer_expr_type(expr.args[0])
                if fn_name in self.struct_fields:
                    return fn_name
                if fn_name in self.enum_defs:
                    return fn_name
            elif isinstance(expr.callee, FieldAccess) and isinstance(expr.callee.target, Ident):
                mangled = f"{expr.callee.target.name}_{expr.callee.field_name}"
                if mangled in self.struct_fields or mangled in self.enum_defs:
                    return mangled
        if isinstance(expr, MapExpr):
            return "KolMap_si"
        if isinstance(expr, OrExpr):
            return self._infer_expr_type(expr.default_val)
        if isinstance(expr, MethodCallExpr):
            obj_t = self._infer_expr_type(expr.object)
            if obj_t in self.type_impl_methods and expr.method_name in self.type_impl_methods[obj_t]:
                return self.type_impl_methods[obj_t][expr.method_name]
            if obj_t == "str":
                str_method_types = {
                    "upper": "str", "lower": "str", "trim": "str",
                    "len": "int", "contains": "bool",
                    "starts_with": "bool", "ends_with": "bool",
                    "replace": "str", "repeat": "str", "split": "list_str",
                }
                if expr.method_name in str_method_types:
                    return str_method_types[expr.method_name]
            if obj_t.startswith("list") or obj_t == "kol_array_t":
                list_method_types = {
                    "len": "int", "pop": "int", "contains": "bool",
                    "push": "void", "reverse": "void"
                }
                if expr.method_name in list_method_types:
                    return list_method_types[expr.method_name]
            if expr.method_name in ["upper", "lower", "trim", "greet"]:
                return "str"
        return "int"
