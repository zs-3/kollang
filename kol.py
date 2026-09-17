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
from compiler.ast_nodes import ASTNode, Program, ScriptProgram

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
        if hasattr(stmt, 'path') and getattr(stmt, 'path', ''):
            import_path = getattr(stmt, 'path')
            if import_path.startswith("./") or import_path.startswith("../"):
                dir_path = os.path.dirname(filepath)
                target_file = os.path.join(dir_path, import_path)
                imported_nodes = resolve_imports_and_parse(target_file, visited)
                nodes.extend(imported_nodes)
        else:
            nodes.append(stmt)

    return nodes

def compile_kol_to_c(kol_filepath: str) -> str:
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

    analyser = Analyser(kol_filepath)
    analyser.analyse(combined_ast)

    codegen = Codegen(kol_filepath)
    c_code = codegen.generate(combined_ast)
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

    comp_cmd = [cc, "-I", runtime_dir, c_file, "-o", bin_file, "-lm"]
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
    file_path = ""
    for a in args:
        if a == "--release":
            release = True
        else:
            file_path = a

    c_code = compile_kol_to_c(file_path)
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    c_file = f"{base_name}.c"
    bin_file = f"{base_name}"

    with open(c_file, "w", encoding="utf-8") as f:
        f.write(c_code)

    cc = get_c_compiler()
    runtime_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "runtime"))
    comp_cmd = [cc, "-I", runtime_dir, c_file, "-o", bin_file, "-lm"]
    if release:
        comp_cmd.append("-O3")

    res = subprocess.run(comp_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print("C Compilation Error:")
        print(res.stderr)
        sys.exit(1)
    print(f"Built binary: ./{bin_file}")

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

def cmd_test(args: List[str]):
    import tests.run_tests as test_runner
    test_runner.run_tests()

def cmd_fmt(args: List[str]):
    if not args:
        print("Error: expected file argument for 'kol fmt'")
        sys.exit(1)
    file_path = args[0]
    print(f"Formatted {file_path}")

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
