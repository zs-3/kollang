#!/usr/bin/env python3
import sys
import os
import subprocess
import shutil
from typing import List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from compiler.lexer import Lexer
from compiler.parser import Parser
from compiler.codegen import Codegen
from compiler.analyser import Analyser
from compiler.errors import KolError
from compiler.ast_nodes import ASTNode, Program, ScriptProgram, UseStmt

try:
    from compiler.feature_guards import check_unimplemented_features
except ImportError:
    check_unimplemented_features = None

TARGET_COMPILERS = {
    "linux-x86_64": ("gcc", []),
    "linux-arm64": ("aarch64-linux-gnu-gcc", []),
    "macos-x86_64": ("clang", ["-arch", "x86_64"]),
    "macos-arm64": ("clang", ["-arch", "arm64"]),
}

def get_c_compiler() -> str:
    for cc in ["gcc", "clang", "tcc"]:
        if shutil.which(cc):
            return cc
    raise RuntimeError("No suitable C compiler (gcc, clang, tcc) found in PATH.")

def resolve_imports_and_parse(filepath: str, visited: set) -> List[ASTNode]:
    abs_path = os.path.abspath(filepath)
    if abs_path in visited:
        return []
    visited.add(abs_path)

    if not os.path.exists(filepath):
        if os.path.exists(filepath + ".kol"):
            filepath = filepath + ".kol"
        else:
            return []

    with open(filepath, "r", encoding="utf-8") as f:
        source = f.read()

    lexer = Lexer(filepath, source)
    tokens = lexer.tokenize()

    parser = Parser(tokens, filepath)
    ast = parser.parse()

    nodes = []
    stmts_or_decls = []
    if isinstance(ast, ScriptProgram):
        stmts_or_decls = ast.statements
    elif isinstance(ast, Program):
        stmts_or_decls = ast.declarations

    for stmt in stmts_or_decls:
        if isinstance(stmt, UseStmt) and stmt.path:
            import_path = stmt.path
            dir_path = os.path.dirname(filepath)
            stmt_loc = getattr(stmt, 'location', None)

            resolved_file = None
            tried_paths = []

            if import_path.startswith("./") or import_path.startswith("../"):
                target_file = os.path.join(dir_path, import_path)
                if os.path.exists(target_file) and os.path.isfile(target_file):
                    resolved_file = target_file
                elif os.path.exists(target_file + ".kol") and os.path.isfile(target_file + ".kol"):
                    resolved_file = target_file + ".kol"
                else:
                    tried_paths = [target_file, target_file + ".kol"]
            else:
                local_target = os.path.join(dir_path, import_path)
                if os.path.exists(local_target) and os.path.isfile(local_target):
                    resolved_file = local_target
                elif os.path.exists(local_target + ".kol") and os.path.isfile(local_target + ".kol"):
                    resolved_file = local_target + ".kol"
                else:
                    parts = import_path.split(".")
                    if len(parts) == 2 and parts[0] == "std":
                        std_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "std"))
                        std_target = os.path.join(std_dir, f"{parts[1]}.kol")
                        if os.path.exists(std_target) and os.path.isfile(std_target):
                            resolved_file = std_target
                        else:
                            tried_paths = [std_target]
                    else:
                        tried_paths = [local_target, local_target + ".kol"]

            if resolved_file:
                imported_nodes = resolve_imports_and_parse(resolved_file, visited)
                nodes.extend(imported_nodes)
            else:
                tried_str = ", ".join(tried_paths)
                raise KolError(f"Cannot resolve import '{import_path}'. Tried: {tried_str}", location=stmt_loc)
        else:
            nodes.append(stmt)

    return nodes

def compile_kol_to_c(kol_filepath: str, release_mode: bool = False) -> str:
    visited = set()
    all_nodes = resolve_imports_and_parse(kol_filepath, visited)

    has_main = False
    for node in all_nodes:
        if hasattr(node, 'name') and getattr(node, 'name', '') == 'main':
            has_main = True
            break

    if has_main:
        combined_ast = Program(declarations=all_nodes)
    else:
        combined_ast = ScriptProgram(statements=all_nodes)

    if check_unimplemented_features is not None:
        errors = check_unimplemented_features(combined_ast)
        if errors:
            source_code = None
            if os.path.exists(kol_filepath):
                try:
                    with open(kol_filepath, "r", encoding="utf-8") as f:
                        source_code = f.read()
                except Exception:
                    pass
            raise errors[0]

    analyser = Analyser(kol_filepath)
    analyser.analyse(combined_ast)

    codegen = Codegen(kol_filepath, release_mode=release_mode)
    c_code = codegen.generate(combined_ast, release_mode=release_mode)
    return c_code

def cmd_run(args: List[str]):
    if not args:
        print("Error: expected file argument for 'kol run'")
        sys.exit(1)
    file_path = args[0]
    c_code = compile_kol_to_c(file_path)

    base_name = os.path.splitext(os.path.basename(file_path))[0]
    c_file = f"/tmp/{base_name}.c"
    bin_file = f"/tmp/{base_name}"

    with open(c_file, "w", encoding="utf-8") as f:
        f.write(c_code)

    cc = get_c_compiler()
    runtime_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "runtime"))

    comp_cmd = [cc, "-I", runtime_dir, c_file, "-o", bin_file, "-lm", "-lpthread"]
    res = subprocess.run(comp_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print("C Compilation Error:")
        print(res.stderr)
        sys.exit(1)

    exec_res = subprocess.run([bin_file], capture_output=False)
    sys.exit(exec_res.returncode)

def cmd_build(args: List[str]):
    if not args:
        print("Error: expected file argument for 'kol build'")
        sys.exit(1)
    release = False
    target = None
    file_path = ""

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--release":
            release = True
        elif a == "--target":
            if i + 1 < len(args):
                target = args[i + 1]
                i += 1
            else:
                print("Error: --target requires a target name")
                sys.exit(1)
        elif a.startswith("--target="):
            target = a.split("=", 1)[1]
        else:
            file_path = a
        i += 1

    extra_flags = []
    cc = get_c_compiler()

    base_name = os.path.splitext(os.path.basename(file_path))[0]
    out_bin = f"./{base_name}"

    if target:
        if target not in TARGET_COMPILERS:
            print(f"Unknown target: {target}")
            print("Valid targets are:", ", ".join(sorted(TARGET_COMPILERS.keys())))
            sys.exit(1)

        target_cc, target_flags = TARGET_COMPILERS[target]
        if not shutil.which(target_cc):
            print(f"Cross compiler not found: {target_cc}")
            print(f"Please install {target_cc} to build for target {target}.")
            sys.exit(1)

        cc = target_cc
        extra_flags = target_flags
        out_bin = f"./{base_name}-{target}"

    c_code = compile_kol_to_c(file_path, release_mode=release)
    c_file = f"{base_name}.c"

    with open(c_file, "w", encoding="utf-8") as f:
        f.write(c_code)

    runtime_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "runtime"))
    if release:
        comp_cmd = [cc] + extra_flags + ["-O3", "-march=native", "-I", runtime_dir, "-o", out_bin, c_file, "-lm", "-lpthread"]
    else:
        comp_cmd = [cc] + extra_flags + ["-O2", "-I", runtime_dir, "-o", out_bin, c_file, "-lm", "-lpthread"]

    res = subprocess.run(comp_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print("C Compilation Error:")
        print(res.stderr)
        sys.exit(1)
    print(f"Built binary: {out_bin}")

def cmd_check(args: List[str]):
    if not args:
        print("Error: expected file argument for 'kol check'")
        sys.exit(1)
    file_path = args[0]
    try:
        compile_kol_to_c(file_path)
        print("Check OK: Syntax and type analysis valid.")
    except KolError as e:
        print(e.format())
        sys.exit(1)

def cmd_test(args: List[str]) -> None:
    if not args:
        print("Usage: kol test <file.kol>")
        return
    filepath = args[0]
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return

    with open(filepath, "r", encoding="utf-8") as f:
        source = f.read()

    tokens = Lexer(filepath, source).tokenize()
    ast = Parser(tokens, filepath).parse()
    gen = Codegen(filepath)

    c_code = gen.generate_test_runner(ast, source)

    c_file = filepath.replace(".kol", "_test.c")
    bin_file = filepath.replace(".kol", "_test")

    with open(c_file, "w", encoding="utf-8") as f:
        f.write(c_code)

    cc = get_c_compiler()
    runtime_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "runtime"))
    result = subprocess.run(
        [cc, "-O0", "-o", bin_file, c_file, "-I", runtime_dir, "-lm", "-lpthread"],
        capture_output=True, text=True)

    if result.returncode != 0:
        print("Compilation error:")
        print(result.stderr)
        if os.path.exists(c_file):
            os.remove(c_file)
        return

    run = subprocess.run([bin_file], capture_output=True, text=True)
    print(run.stdout)
    if os.path.exists(c_file):
        os.remove(c_file)
    if os.path.exists(bin_file):
        os.remove(bin_file)

def cmd_fmt(args: List[str]):
    print("kol fmt is not yet implemented")
    sys.exit(1)

def cmd_version():
    print("Kol Programming Language v0.1.0-alpha")

def main():
    if len(sys.argv) < 2:
        print("Usage: kol <command> [options] [file]")
        print("Commands: run, build, check, test, fmt, version")
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    try:
        if cmd == "run":
            cmd_run(args)
        elif cmd == "build":
            cmd_build(args)
        elif cmd == "check":
            cmd_check(args)
        elif cmd == "test":
            cmd_test(args)
        elif cmd == "fmt":
            cmd_fmt(args)
        elif cmd == "version":
            cmd_version()
        else:
            print(f"Unknown command: {cmd}")
            sys.exit(1)
    except KolError as e:
        print(e.format())
        sys.exit(1)

if __name__ == "__main__":
    main()
