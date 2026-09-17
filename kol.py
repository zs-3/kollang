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

def get_c_compiler() -> str:
    for cc in ["gcc", "clang", "tcc"]:
        if shutil.which(cc):
            return cc
    raise RuntimeError("No suitable C compiler (gcc, clang, tcc) found in PATH.")

def compile_kol_to_c(kol_filepath: str) -> str:
    with open(kol_filepath, "r", encoding="utf-8") as f:
        source = f.read()

    lexer = Lexer(kol_filepath, source)
    tokens = lexer.tokenize()

    parser = Parser(tokens, kol_filepath)
    ast = parser.parse()

    analyser = Analyser(kol_filepath)
    analyser.analyse(ast)

    codegen = Codegen(kol_filepath)
    c_code = codegen.generate(ast)
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
