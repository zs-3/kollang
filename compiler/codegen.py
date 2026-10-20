from typing import List, Set, Dict, Optional, Tuple
import platform as _platform
import sys as _sys
from compiler.ast_nodes import *
from compiler.errors import KolError

C_KEYWORDS = {
    "double", "float", "int", "char", "long", "short",
    "void", "auto", "register", "static", "extern",
    "const", "volatile", "signed", "unsigned", "struct",
    "union", "enum", "typedef", "goto", "sizeof",
    "return", "break", "continue", "switch", "case",
    "default", "for", "while", "do", "if", "else"
}

class Codegen:
    def __init__(self, filename: str, release_mode: bool = False, target: Optional[str] = None):
        self.filename = filename
        self.release_mode = release_mode
        self.target = target
        self.code: List[str] = []
        self.global_consts: List[str] = []
        self.type_decls: List[str] = []
        self.proto_decls: List[str] = []
        self.helper_funcs: List[str] = []
        self.indent_level = 0
        self.temp_var_count = 0
        self.lambda_count = 0
        self.var_types: Dict[str, str] = {}
        self.func_ret_types: Dict[str, str] = {}
        self.fallible_fns: set = set()
        self.cur_fn_fallible: bool = False
        self.list_type_hint: Optional[str] = None
        self.struct_fields: Dict[str, List[tuple]] = {}
        self.nested_type_registry: Dict[str, str] = {}
        self.type_impl_methods: Dict[str, Dict[str, str]] = {}
        self.type_constants: Dict[str, Dict[str, str]] = {}
        self.enum_defs: Dict[str, EnumDecl] = {}
        self.generic_func_defs: Dict[str, FunctionDecl] = {}
        self.generic_instances: Set[Tuple[str, str]] = set()
        self.extern_fns: Dict[str, str] = {}
        self.extern_headers: Set[str] = set()
        self.task_param_names: Dict[str, List[str]] = {}
        self.task_decls: Dict[str, FunctionDecl] = {}
        self.task_wrappers: Set[str] = set()
        self.uses_conc: bool = False
        self.in_system_block: bool = False
        self.current_type_name: Optional[str] = None
        self.defer_stack: List[List[ASTNode]] = []
        self.scope_stack: List[List[Tuple[str, str]]] = [[]]
        self.loop_scope_depths: List[int] = []

    def _push_scope(self):
        self.scope_stack.append([])

    def _pop_and_emit_cleanup(self, skip_var: Optional[str] = None):
        if not self.scope_stack:
            return
        scope_vars = self.scope_stack.pop()
        for var_name, type_key in reversed(scope_vars):
            if skip_var and var_name == skip_var:
                continue
            self._emit_cleanup_for(var_name, type_key)

    def _emit_all_active_scope_cleanups(self, skip_var: Optional[str] = None):
        for scope_vars in reversed(self.scope_stack):
            for var_name, type_key in reversed(scope_vars):
                if skip_var and var_name == skip_var:
                    continue
                self._emit_cleanup_for(var_name, type_key)

    def _emit_loop_scope_cleanups(self):
        if not self.loop_scope_depths:
            return
        loop_start_depth = self.loop_scope_depths[-1]
        for scope_vars in reversed(self.scope_stack[loop_start_depth:]):
            for var_name, type_key in reversed(scope_vars):
                self._emit_cleanup_for(var_name, type_key)

    # ------------------------------------------------------------------
    # Type keys for the concurrency types
    #   channel(T) -> "channel_<T>"   future(T) -> "future_<T>"
    #   mutex      -> "mutex"          atomic    -> "atomic"
    #   [T]        -> "list_<T>"
    # ------------------------------------------------------------------
    HANDLE_NAMES = ("channel", "future", "mutex", "atomic")

    def _tkey(self, ta) -> str:
        if ta is None:
            return "int"
        n = ta.name
        if n in ("channel", "future"):
            inner = self._tkey(ta.generic_args[0]) if ta.generic_args else "int"
            return f"{n}_{inner}"
        if n == "list":
            return f"list_{self._tkey(ta.generic_args[0])}" if ta.generic_args else "list_int"
        return n

    def _is_handle_key(self, k: str) -> bool:
        return k.startswith("channel_") or k.startswith("future_") or k in ("mutex", "atomic")

    def _ctype_of_key(self, k: str) -> str:
        if k == "int": return "int64_t"
        if k == "float": return "double"
        if k == "bool": return "bool"
        if k == "str": return "KolStr"
        if k == "ptr": return "void*"
        if k.startswith("channel_") or k == "channel": return "KolChannel*"
        if k.startswith("future_") or k == "future": return "KolFuture*"
        if k == "mutex": return "KolMutex*"
        if k == "atomic": return "KolAtomic*"
        if k.startswith("list") or k == "kol_array_t": return "kol_array_t"
        return k

    @staticmethod
    def _nested_list_info(type_key: str):
        """Return (depth, leaf type) for nested scalar arrays, else None."""
        depth = 0
        key = type_key
        while key.startswith("list_"):
            depth += 1
            key = key[5:]
        if depth >= 2 and key in ("str", "int", "float", "bool"):
            return depth, key
        return None

    def _nested_list_call_args(self, type_key: str):
        info = self._nested_list_info(type_key)
        if not info:
            return None
        depth, leaf = info
        leaf_kind = 1 if leaf == "str" else 0
        return depth, leaf_kind

    def _emit_cleanup_for(self, var_name: str, type_key: str):
        safe_name = self._safe_c_name(var_name)
        nested = self._nested_list_call_args(type_key)
        if nested:
            depth, leaf_kind = nested
            self._emit(f"kol_array_free_deep(&{safe_name}, {depth}, {leaf_kind});")
            return
        if type_key == "str":
            self._emit(f"kol_arc_release_str({safe_name});")
        elif self._is_handle_key(type_key):
            self._emit(f"kol_obj_release({safe_name});")
        elif type_key.startswith("list_") and self._is_handle_key(type_key[5:]):
            self._emit(f"kol_array_free_objs(&{safe_name});")
        elif type_key == "list_str":
            self._emit(f"kol_array_free_strs(&{safe_name});")
        elif type_key.startswith("list") or type_key == "kol_array_t":
            self._emit(f"kol_array_free(&{safe_name});")
        elif type_key in self.struct_fields:
            str_fields = [f_name for f_name, f_type in self.struct_fields[type_key] if f_type == "KolStr"]
            for sf in str_fields:
                self._emit(f"kol_arc_release_str({safe_name}.{sf});")

    def _is_borrowed_expr(self, e: ASTNode) -> bool:
        """True when evaluating e yields a value some variable still owns."""
        return isinstance(e, (Ident, IndexExpr, FieldAccess))

    def _proto_ret_ctype(self, decl: FunctionDecl) -> str:
        rt = "void"
        if decl.return_type:
            n = decl.return_type.name
            if decl.return_type.is_fallible:
                rt = "KolResult"
            elif decl.return_type.is_optional:
                if n == "str": rt = "KolOptStr"
                elif n == "float": rt = "KolOptFloat"
                else: rt = "KolOptInt"
            elif n in self.HANDLE_NAMES:
                rt = self._ctype_of_key(self._tkey(decl.return_type))
            elif n == "str": rt = "KolStr"
            elif n == "float": rt = "double"
            elif n == "bool": rt = "bool"
            elif n == "int": rt = "int64_t"
            elif n == "list": rt = "kol_array_t"
            else: rt = n
        return rt

    def _proto_param_ctype(self, p) -> str:
        pt = "int64_t"
        if p.type_annot:
            n = p.type_annot.name
            if n in self.HANDLE_NAMES: pt = self._ctype_of_key(self._tkey(p.type_annot))
            elif n == "str": pt = "KolStr"
            elif n == "float": pt = "double"
            elif n == "bool": pt = "bool"
            elif n == "int": pt = "int64_t"
            elif n == "list": pt = "kol_array_t"
            else: pt = n
        return pt

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
                params_code.append(f"{pt} _kol_{p.name}")
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
                        elif p.type_annot.name == "list": pt = "kol_array_t"
                        else: pt = p.type_annot.name
                    params_code.append(f"{pt} _kol_{p.name}")
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

    def _host_platform(self) -> str:
        if self.target:
            if self.target.startswith("linux-"): return "linux"
            if self.target.startswith("macos-"): return "macos"
            if self.target.startswith("windows-"): return "windows"
        if _sys.platform.startswith("linux"):
            return "linux"
        if _sys.platform == "darwin":
            return "macos"
        if _sys.platform.startswith(("win32", "cygwin")):
            return "windows"
        return _sys.platform.lower()

    def _host_arch(self) -> str:
        if self.target and self.target.endswith("x86_64"): return "x86_64"
        if self.target and self.target.endswith("arm64"): return "arm64"
        machine = _platform.machine().lower()
        if machine in ("amd64", "x64", "x86_64"):
            return "x86_64"
        if machine in ("aarch64", "arm64"):
            return "arm64"
        if machine in ("i386", "i486", "i586", "i686", "x86"):
            return "x86"
        return machine

    def _eval_when_cond(self, cond: ASTNode) -> bool:
        return bool(self._eval_when_expr(cond))

    def _eval_when_expr(self, expr: ASTNode) -> Any:
        if isinstance(expr, Ident):
            if expr.name == "PLATFORM": return self._host_platform()
            if expr.name == "ARCH": return self._host_arch()
            if expr.name == "RELEASE": return self.release_mode
            if expr.name == "DEBUG": return not self.release_mode
            return expr.name
        if isinstance(expr, StrLit): return expr.value
        if isinstance(expr, BoolLit): return expr.value
        if isinstance(expr, IntLit): return expr.value
        if isinstance(expr, UnaryOp):
            if expr.op in ("not", "!"):
                return not bool(self._eval_when_expr(expr.operand))
            if expr.op == "-":
                return -self._eval_when_expr(expr.operand)
            return None
        if isinstance(expr, BinOp):
            if expr.op == "and":
                return bool(self._eval_when_expr(expr.left)) and bool(self._eval_when_expr(expr.right))
            if expr.op == "or":
                return bool(self._eval_when_expr(expr.left)) or bool(self._eval_when_expr(expr.right))
            left_v = self._eval_when_expr(expr.left)
            right_v = self._eval_when_expr(expr.right)
            if expr.op == "==": return left_v == right_v
            if expr.op == "!=": return left_v != right_v
            return None
        return None

    def _eval_when_branch(self, stmt: WhenStmt) -> List[ASTNode]:
        cond_val = self._eval_when_cond(stmt.condition)
        if cond_val:
            return stmt.then_branch
        elif stmt.else_branch:
            return stmt.else_branch
        return []

    def _collect_declarations(self, nodes: List[ASTNode]):
        for decl in nodes:
            if isinstance(decl, VarDecl) and decl.is_const:
                self._gen_var_decl(decl, global_scope=True)
            elif isinstance(decl, ExternDecl):
                self.extern_fns[decl.name] = decl.name
                if decl.header:
                    self.extern_headers.add(decl.header)
                if decl.return_type:
                    rt_name = decl.return_type.name
                    if rt_name == "float": self.func_ret_types[decl.name] = "float"
                    elif rt_name == "str": self.func_ret_types[decl.name] = "str"
                    elif rt_name == "bool": self.func_ret_types[decl.name] = "bool"
                    else: self.func_ret_types[decl.name] = rt_name
                else:
                    self.func_ret_types[decl.name] = "void"
            elif isinstance(decl, FunctionDecl):
                self.task_param_names[decl.name] = [p.name for p in decl.params]
                self.task_decls[decl.name] = decl
                if decl.generic_params:
                    self.generic_func_defs[decl.name] = decl
                else:
                    if decl.return_type:
                        rt_name = decl.return_type.name
                        if decl.return_type.is_fallible:
                            self.fallible_fns.add(decl.name)
                        if decl.return_type.is_optional:
                            if rt_name == "str": self.func_ret_types[decl.name] = "KolOptStr"
                            elif rt_name == "float": self.func_ret_types[decl.name] = "KolOptFloat"
                            else: self.func_ret_types[decl.name] = "KolOptInt"
                        elif rt_name in self.HANDLE_NAMES or rt_name == "list":
                            self.func_ret_types[decl.name] = self._tkey(decl.return_type)
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
            elif isinstance(decl, WhenStmt):
                branch = self._eval_when_branch(decl)
                if branch:
                    self._collect_declarations(branch)

    def generate(self, node: ASTNode, is_test_mode: bool = False, release_mode: bool = False) -> str:
        if release_mode:
            self.release_mode = True
        self.is_test_mode = is_test_mode
        self.uses_conc = False
        self.task_wrappers = set()
        self.code = []
        self.global_consts = []
        self.type_decls = []
        self.proto_decls = []
        self.helper_funcs = []

        all_nodes = []
        if isinstance(node, Program):
            all_nodes = node.declarations
        elif isinstance(node, ScriptProgram):
            all_nodes = node.statements

        self.extern_fns = {}
        self.extern_headers = set()

        # Pre-pass: collect consts, types, enums, functions
        self._collect_declarations(all_nodes)

        # Forward declarations for functions and methods
        for decl in all_nodes:
            if isinstance(decl, FunctionDecl) and not decl.generic_params and decl.name != "main":
                rt = self._proto_ret_ctype(decl)
                params_code = [f"{self._proto_param_ctype(p)} {p.name}" for p in decl.params]
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
                self._emit("int main(int argc, char** argv) {")
                self.indent_level += 1
                self._emit("kol_runtime_set_args(argc, argv);")
                self.indent_level -= 1
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
                self._emit("int main(int argc, char** argv) {")
                self.indent_level += 1
                self._emit("kol_runtime_set_args(argc, argv);")
                self.indent_level -= 1
                self._emit("    return 0;")
                self._emit("}")

        extern_inc = "".join(f"#include <{h if h.endswith('.h') else h + '.h'}>\n" for h in sorted(self.extern_headers))
        if extern_inc:
            extern_inc += "\n"
        conc_def = "#define KOL_USE_CONCURRENCY 1\n" if self.uses_conc else ""
        header_inc = conc_def + '#include "kol_runtime.h"\n' + extern_inc
        const_section = "\n".join(self.global_consts) + "\n\n" if self.global_consts else ""
        type_section = "\n".join(self.type_decls) + "\n\n" if self.type_decls else ""
        proto_section = "\n".join(self.proto_decls) + "\n\n" if self.proto_decls else ""
        helper_section = "\n".join(self.helper_funcs) + "\n\n" if self.helper_funcs else ""

        full_code = header_inc + const_section + type_section + proto_section + helper_section + "\n".join(body_code)
        return full_code

    def generate_test_runner(self, program: ASTNode, source: str) -> str:
        """Generate C code that runs test blocks."""
        self.code = []
        self.global_consts = []
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

        # Pre-pass: collect consts, types, enums, functions
        for decl in all_nodes:
            if isinstance(decl, VarDecl) and decl.is_const:
                self._gen_var_decl(decl, global_scope=True)
            elif isinstance(decl, FunctionDecl):
                self.task_param_names[decl.name] = [p.name for p in decl.params]
                self.task_decls[decl.name] = decl
                if decl.generic_params:
                    self.generic_func_defs[decl.name] = decl
                else:
                    if decl.return_type:
                        rt_name = decl.return_type.name
                        if decl.return_type.is_fallible:
                            self.fallible_fns.add(decl.name)
                        if decl.return_type.is_optional:
                            if rt_name == "str": self.func_ret_types[decl.name] = "KolOptStr"
                            elif rt_name == "float": self.func_ret_types[decl.name] = "KolOptFloat"
                            else: self.func_ret_types[decl.name] = "KolOptInt"
                        elif rt_name in self.HANDLE_NAMES or rt_name == "list":
                            self.func_ret_types[decl.name] = self._tkey(decl.return_type)
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
                rt = self._proto_ret_ctype(decl)
                params_code = [f"{self._proto_param_ctype(p)} {p.name}" for p in decl.params]
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
        self._emit('int main(int argc, char** argv) {')
        self.indent_level += 1
        self._emit('kol_runtime_set_args(argc, argv);')
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

        conc_def = "#define KOL_USE_CONCURRENCY 1\n" if self.uses_conc else ""
        header_inc = conc_def + '#include <stdio.h>\n#include <stdbool.h>\n#include "kol_runtime.h"\n\nint _kol_test_failures = 0;\n\n'
        const_section = "\n".join(self.global_consts) + "\n\n" if self.global_consts else ""
        type_section = "\n".join(self.type_decls) + "\n\n" if self.type_decls else ""
        proto_section = "\n".join(self.proto_decls) + "\n\n" if self.proto_decls else ""
        helper_section = "\n".join(self.helper_funcs) + "\n\n" if self.helper_funcs else ""

        full_code = header_inc + const_section + type_section + proto_section + helper_section + "\n".join(body_code)
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
            if (isinstance(stmt.target, Ident) and stmt.op == "="
                    and self.var_types.get(stmt.target.name, "").startswith("list")
                    and self._is_local_var(stmt.target.name)):
                # reassigning an array: build the new value, free the old one, move in
                tkey = self.var_types[stmt.target.name]
                tname_c = self._safe_c_name(stmt.target.name)
                if isinstance(stmt.value, ListExpr) and not stmt.value.elements:
                    self.list_type_hint = tkey
                new_c = self._gen_expr(stmt.value)
                self.list_type_hint = None
                if self._is_borrowed(stmt.value):
                    new_c = self._array_clone_expr(new_c, tkey)
                tmp_arr = self._temp_var("assign_arr")
                self._emit(f"kol_array_t {tmp_arr} = {new_c};")
                self._emit(self._array_free_stmt(tname_c, tkey))
                self._emit(f"{tname_c} = {tmp_arr};")
                return
            if (isinstance(stmt.target, IndexExpr) and stmt.op == "="
                    and self._infer_expr_type(stmt.target.target) == "list_str"):
                arr_c = self._gen_expr(stmt.target.target)
                idx_c = self._gen_expr(stmt.target.index)
                new_c = self._gen_expr(stmt.value)
                tmp_s = self._temp_var("set_str")
                self._emit(f"KolStr {tmp_s} = {new_c};")
                if self._is_borrowed(stmt.value):
                    self._emit(f"kol_arc_retain_str({tmp_s});")
                line_no = stmt.location.line if stmt.location else 0
                self._emit(f'kol_array_set_str(&{arr_c}, (size_t)({idx_c}), {tmp_s}, "{self.filename}", {line_no});')
                return
            target = self._gen_expr(stmt.target)
            val = self._gen_expr(stmt.value)
            if isinstance(stmt.target, Ident) and self.var_types.get(stmt.target.name) == "str":
                target_safe = self._safe_c_name(stmt.target.name)
                tmp_val = self._temp_var("assign_str")
                self._emit(f"KolStr {tmp_val} = {val};")
                if isinstance(stmt.value, Ident):
                    self._emit(f"kol_arc_retain_str({tmp_val});")
                self._emit(f"kol_arc_release_str({target_safe});")
                self._emit(f"{target} {stmt.op} {tmp_val};")
            else:
                self._emit(f"{target} {stmt.op} {val};")
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
            self._push_scope()
            for s in stmt.then_branch:
                self._gen_statement(s, current_fn_fallible, current_fn_optional, opt_ret_kind)
            self._pop_and_emit_cleanup()
            self.indent_level -= 1

            for elif_cond, elif_body in stmt.elif_branches:
                e_cond = self._gen_expr(elif_cond)
                self._emit(f"}} else if ({e_cond}) {{")
                self.indent_level += 1
                self._push_scope()
                for s in elif_body:
                    self._gen_statement(s, current_fn_fallible, current_fn_optional, opt_ret_kind)
                self._pop_and_emit_cleanup()
                self.indent_level -= 1

            if stmt.else_branch is not None:
                self._emit("} else {")
                self.indent_level += 1
                self._push_scope()
                for s in stmt.else_branch:
                    self._gen_statement(s, current_fn_fallible, current_fn_optional, opt_ret_kind)
                self._pop_and_emit_cleanup()
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
                self.loop_scope_depths.append(len(self.scope_stack))
                self._push_scope()
                for s in stmt.body:
                    self._gen_statement(s, current_fn_fallible, current_fn_optional, opt_ret_kind)
                self._pop_and_emit_cleanup()
                self.loop_scope_depths.pop()
                self.indent_level -= 1
                self._emit("}")
            elif self._infer_expr_type(stmt.iterable).startswith("channel"):
                self.uses_conc = True
                ch_key = self._infer_expr_type(stmt.iterable)
                elem_key = ch_key[8:] if len(ch_key) > 8 else "int"
                elem_ct = self._ctype_of_key(elem_key)
                ch_v = self._temp_var("forch")
                self._emit(f"KolChannel* {ch_v} = {self._gen_expr(stmt.iterable)};")
                counter = self._temp_var("n")
                self._emit(f"int64_t {counter} = 0;")
                tmp = self._temp_var("item")
                self._emit("for (;;) {")
                self.indent_level += 1
                self._emit(f"{elem_ct} {tmp};")
                self._emit(f"if (!kol_channel_recv({ch_v}, &{tmp})) break;")
                self.loop_scope_depths.append(len(self.scope_stack))
                self._push_scope()
                if stmt.index_var:
                    self.var_types[stmt.index_var] = "int"
                    self._emit(f"int64_t {self._safe_c_name(stmt.index_var)} = {counter};")
                self._emit(f"{counter}++;")
                self.var_types[stmt.var_name] = elem_key
                self._emit(f"{elem_ct} {self._safe_c_name(stmt.var_name)} = {tmp};")
                if elem_key == "str":
                    self.scope_stack[-1].append((stmt.var_name, "str"))
                for s_ in stmt.body:
                    self._gen_statement(s_, current_fn_fallible, current_fn_optional, opt_ret_kind)
                self._pop_and_emit_cleanup()
                self.loop_scope_depths.pop()
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
                elif list_type == "list_int":
                    elem_c_type = "int64_t"
                    elem_type_key = "int"
                elif list_type.startswith("list_"):
                    elem_type_key = list_type[5:]
                    elem_c_type = self._ctype_of_key(elem_type_key)
                else:
                    elem_c_type = "int64_t"
                    elem_type_key = "int"

                self.var_types[elem_v] = elem_type_key
                elem_safe = self._safe_c_name(elem_v)

                self._emit(f"for (size_t {idx_c} = 0; {idx_c} < {arr_c}.len; {idx_c}++) {{")
                self.indent_level += 1
                self.loop_scope_depths.append(len(self.scope_stack))
                self._push_scope()
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
                self._pop_and_emit_cleanup()
                self.loop_scope_depths.pop()
                self.indent_level -= 1
                self._emit("}")

        elif isinstance(stmt, SelectStmt):
            self._gen_select(stmt, current_fn_fallible, current_fn_optional, opt_ret_kind)

        elif isinstance(stmt, WhileStmt):
            cond = self._gen_expr(stmt.condition)
            self._emit(f"while ({cond}) {{")
            self.indent_level += 1
            self.loop_scope_depths.append(len(self.scope_stack))
            self._push_scope()
            for s in stmt.body:
                self._gen_statement(s, current_fn_fallible)
            self._pop_and_emit_cleanup()
            self.loop_scope_depths.pop()
            self.indent_level -= 1
            self._emit("}")

        elif isinstance(stmt, LoopStmt):
            self._emit("while (1) {")
            self.indent_level += 1
            self.loop_scope_depths.append(len(self.scope_stack))
            self._push_scope()
            for s in stmt.body:
                self._gen_statement(s, current_fn_fallible)
            self._pop_and_emit_cleanup()
            self.loop_scope_depths.pop()
            self.indent_level -= 1
            self._emit("}")

        elif isinstance(stmt, ReturnStmt):
            self._emit_defers()
            if (stmt.value and current_fn_fallible and isinstance(stmt.value, CallExpr)
                    and isinstance(stmt.value.callee, Ident)
                    and stmt.value.callee.name in self.fallible_fns):
                # `return other_fallible(...)` forwards the whole result
                fwd = self._gen_expr(stmt.value)
                self._emit_all_active_scope_cleanups()
                self._emit(f"return {fwd};")
            elif stmt.value:
                val = self._gen_expr(stmt.value)
                ret_type_c = "int64_t"
                t_inf = self._infer_expr_type(stmt.value)
                if t_inf == "str": ret_type_c = "KolStr"
                elif t_inf == "float": ret_type_c = "double"
                elif t_inf == "bool": ret_type_c = "bool"
                elif t_inf.startswith("list") or t_inf == "kol_array_t": ret_type_c = "kol_array_t"
                elif self._is_handle_key(t_inf): ret_type_c = self._ctype_of_key(t_inf)
                elif t_inf == "ptr": ret_type_c = "void*"
                elif t_inf in self.struct_fields or t_inf in self.enum_defs: ret_type_c = t_inf

                ret_var = stmt.value.name if isinstance(stmt.value, Ident) else None
                if t_inf.startswith("list") and self._is_borrowed(stmt.value):
                    is_local = isinstance(stmt.value, Ident) and self._is_local_var(stmt.value.name)
                    if not is_local:
                        # the caller owns parameters / elements: return our own copy
                        val = self._array_clone_expr(val, t_inf)
                self._emit(f"{ret_type_c} _kol_ret_val = {val};")
                if self._is_borrowed(stmt.value):
                    is_local_ret = isinstance(stmt.value, Ident) and self._is_local_var(stmt.value.name)
                    if not is_local_ret:
                        # Parameters, fields and elements stay owned by the caller/container:
                        # the returned value needs its own reference (the caller releases it).
                        if t_inf == "str":
                            self._emit("kol_arc_retain_str(_kol_ret_val);")
                        elif self._is_handle_key(t_inf):
                            self._emit("kol_obj_retain(_kol_ret_val);")
                self._emit_all_active_scope_cleanups(skip_var=ret_var)
                if current_fn_fallible:
                    if t_inf.startswith("list"): self._emit("return kol_result_ok_arr(_kol_ret_val);")
                    elif t_inf == "str": self._emit("return kol_result_ok_str(_kol_ret_val);")
                    elif t_inf == "float": self._emit("return kol_result_ok_float(_kol_ret_val);")
                    elif t_inf == "bool": self._emit("return kol_result_ok_bool(_kol_ret_val);")
                    else: self._emit("return kol_result_ok_int(_kol_ret_val);")
                elif current_fn_optional:
                    if isinstance(stmt.value, NoneLit):
                        if opt_ret_kind == "str": self._emit("return kol_opt_str_none();")
                        elif opt_ret_kind == "float": self._emit("return kol_opt_float_none();")
                        else: self._emit("return kol_opt_int_none();")
                    else:
                        if opt_ret_kind == "str": self._emit("return kol_opt_str_some(_kol_ret_val);")
                        elif opt_ret_kind == "float": self._emit("return kol_opt_float_some(_kol_ret_val);")
                        else: self._emit("return kol_opt_int_some(_kol_ret_val);")
                else:
                    self._emit("return _kol_ret_val;")
            else:
                self._emit_all_active_scope_cleanups()
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
            self._emit_loop_scope_cleanups()
            self._emit("break;")

        elif isinstance(stmt, ContinueStmt):
            self._emit_loop_scope_cleanups()
            self._emit("continue;")

        elif isinstance(stmt, FailStmt):
            msg = self._gen_expr(stmt.message)
            self._emit_defers()
            self._emit(f"return kol_result_err({msg});")

        elif isinstance(stmt, FunctionDecl):
            if not stmt.generic_params:
                self._gen_function_decl(stmt)

        elif isinstance(stmt, MultiAssignStmt):
            all_expr = None
            if isinstance(stmt.value, AwaitExpr) and isinstance(stmt.value.task_expr, AllExpr):
                all_expr = stmt.value.task_expr
            elif isinstance(stmt.value, AllExpr):
                all_expr = stmt.value

            if not all_expr:
                raise KolError("multi-variable let requires 'all(...)' on the right-hand side", stmt.location)

            tasks = all_expr.tasks
            if len(stmt.names) != len(tasks):
                raise KolError(f"multi-variable let has {len(stmt.names)} variables but {len(tasks)} tasks in all(...)", stmt.location)

            # Start every task on the pool, then collect the results in order.
            spawned = []
            for task_node in tasks:
                call_node = task_node.task_expr if isinstance(task_node, AwaitExpr) else task_node
                if not isinstance(call_node, CallExpr) or not isinstance(call_node.callee, Ident):
                    raise KolError("all(...) arguments must be task function calls", task_node.location)
                fn_name = call_node.callee.name
                ret_key = self.func_ret_types.get(fn_name, "void")
                spawned.append((self._gen_spawn(SpawnExpr(call=call_node, location=task_node.location)), ret_key))

            for (fut, ret_key), var_name in zip(spawned, stmt.names):
                val = self._emit_future_value(fut, ret_key, True)
                self.var_types[var_name] = ret_key
                safe_var = self._safe_c_name(var_name)
                if ret_key == "void":
                    self._emit(f"int64_t {safe_var} = 0;")
                else:
                    self._emit(f"{self._ctype_of_key(ret_key)} {safe_var} = {val};")
                    if self.scope_stack:
                        self.scope_stack[-1].append((var_name, ret_key))

        elif isinstance(stmt, ExternDecl):
            pass

        elif isinstance(stmt, WhenStmt):
            branch = self._eval_when_branch(stmt)
            for s in branch:
                self._gen_statement(s, current_fn_fallible, current_fn_optional, opt_ret_kind, is_return)

        elif isinstance(stmt, SystemBlock):
            old_sys = self.in_system_block
            self.in_system_block = True
            for s in stmt.body:
                self._gen_statement(s, current_fn_fallible, current_fn_optional, opt_ret_kind, is_return)
            self.in_system_block = old_sys

        else:
            expr = self._gen_expr(stmt)
            if expr:
                self._emit(f"{expr};")

    def _gen_var_decl(self, decl: VarDecl, global_scope: bool = False):
        if decl.is_const:
            c_type = "int64_t"
            type_key = "int"
            if decl.type_annot:
                tname = decl.type_annot.name
                type_key = tname
                if tname == "float": c_type = "double"
                elif tname == "str": c_type = "KolStr"
                elif tname == "bool": c_type = "bool"
                elif tname == "int": c_type = "int64_t"
                else: c_type = tname
            elif decl.value:
                if isinstance(decl.value, FloatLit):
                    c_type = "double"
                    type_key = "float"
                elif isinstance(decl.value, IntLit):
                    c_type = "int64_t"
                    type_key = "int"
                elif isinstance(decl.value, (StrLit, StrInterp)):
                    c_type = "KolStr"
                    type_key = "str"
                elif isinstance(decl.value, BoolLit):
                    c_type = "bool"
                    type_key = "bool"
                else:
                    t = self._infer_expr_type(decl.value)
                    type_key = t
                    if t == "float": c_type = "double"
                    elif t == "str": c_type = "KolStr"
                    elif t == "bool": c_type = "bool"
                    else: c_type = "int64_t"

            self.var_types[decl.name] = type_key
            safe_name = self._safe_c_name(decl.name)
            val_c = self._gen_expr(decl.value) if decl.value else "0"
            if c_type == "KolStr":
                const_line = f"#define {safe_name} ({val_c})"
            else:
                const_line = f"static const {c_type} {safe_name} = {val_c};"
            if const_line not in self.global_consts:
                self.global_consts.append(const_line)
            return

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
            if tname == "ptr": type_str = "void*"
            elif tname in self.HANDLE_NAMES:
                type_key = self._tkey(decl.type_annot)
                type_str = self._ctype_of_key(type_key)
            elif tname == "int": type_str = "int64_t"
            elif tname == "float": type_str = "double"
            elif tname == "bool": type_str = "bool"
            elif tname == "str": type_str = "KolStr"
            elif tname == "list":
                type_str = "kol_array_t"
                type_key = self._tkey(decl.type_annot)
            else: type_str = tname
        elif decl.value:
            t_inf = self._infer_expr_type(decl.value)
            type_key = t_inf
            if t_inf.startswith("list"):
                type_str = "kol_array_t"
            elif t_inf == "str":
                type_str = "KolStr"
            elif t_inf == "float":
                type_str = "double"
            elif t_inf == "bool":
                type_str = "bool"
            elif t_inf == "ptr":
                type_str = "void*"
            elif self._is_handle_key(t_inf):
                type_str = self._ctype_of_key(t_inf)
            elif t_inf not in ("int", "void") and t_inf in self.struct_fields:
                type_str = t_inf          # ← ADD THIS LINE    

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
                elif type_key == "ptr": type_str = "void*"
                elif type_key == "int": type_str = "int64_t"
                elif type_key == "list" or type_key.startswith("list"): type_str = "kol_array_t"
                else: type_str = self._ctype_of_key(type_key)
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
                    elif type_key.startswith("list"): type_str = "kol_array_t"
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
            elif isinstance(decl.value, AwaitExpr) and isinstance(decl.value.task_expr, CallExpr) and isinstance(decl.value.task_expr.callee, Ident):
                fn_name = decl.value.task_expr.callee.name
                if fn_name in self.func_ret_types:
                    type_key = self.func_ret_types[fn_name]
                    if type_key == "str": type_str = "KolStr"
                    elif type_key == "float": type_str = "double"
                    elif type_key == "bool": type_str = "bool"
                    elif type_key == "int": type_str = "int64_t"
                    elif type_key.startswith("list"): type_str = "kol_array_t"
                    else: type_str = type_key
            elif isinstance(decl.value, ChannelExpr):
                type_str = "KolChannel*"
                type_key = f"channel_{self._tkey(decl.value.elem_type)}"
            elif isinstance(decl.value, ListExpr):
                type_str = "kol_array_t"
                # An explicit annotation is authoritative, especially for empty
                # arrays and nested arrays where inference has no element value.
                if not decl.type_annot:
                    if decl.value.elements:
                        first_t = self._infer_expr_type(decl.value.elements[0])
                        if first_t == "str": type_key = "list_str"
                        elif first_t == "float": type_key = "list_float"
                        elif first_t == "bool": type_key = "list_bool"
                        elif first_t.startswith("list_"): type_key = f"list_{first_t}"
                        elif first_t in self.struct_fields or first_t in self.enum_defs: type_key = f"list_{first_t}"
                        else: type_key = "list_int"
                    else:
                        type_key = "list_int"

        self.var_types[decl.name] = type_key
        if self.scope_stack and not global_scope:
            self.scope_stack[-1].append((decl.name, type_key))
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
                if isinstance(decl.value, ListExpr) and type_key.startswith("list"):
                    self.list_type_hint = type_key
                val_str = self._gen_expr(decl.value)
                self.list_type_hint = None
                if type_key.startswith("list") and self._is_borrowed(decl.value):
                    # value semantics: `let b = a` gets its own copy of the array
                    val_str = self._array_clone_expr(val_str, type_key)
                self._emit(f"{type_str} {safe_decl_name} = {val_str};")
                if type_key == "str" and isinstance(decl.value, Ident):
                    self._emit(f"kol_arc_retain_str({safe_decl_name});")
                elif self._is_handle_key(type_key) and self._is_borrowed_expr(decl.value):
                    self._emit(f"kol_obj_retain({safe_decl_name});")
        elif type_str == "kol_array_t":
            self._emit(f"{type_str} {safe_decl_name} = {{0}};")
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
            elif rt in self.HANDLE_NAMES: ret_type = self._ctype_of_key(self._tkey(decl.return_type))
            elif rt == "int": ret_type = "int64_t"
            elif rt == "float": ret_type = "double"
            elif rt == "bool": ret_type = "bool"
            elif rt == "str": ret_type = "KolStr"
            elif rt == "list": ret_type = "kol_array_t"
            else: ret_type = rt

        c_fn_name = "main" if is_main else f"_kol_fn_{decl.name}"
        saved_fn_fallible = self.cur_fn_fallible
        self.cur_fn_fallible = is_fallible

        params_code = []
        for p in decl.params:
            pt = "int64_t"
            pk = "int"
            if p.type_annot:
                pn = p.type_annot.name
                pk = pn
                if pn in self.HANDLE_NAMES:
                    pk = self._tkey(p.type_annot)
                    pt = self._ctype_of_key(pk)
                elif pn == "int": pt = "int64_t"
                elif pn == "float": pt = "double"
                elif pn == "bool": pt = "bool"
                elif pn == "str": pt = "KolStr"
                elif pn == "list":
                    pt = "kol_array_t"
                    pk = self._tkey(p.type_annot)
                elif pn in self.struct_fields or pn in self.enum_defs: pt = pn
            self.var_types[p.name] = pk
            p_safe = self._safe_c_name(p.name)
            params_code.append(f"{pt} {p_safe}")

        param_str = ", ".join(params_code) if params_code else "void"
        # The C entry point receives argc/argv even when the KOL main function
        # declares no parameters; std.args exposes a safe KOL-level view.
        if is_main and not decl.params:
            param_str = "int argc, char** argv"

        inline_prefix = "static inline " if (decl.is_pure and not is_main) else ""
        self._emit(f"{inline_prefix}{ret_type} {c_fn_name}({param_str}) {{")
        self.indent_level += 1
        self.defer_stack.append([])
        self._push_scope()
        if is_main and not decl.params:
            self._emit("kol_runtime_set_args(argc, argv);")

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
                        if rt == "str": self._emit(f"return kol_result_ok_str({val});")
                        elif rt == "float": self._emit(f"return kol_result_ok_float({val});")
                        elif rt == "bool": self._emit(f"return kol_result_ok_bool({val});")
                        else: self._emit(f"return kol_result_ok_int({val});")
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
        self._pop_and_emit_cleanup()
        if is_main:
            self._emit("return 0;")
        self.defer_stack.pop()
        self.cur_fn_fallible = saved_fn_fallible

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
                else: pt = pn    
            self.var_types[p.name] = pk
            self.var_types[f"_kol_{p.name}"] = pk
            params_code.append(f"{pt} _kol_{p.name}")

        param_str = ", ".join(params_code)

        self._emit(f"{ret_type} {c_fn_name}({param_str}) {{")
        self.indent_level += 1
        self.defer_stack.append([])
        self._push_scope()

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
        self._pop_and_emit_cleanup()
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

    # ------------------------------------------------------------------
    # Tasks, futures, select
    # ------------------------------------------------------------------
    def _acquire_lines(self, ref: str, key: str, borrowed: bool) -> List[str]:
        """Make a copy of a value that is safe to hand to another task.

        Values that some variable still owns (borrowed) are retained or copied.
        Fresh temporaries are *moved* into the task, which releases them when done."""
        if not borrowed:
            return []
        if key == "str":
            return [f"kol_arc_retain_str({ref});"]
        if self._is_handle_key(key):
            return [f"kol_obj_retain({ref});"]
        nested = self._nested_list_call_args(key)
        if nested:
            depth, leaf_kind = nested
            return [f"{ref} = kol_array_clone_deep(&{ref}, {depth}, {leaf_kind});"]
        if key.startswith("list_") and self._is_handle_key(key[5:]):
            return [f"{ref} = kol_array_clone(&{ref});", f"kol_array_retain_objs(&{ref});"]
        if key == "list_str":
            return [f"{ref} = kol_array_clone_strs(&{ref});"]
        if key.startswith("list") or key == "kol_array_t":
            return [f"{ref} = kol_array_clone(&{ref});"]
        if key in self.struct_fields:
            return [f"kol_arc_retain_str({ref}.{f});" for f, t in self.struct_fields[key] if t == "KolStr"]
        return []

    def _release_lines(self, ref: str, key: str) -> List[str]:
        if key == "str":
            return [f"kol_arc_release_str({ref});"]
        if self._is_handle_key(key):
            return [f"kol_obj_release({ref});"]
        nested = self._nested_list_call_args(key)
        if nested:
            depth, leaf_kind = nested
            return [f"kol_array_free_deep(&{ref}, {depth}, {leaf_kind});"]
        if key.startswith("list_") and self._is_handle_key(key[5:]):
            return [f"kol_array_free_objs(&{ref});"]
        if key == "list_str":
            return [f"kol_array_free_strs(&{ref});"]
        if key.startswith("list") or key == "kol_array_t":
            return [f"kol_array_free(&{ref});"]
        if key in self.struct_fields:
            return [f"kol_arc_release_str({ref}.{f});" for f, t in self.struct_fields[key] if t == "KolStr"]
        return []

    def _ensure_task_wrapper(self, name: str):
        """Emit the argument struct, run function and result destructor used to spawn `name`."""
        if name in self.task_wrappers:
            return
        self.task_wrappers.add(name)
        decl = self.task_decls[name]
        if decl.generic_params:
            raise KolError(f"generic task function '{name}' cannot be spawned", decl.location)
        if decl.return_type and (decl.return_type.is_fallible or decl.return_type.is_optional):
            raise KolError(f"task '{name}' returns an optional/fallible value; spawn needs a plain return type", decl.location)

        keys = [self._tkey(p.type_annot) if p.type_annot else "int" for p in decl.params]
        fields = [f"    {self._ctype_of_key(k)} a{i};" for i, k in enumerate(keys)] or ["    char _unused;"]
        self.type_decls.append("typedef struct {\n" + "\n".join(fields) + f"\n}} _KolTaskArgs_{name};")

        ret_key = self.func_ret_types.get(name, "void")
        ret_ct = None if ret_key == "void" else self._ctype_of_key(ret_key)
        call = f"_kol_fn_{name}({', '.join(f'a->a{i}' for i in range(len(keys)))})"

        lines = [f"static void _kol_run_{name}(void* raw, void* res) {{",
                 f"    _KolTaskArgs_{name}* a = (_KolTaskArgs_{name}*)raw;",
                 "    (void)res; (void)a;"]
        lines.append(f"    *({ret_ct}*)res = {call};" if ret_ct else f"    {call};")
        for i, k in enumerate(keys):
            lines += ["    " + l for l in self._release_lines(f"a->a{i}", k)]
        lines.append("}")
        self.helper_funcs.append("\n".join(lines))

        if ret_ct:
            rel = self._release_lines(f"(*({ret_ct}*)r)", ret_key)
            if rel:
                self.helper_funcs.append(f"static void _kol_dtor_{name}(void* r) {{\n    " + "\n    ".join(rel) + "\n}")

    def _result_dtor(self, name: str, ret_key: str) -> str:
        if ret_key == "void":
            return "NULL"
        ret_ct = self._ctype_of_key(ret_key)
        return f"_kol_dtor_{name}" if self._release_lines(f"(*({ret_ct}*)r)", ret_key) else "NULL"

    def _gen_spawn(self, expr: "SpawnExpr") -> str:
        call = expr.call
        if (not isinstance(call, CallExpr) or not isinstance(call.callee, Ident)
                or call.callee.name not in self.task_decls or not self.task_decls[call.callee.name].is_task):
            raise KolError("spawn needs a call to a 'task fn', e.g. 'spawn work(5)'", expr.location)
        name = call.callee.name
        decl = self.task_decls[name]
        if call.named_args:
            raise KolError("named arguments are not supported with spawn", expr.location)
        if len(call.args) > len(decl.params):
            raise KolError(f"task '{name}' takes {len(decl.params)} argument(s) but {len(call.args)} were given", expr.location)

        self.uses_conc = True
        self._ensure_task_wrapper(name)
        keys = [self._tkey(p.type_annot) if p.type_annot else "int" for p in decl.params]
        ret_key = self.func_ret_types.get(name, "void")
        res_size = "0" if ret_key == "void" else f"sizeof({self._ctype_of_key(ret_key)})"

        # Evaluate the arguments first: they may emit statements of their own.
        arg_vals = []
        for i, p in enumerate(decl.params):
            if i < len(call.args):
                arg_vals.append((self._gen_expr(call.args[i]), self._is_borrowed_expr(call.args[i])))
            elif p.default_val is not None:
                arg_vals.append((self._gen_expr(p.default_val), False))
            else:
                raise KolError(f"task '{name}' is missing argument '{p.name}'", expr.location)

        f = self._temp_var("fut")
        self._emit(f"KolFuture* {f} = kol_future_new(_kol_run_{name}, sizeof(_KolTaskArgs_{name}), {res_size}, {self._result_dtor(name, ret_key)});")
        self._emit(f"_KolTaskArgs_{name}* {f}_a = (_KolTaskArgs_{name}*){f}->args;")
        for i, ((val, borrowed), k) in enumerate(zip(arg_vals, keys)):
            self._emit(f"{f}_a->a{i} = {val};")
            for line in self._acquire_lines(f"{f}_a->a{i}", k, borrowed):
                self._emit(line)
        self._emit(f"kol_future_submit({f});")
        return f

    def _emit_future_value(self, fut: str, key: str, release: bool) -> str:
        """Wait for a future and return a C expression holding its (independently owned) value."""
        if key == "void":
            self._emit(f"kol_future_wait({fut});")
            if release:
                self._emit(f"kol_obj_release({fut});")
            return "0"
        ct = self._ctype_of_key(key)
        tmp = self._temp_var("aw")
        self._emit(f"{ct} {tmp} = *({ct}*)kol_future_wait({fut});")
        for line in self._acquire_lines(tmp, key, True):
            self._emit(line)
        if release:
            self._emit(f"kol_obj_release({fut});")
        return tmp

    def _gen_await(self, expr: AwaitExpr) -> str:
        t = expr.task_expr
        # `await f(x)`: the caller waits for the result right away, so the call simply
        # runs on the calling thread - no thread, no queue, no overhead.
        if isinstance(t, CallExpr) and isinstance(t.callee, Ident):
            name = t.callee.name
            callee = name if name in self.extern_fns else f"_kol_fn_{name}"
            return f"{callee}({', '.join(self._gen_expr(a) for a in t.args)})"
        key = self._infer_expr_type(t)
        if key.startswith("future_"):
            self.uses_conc = True
            fut = self._gen_expr(t)
            return self._emit_future_value(fut, key[len("future_"):], isinstance(t, SpawnExpr))
        return self._gen_expr(t)

    def _gen_builtin_call(self, expr: CallExpr) -> Optional[str]:
        """sleep(ms), now_ms(), cpu_count(), yield_now(), mutex(), atomic(n)"""
        if not isinstance(expr.callee, Ident):
            return None
        name = expr.callee.name
        if (name in self.func_ret_types or name in self.generic_func_defs or name in self.struct_fields
                or name in self.extern_fns or self.var_types.get(name, "").startswith("lambda")):
            return None
        if name == "sleep":
            self.uses_conc = True
            return f"kol_sleep_ms({self._gen_expr(expr.args[0]) if expr.args else '0'})"
        if name == "now_ms":
            self.uses_conc = True
            return "kol_now_ms()"
        if name == "cpu_count":
            self.uses_conc = True
            return "kol_cpu_count()"
        if name == "yield_now":
            self.uses_conc = True
            return "kol_yield()"
        if name == "mutex":
            self.uses_conc = True
            return "kol_mutex_create()"
        if name == "atomic":
            self.uses_conc = True
            return f"kol_atomic_create({self._gen_expr(expr.args[0]) if expr.args else '0'})"
        return None

    def _gen_handle_method(self, expr: MethodCallExpr, obj: str, obj_t: str) -> Optional[str]:
        """Methods of channels, futures, mutexes and atomics. Returns None if obj_t is none of them."""
        m = expr.method_name
        args = expr.args

        def need(n: int):
            if len(args) != n:
                raise KolError(f"{m}() takes {n} argument(s) but {len(args)} were given", expr.location)

        if obj_t.startswith("channel"):
            self.uses_conc = True
            elem_key = obj_t[8:] if len(obj_t) > 8 else "int"
            elem_c = self._ctype_of_key(elem_key)
            if m == "send":
                need(1)
                val_c = self._gen_expr(args[0])
                tmp_v = self._temp_var("send")
                self._emit(f"{elem_c} {tmp_v} = {val_c};")
                if elem_key == "str" and self._is_borrowed_expr(args[0]):
                    self._emit(f"kol_arc_retain_str({tmp_v});")
                self._emit(f"kol_channel_send({obj}, &{tmp_v});")
                return ""
            if m == "receive":
                need(0)
                tmp_v = self._temp_var("recv")
                self._emit(f"{elem_c} {tmp_v};")
                self._emit(f"kol_channel_recv({obj}, &{tmp_v});")
                return tmp_v
            if m == "close":
                need(0)
                self._emit(f"kol_channel_close({obj});")
                return ""
            if m == "len":
                need(0)
                return f"kol_channel_len({obj})"
            if m == "cap":
                need(0)
                return f"kol_channel_cap({obj})"
            if m == "is_closed":
                need(0)
                return f"kol_channel_is_closed({obj})"
            raise KolError(f"channels have no method '{m}' (send, receive, close, len, cap, is_closed)", expr.location)

        if obj_t == "mutex":
            self.uses_conc = True
            need(0)
            if m == "lock": return f"kol_mutex_lock({obj})"
            if m == "unlock": return f"kol_mutex_unlock({obj})"
            if m == "try_lock": return f"kol_mutex_try_lock({obj})"
            raise KolError(f"mutex has no method '{m}' (lock, unlock, try_lock)", expr.location)

        if obj_t == "atomic":
            self.uses_conc = True
            vals = [self._gen_expr(a) for a in args]
            table = {"load": (0, "kol_atomic_load"), "store": (1, "kol_atomic_store"), "add": (1, "kol_atomic_add"),
                     "sub": (1, "kol_atomic_sub"), "swap": (1, "kol_atomic_swap"), "cas": (2, "kol_atomic_cas")}
            if m not in table:
                raise KolError(f"atomic has no method '{m}' (load, store, add, sub, swap, cas)", expr.location)
            need(table[m][0])
            return f"{table[m][1]}({', '.join([obj] + vals)})"

        if obj_t.startswith("future_"):
            self.uses_conc = True
            if m == "done":
                need(0)
                return f"kol_future_done({obj})"
            raise KolError(f"futures have no method '{m}' (use 'await' to get the value, done() to poll)", expr.location)

        return None

    def _gen_select(self, stmt: "SelectStmt", cf, co, ok):
        self.uses_conc = True
        sid = self._temp_var("sel")
        arms = stmt.arms
        self._emit("{")
        self.indent_level += 1

        comm = []   # (arm index, ctype, elem_key)
        default_idx = timeout_idx = -1
        for idx, arm in enumerate(arms):
            if arm.kind in ("receive", "send"):
                key = self._infer_expr_type(arm.channel)
                if not key.startswith("channel"):
                    raise KolError("select cases must operate on a channel", arm.location)
                elem_key = key[8:] if len(key) > 8 else "int"
                ct = self._ctype_of_key(elem_key)
                self._emit(f"KolChannel* {sid}_ch{idx} = {self._gen_expr(arm.channel)};")
                if arm.kind == "receive":
                    self._emit(f"{ct} {sid}_v{idx};")
                    self._emit(f"int {sid}_ok{idx} = 0;")
                else:
                    self._emit(f"{ct} {sid}_v{idx} = {self._gen_expr(arm.value)};")
                    if elem_key == "str" and self._is_borrowed_expr(arm.value):
                        self._emit(f"kol_arc_retain_str({sid}_v{idx});")
                comm.append((idx, ct, elem_key))
            elif arm.kind == "timeout":
                timeout_idx = idx
                self._emit(f"int64_t {sid}_dl = kol_now_ms() + (int64_t)({self._gen_expr(arm.value)});")
            else:
                default_idx = idx

        self._emit(f"int {sid} = -1;")
        self._emit("kol_select_enter();")
        self._emit("for (;;) {")
        self.indent_level += 1
        self._emit(f"uint64_t {sid}_seen = kol_select_epoch();")
        if comm:
            n = len(comm)
            self._emit(f"uint32_t {sid}_start = kol_select_rand({n});")
            self._emit(f"for (uint32_t {sid}_k = 0; {sid}_k < {n}; {sid}_k++) {{")
            self.indent_level += 1
            self._emit(f"switch (({sid}_start + {sid}_k) % {n}) {{")
            for j, (idx, ct, elem_key) in enumerate(comm):
                if arms[idx].kind == "receive":
                    self._emit(f"case {j}: {{ int {sid}_r = kol_channel_try_recv({sid}_ch{idx}, &{sid}_v{idx}); "
                               f"if ({sid}_r != 0) {{ {sid} = {idx}; {sid}_ok{idx} = ({sid}_r > 0); }} break; }}")
                else:
                    self._emit(f"case {j}: {{ if (kol_channel_try_send({sid}_ch{idx}, &{sid}_v{idx})) {sid} = {idx}; break; }}")
            self._emit("}")
            self._emit(f"if ({sid} >= 0) break;")
            self.indent_level -= 1
            self._emit("}")
            self._emit(f"if ({sid} >= 0) break;")
        if default_idx >= 0:
            self._emit(f"{sid} = {default_idx}; break;")
        elif timeout_idx >= 0:
            self._emit(f"if (!kol_select_wait({sid}_seen, {sid}_dl)) {{ {sid} = {timeout_idx}; break; }}")
        else:
            self._emit(f"kol_select_wait({sid}_seen, -1);")
        self.indent_level -= 1
        self._emit("}")
        self._emit("kol_select_leave();")

        # A value that was prepared for a send case but not sent still holds a reference.
        for idx, ct, elem_key in comm:
            if arms[idx].kind == "send" and elem_key == "str":
                self._emit(f"if ({sid} != {idx}) kol_arc_release_str({sid}_v{idx});")

        # Bodies are an if-chain (not a switch) so 'break'/'continue' still reach the enclosing Kol loop.
        for idx, arm in enumerate(arms):
            self._emit(("if" if idx == 0 else "} else if") + f" ({sid} == {idx}) {{")
            self.indent_level += 1
            self._push_scope()
            if arm.kind == "receive" and arm.names:
                ct, elem_key = next((c, k) for i, c, k in comm if i == idx)
                v = arm.names[0]
                self.var_types[v] = elem_key
                self._emit(f"{ct} {self._safe_c_name(v)} = {sid}_v{idx};")
                if elem_key == "str":
                    self.scope_stack[-1].append((v, "str"))
                if len(arm.names) > 1:
                    self.var_types[arm.names[1]] = "bool"
                    self._emit(f"bool {self._safe_c_name(arm.names[1])} = {sid}_ok{idx};")
            elif arm.kind == "receive":
                ct, elem_key = next((c, k) for i, c, k in comm if i == idx)
                if elem_key == "str":
                    self._emit(f"kol_arc_release_str({sid}_v{idx});")
            for s_ in arm.body:
                self._gen_statement(s_, cf, co, ok)
            self._pop_and_emit_cleanup()
            self.indent_level -= 1
        self._emit("}")
        self.indent_level -= 1
        self._emit("}")

    def _gen_expr(self, expr: ASTNode) -> str:
        if isinstance(expr, IntLit):
            return str(expr.value)

        if isinstance(expr, FloatLit):
            return str(expr.value)

        if isinstance(expr, StrLit):
            return f'kol_str_create("{self._c_escape(expr.value)}")'

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
                    fmt_parts.append(self._c_escape(part).replace('%', '%%'))
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
            is_str_cmp = (l_type == "str" or r_type == "str")
            op = expr.op
            if l_type == "str" and r_type == "str" and op == "+":
                tmp_l = self._temp_var("str_l")
                tmp_r = self._temp_var("str_r")
                res_v = self._temp_var("str_res")
                # Operands that name existing storage (variables, elements, fields)
                # are borrowed: retain them so the release below nets to zero.
                # Everything else is a fresh temporary we own and must release.
                borrowed = (Ident, IndexExpr, FieldAccess)
                self._emit(f"KolStr {tmp_l} = {left};")
                if isinstance(expr.left, borrowed):
                    self._emit(f"kol_arc_retain_str({tmp_l});")
                self._emit(f"KolStr {tmp_r} = {right};")
                if isinstance(expr.right, borrowed):
                    self._emit(f"kol_arc_retain_str({tmp_r});")
                self._emit(f"KolStr {res_v} = kol_str_concat({tmp_l}, {tmp_r});")
                # concat appends in place when it can, returning the left operand's buffer:
                # releasing the left operand then would free the result.
                self._emit(f"if (!({res_v}.is_heap && {tmp_l}.is_heap && {res_v}.heap.ptr == {tmp_l}.heap.ptr)) kol_arc_release_str({tmp_l});")
                self._emit(f"kol_arc_release_str({tmp_r});")
                return res_v
            if is_str_cmp and op == "==":
                return f"kol_str_eq({left}, {right})"
            if is_str_cmp and op == "!=":
                return f"kol_str_neq({left}, {right})"
            if l_type == "str" and r_type == "str" and op in ("<", ">", "<=", ">="):
                return f"(kol_str_cmp({left}, {right}) {op} 0)"
            if op == "and": op = "&&"
            elif op == "or": op = "||"
            elif op == "mod": op = "%"
            return f"({left} {op} {right})"

        if isinstance(expr, AwaitExpr):
            return self._gen_await(expr)

        if isinstance(expr, SpawnExpr):
            return self._gen_spawn(expr)

        if isinstance(expr, UnaryOp):
            operand = self._gen_expr(expr.operand)
            op = expr.op
            if op == "not": op = "!"
            return f"({op}{operand})"

        if isinstance(expr, TryExpr):
            inner = expr.expr
            if not self.cur_fn_fallible:
                raise KolError("'try' / '?' can only be used inside a function that returns 'T!'", expr.location)
            inner_c = self._gen_expr(inner)
            res_var = self._temp_var("try")
            self._emit(f"KolResult {res_var} = {inner_c};")
            self._emit(f"if (!{res_var}.ok) {{")
            self.indent_level += 1
            self._emit_defers()
            self._emit(f"return {res_var};")
            self.indent_level -= 1
            self._emit("}")
            return self._result_unwrap(res_var, inner)

        if isinstance(expr, OrExpr) and self._is_logical_or(expr):
            # `a or b` on plain booleans is logical OR (short-circuit)
            return f"({self._gen_expr(expr.expr)} || {self._gen_expr(expr.default_val)})"

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
                val_field = "val_int"
                if isinstance(expr.expr, CallExpr) and isinstance(expr.expr.callee, Ident):
                    fn_n = expr.expr.callee.name
                    ret_t = self.func_ret_types.get(fn_n, "")
                    if ret_t == "float": val_field = "val_float"
                    elif ret_t == "str": val_field = "val_str"
                    elif ret_t == "bool": val_field = "val_bool"
                elif self._infer_expr_type(expr.default_val) == "float":
                    val_field = "val_float"
                elif self._infer_expr_type(expr.default_val) == "str":
                    val_field = "val_str"
                elif self._infer_expr_type(expr.default_val) == "bool":
                    val_field = "val_bool"
                if self._infer_expr_type(expr.expr).startswith("list"):
                    return f"({res_var}.ok ? kol_result_take_arr({res_var}) : {d_str})"
                return f"({res_var}.ok ? {res_var}.{val_field} : {d_str})"

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
            elem_c_t = "int64_t"
            if target_t == "list_str": elem_c_t = "KolStr"
            elif target_t == "list_float": elem_c_t = "double"
            elif target_t == "list_bool": elem_c_t = "bool"
            elif target_t.startswith("list_"): elem_c_t = self._ctype_of_key(target_t[5:])
            if self.release_mode:
                return f"((({elem_c_t}* restrict){target}.data)[{idx}])"
            return f"(*(({elem_c_t}*)kol_array_get(&{target}, {idx}, \"{self.filename}\", {expr.location.line if expr.location else 0})))"

        if isinstance(expr, ListExpr):
            arr_var = self._temp_var("arr")
            elem_type_c = "int64_t"
            elem_size = "sizeof(int64_t)"
            hint = self.list_type_hint
            self.list_type_hint = None
            if not expr.elements and hint:
                if hint == "list_str": elem_type_c, elem_size = "KolStr", "sizeof(KolStr)"
                elif hint == "list_float": elem_type_c, elem_size = "double", "sizeof(double)"
                elif hint == "list_bool": elem_type_c, elem_size = "bool", "sizeof(bool)"
                elif hint.startswith("list_list_"):
                    elem_type_c, elem_size = "kol_array_t", "sizeof(kol_array_t)"
                elif hint.startswith("list_") and (hint[5:] in self.struct_fields or hint[5:] in self.enum_defs):
                    elem_type_c, elem_size = hint[5:], f"sizeof({hint[5:]})"
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
                elif first_t.startswith("list_"):
                    elem_type_c = "kol_array_t"
                    elem_size = "sizeof(kol_array_t)"
                elif first_t in self.struct_fields or first_t in self.enum_defs:
                    elem_type_c = first_t
                    elem_size = f"sizeof({first_t})"

            self._emit(f"kol_array_t {arr_var} = kol_array_create({elem_size}, {len(expr.elements)});")
            for elem in expr.elements:
                if elem_type_c == "kol_array_t" and hint and hint.startswith("list_list_"):
                    self.list_type_hint = hint[5:]
                elem_c = self._gen_expr(elem)
                self.list_type_hint = None
                tmp_elem = self._temp_var("elem")
                self._emit(f"{elem_type_c} {tmp_elem} = {elem_c};")
                if elem_type_c == "KolStr" and self._is_borrowed(elem):
                    self._emit(f"kol_arc_retain_str({tmp_elem});")
                elif elem_type_c == "kol_array_t" and self._is_borrowed(elem):
                    self._emit(f"{tmp_elem} = {self._array_clone_expr(tmp_elem, self._infer_expr_type(elem))};")
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

            builtin = self._gen_builtin_call(expr)
            if builtin is not None:
                return builtin

            if isinstance(expr.callee, FieldAccess) and isinstance(expr.callee.target, Ident) and expr.callee.target.name == "mem":
                m_name = expr.callee.field_name
                arg_strs = [self._gen_expr(a) for a in expr.args]
                args_joined = ", ".join(arg_strs)
                if m_name == "alloc": return f"kol_mem_alloc({args_joined})"
                elif m_name == "free": return f"kol_mem_free({args_joined})"
                elif m_name == "set": return f"kol_mem_set({args_joined})"
                elif m_name == "realloc": return f"kol_mem_realloc({args_joined})"
                elif m_name == "copy": return f"kol_mem_copy({args_joined})"

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

                if fn_name in self.extern_fns:
                    callee_name = fn_name
                else:
                    callee_name = f"_kol_fn_{fn_name}"

            else:
                callee_name = self._gen_expr(expr.callee)

            arg_strs = [self._gen_expr(a) for a in expr.args]
            return f"{callee_name}({', '.join(arg_strs)})"

        if isinstance(expr, ChannelExpr):
            self.uses_conc = True
            key = self._tkey(expr.elem_type)
            ct = self._ctype_of_key(key)
            if expr.capacity is None:
                cap = "1"
            else:
                if isinstance(expr.capacity, IntLit) and expr.capacity.value < 1:
                    raise KolError("channel capacity must be at least 1", expr.location)
                cap = self._gen_expr(expr.capacity)
            kind = "KOL_ELEM_STR" if key == "str" else "KOL_ELEM_PLAIN"
            return f"kol_channel_create(sizeof({ct}), {cap}, {kind})"

        if isinstance(expr, MethodCallExpr):
            if self.in_system_block and isinstance(expr.object, Ident) and expr.object.name == "mem":
                m_name = expr.method_name
                arg_strs = [self._gen_expr(a) for a in expr.args]
                args_joined = ", ".join(arg_strs)
                if m_name == "alloc": return f"kol_mem_alloc({args_joined})"
                elif m_name == "free": return f"kol_mem_free({args_joined})"
                elif m_name == "set": return f"kol_mem_set({args_joined})"
                elif m_name == "realloc": return f"kol_mem_realloc({args_joined})"
                elif m_name == "copy": return f"kol_mem_copy({args_joined})"

            obj = self._gen_expr(expr.object)
            obj_t = self._infer_expr_type(expr.object)

            handled = self._gen_handle_method(expr, obj, obj_t)
            if handled is not None:
                return handled

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
                elif m == "split": return f"kol_str_split({obj}, {arg_strs[0]})"
                elif m == "index_of": return f"kol_str_find({obj}, {arg_strs[0]}, 0)"
                elif m == "last_index_of": return f"kol_str_rfind({obj}, {arg_strs[0]})"

            if obj_t.startswith("list") or obj_t == "kol_array_t":
                m = expr.method_name
                arg_strs = [self._gen_expr(a) for a in expr.args]
                if m == "len":
                    return f"((int64_t){obj}.len)"
                elif m == "reverse":
                    return f"kol_array_reverse(&{obj})"
                elif m == "pop":
                    if obj_t == "list_str": return f"kol_array_pop_str(&{obj})"
                    if obj_t == "list_float": return f"kol_array_pop_float(&{obj})"
                    if obj_t == "list_bool": return f"kol_array_pop_bool(&{obj})"
                    if obj_t == "list_int" or obj_t == "list": return f"kol_array_pop_int(&{obj})"
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
                    elif obj_t.startswith("list_"): elem_t = self._ctype_of_key(obj_t[5:])
                    self._emit(f"{elem_t} {tmp_v} = {arg_strs[0]};")
                    if obj_t == "list_str" and self._is_borrowed(expr.args[0]):
                        self._emit(f"kol_arc_retain_str({tmp_v});")
                    elif obj_t.startswith("list_list_") and self._is_borrowed(expr.args[0]):
                        self._emit(f"{tmp_v} = {self._array_clone_expr(tmp_v, obj_t[5:])};")
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

    @staticmethod
    def _c_escape(text: str) -> str:
        """Escape text for use inside a C string literal."""
        out = []
        for ch in text:
            if ch == '\\': out.append('\\\\')
            elif ch == '"': out.append('\\"')
            elif ch == '\n': out.append('\\n')
            elif ch == '\r': out.append('\\r')
            elif ch == '\t': out.append('\\t')
            elif ch == '?': out.append('\\?')      # avoid trigraphs
            elif ord(ch) < 32 or ord(ch) == 127: out.append('\\%03o' % ord(ch))
            else: out.append(ch)
        return ''.join(out)

    BORROWED_EXPRS = (Ident, IndexExpr, FieldAccess)

    def _is_borrowed(self, expr) -> bool:
        """True when `expr` names storage that something else still owns."""
        return isinstance(expr, self.BORROWED_EXPRS)

    def _is_local_var(self, name: str) -> bool:
        return any(n == name for scope in self.scope_stack for (n, _t) in scope)

    def _array_clone_expr(self, arr_c: str, type_key: str) -> str:
        nested = self._nested_list_call_args(type_key)
        if nested:
            depth, leaf_kind = nested
            return f"kol_array_clone_deep(&{arr_c}, {depth}, {leaf_kind})"
        if type_key.startswith("list_") and self._is_handle_key(type_key[5:]):
            return f"kol_array_clone_objs(&{arr_c})"
        if type_key == "list_str":
            return f"kol_array_clone_strs(&{arr_c})"
        return f"kol_array_clone(&{arr_c})"

    def _array_free_stmt(self, name_c: str, type_key: str) -> str:
        return " ".join(self._release_lines(name_c, type_key))

    def _result_unwrap(self, res_var: str, call_expr) -> str:
        """C expression that extracts the success value of a KolResult."""
        t = self._infer_expr_type(call_expr)
        if t.startswith("list"): return f"kol_result_take_arr({res_var})"
        if t == "float": return f"{res_var}.val_float"
        if t == "str": return f"{res_var}.val_str"
        if t == "bool": return f"{res_var}.val_bool"
        return f"{res_var}.val_int"

    def _is_logical_or(self, expr) -> bool:
        """`x or y` is a logical OR when x is a plain bool value.  It stays the
        optional/fallible default operator when x is a call that can fail or be
        none (or anything that is not a bool)."""
        left = expr.expr
        if isinstance(left, CallExpr) and isinstance(left.callee, Ident):
            fn = left.callee.name
            if fn in self.fallible_fns:
                return False
            ret = self.func_ret_types.get(fn, "")
            if ret.startswith("KolOpt") or ret.endswith("?"):
                return False
        return self._infer_expr_type(left) == "bool"

    def _infer_expr_type(self, expr: ASTNode) -> str:
        if isinstance(expr, ListExpr):
            if not expr.elements:
                return "list_int"
            first_t = self._infer_expr_type(expr.elements[0])
            if first_t == "str": return "list_str"
            if first_t == "float": return "list_float"
            if first_t == "bool": return "list_bool"
            if first_t.startswith("list_"): return f"list_{first_t}"
            if first_t in self.struct_fields or first_t in self.enum_defs: return f"list_{first_t}"
            return "list_int"
        if isinstance(expr, IndexExpr):
            target_t = self._infer_expr_type(expr.target)
            if target_t == "list_str": return "str"
            elif target_t == "list_float": return "float"
            elif target_t == "list_bool": return "bool"
            elif target_t.startswith("list_"): return target_t[5:]
            return "int"
        if isinstance(expr, ChannelExpr):
            return f"channel_{self._tkey(expr.elem_type)}"
        if isinstance(expr, SpawnExpr):
            c = expr.call
            rk = self.func_ret_types.get(c.callee.name, "void") if isinstance(c, CallExpr) and isinstance(c.callee, Ident) else "void"
            return f"future_{rk}"
        if isinstance(expr, AwaitExpr):
            k = self._infer_expr_type(expr.task_expr)
            if k.startswith("future_"):
                return k[len("future_"):]
            return k
        if isinstance(expr, StrLit) or isinstance(expr, StrInterp):
            return "str"
        if isinstance(expr, FloatLit):
            return "float"
        if isinstance(expr, BoolLit):
            return "bool"
        if isinstance(expr, IntLit):
            return "int"
        if isinstance(expr, BinOp):
            if expr.op in ("==", "!=", "<", ">", "<=", ">=", "and", "or"):
                return "bool"
            if expr.op in ("+", "-", "*", "/", "mod"):
                lt = self._infer_expr_type(expr.left)
                rt = self._infer_expr_type(expr.right)
                if lt == "str" or rt == "str":
                    return "str"
                if lt == "float" or rt == "float":
                    return "float"
                return "int"
        if isinstance(expr, UnaryOp):
            if expr.op == "not":
                return "bool"
            return self._infer_expr_type(expr.operand)
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
            if isinstance(expr.callee, FieldAccess) and isinstance(expr.callee.target, Ident) and expr.callee.target.name == "mem":
                if expr.callee.field_name in ("alloc", "realloc"): return "ptr"
                return "void"
            if isinstance(expr.callee, Ident):
                fn_name = expr.callee.name
                if (fn_name not in self.func_ret_types and fn_name not in self.generic_func_defs
                        and fn_name not in self.struct_fields and fn_name not in self.extern_fns):
                    if fn_name == "mutex": return "mutex"
                    if fn_name == "atomic": return "atomic"
                    if fn_name in ("cpu_count", "now_ms"): return "int"
                    if fn_name in ("sleep", "yield_now"): return "void"
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
        if isinstance(expr, TryExpr):
            return self._infer_expr_type(expr.expr)
        if isinstance(expr, OrExpr):
            inner = expr.expr
            if isinstance(inner, CallExpr) and isinstance(inner.callee, Ident) and inner.callee.name in self.func_ret_types:
                return self._infer_expr_type(inner)
            return self._infer_expr_type(expr.default_val)
        if isinstance(expr, MethodCallExpr):
            if self.in_system_block and isinstance(expr.object, Ident) and expr.object.name == "mem":
                if expr.method_name in ("alloc", "realloc"): return "ptr"
                return "void"
            obj_t = self._infer_expr_type(expr.object)
            if obj_t in self.type_impl_methods and expr.method_name in self.type_impl_methods[obj_t]:
                return self.type_impl_methods[obj_t][expr.method_name]
            if obj_t == "str":
                str_method_types = {
                    "upper": "str", "lower": "str", "trim": "str",
                    "len": "int", "contains": "bool",
                    "starts_with": "bool", "ends_with": "bool",
                    "replace": "str", "repeat": "str", "slice": "str", "split": "list_str",
                }
                if expr.method_name in str_method_types:
                    return str_method_types[expr.method_name]
            if obj_t.startswith("channel"):
                m = expr.method_name
                if m == "receive":
                    elem_key = obj_t[8:] if len(obj_t) > 8 else "int"
                    return elem_key
                if m in ("len", "cap"): return "int"
                if m == "is_closed": return "bool"
                return "void"
            if obj_t == "mutex":
                return "bool" if expr.method_name == "try_lock" else "void"
            if obj_t == "atomic":
                m = expr.method_name
                if m == "cas": return "bool"
                if m == "store": return "void"
                return "int"
            if obj_t.startswith("future_"):
                return "bool" if expr.method_name == "done" else "void"
            if obj_t.startswith("list") or obj_t == "kol_array_t":
                list_method_types = {
                    "len": "int", "pop": "int", "contains": "bool",
                    "push": "void", "reverse": "void"
                }
                if expr.method_name == "pop" and obj_t.startswith("list_"):
                    return obj_t[5:] if obj_t[5:] in ("str", "float", "bool") else "int"
                if expr.method_name in list_method_types:
                    return list_method_types[expr.method_name]
            if expr.method_name in ["upper", "lower", "trim", "greet"]:
                return "str"
        return "int"
