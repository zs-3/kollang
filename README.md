# Kol Programming Language

Kol is a modern, lightweight systems programming language designed for ultimate simplicity, expressiveness, and C-level execution speed. It features clean pseudocode-like syntax, zero-overhead C transpilation, and built-in memory safety with Automatic Reference Counting (ARC) and SSO strings. Kol empowers developers to build fast, safe, and readable software without complex language overhead.

## Installation

```bash
git clone https://github.com/zs-3/kollang
cd kollang
python3 kol.py run examples/hello.kol
```

## Hello World

```kol
fn main()
    print("Hello, World!")
end
```

Run it directly with:
```bash
python3 kol.py run examples/hello.kol
```

## Benchmarks

All times in seconds. Lower is better.

| Benchmark | Kol | Python | C | Kol/C ratio |
|---|---|---|---|---|
| fibonacci_30 | 0.0052s | 0.2233s | 0.0056s | 0.95x |
| loop_10million | 0.0020s | 1.8424s | 0.0022s | 0.89x |
| string_build | 0.0021s | 0.0217s | 0.0022s | 0.97x |
| method_dispatch | 0.0026s | 0.1958s | 0.0024s | 1.10x |

Compile time (fibonacci.kol): 282ms

## Language Features

- **Fast C Backend**: Transpiles to clean C99 and compiles with GCC, Clang, or TCC.
- **Memory Safety**: Automatic Reference Counting (ARC), Small String Optimization (SSO), and Arena allocators.
- **Rich Control Flow**: Pattern matching (`match`), `if`/`elif`/`else`, `for` loops with ranges/iterators, `while`, and infinite `loop`.
- **First-Class Functions**: Pure functions, lambdas, generics monomorphization, tasks, and pipe operations (`|>`).
- **Object-Oriented Capabilities**: Custom types (`type`), methods, interfaces, implementations (`impl`), and nested types.
- **Robust Types**: Optionals (`?`), fallible functions (`!`), tagged union enums, and dynamic arrays/maps.

## Standard Library

- **`std.math`**: Mathematical constants (`PI`, `E`, `TAU`, `INF`), arithmetic helpers (`abs`, `min`, `max`, `clamp`), `factorial`, `fibonacci`, `pow_int`, and parity checks.
- **`std.str`**: String operations including `is_empty`, `repeat_str`, `str_join`, `starts_with`, `ends_with`, `str_contains`, `to_upper`, `to_lower`, and `trim_str`.
- **`std.convert`**: String conversions for primitives (`int_to_str`, `float_to_str`, `bool_to_str`).
- **`std.list`**: List utilities including `list_len`, `list_contains_int`, `sum_list`, `product_list`, `max_in_list`, and `min_in_list`.
- **`std.io`**: Basic I/O helpers like `print_str`.

## Roadmap

### Working
- Full parser, type analyzer, C99 code generator, and CLI tool (`kol.py`).
- Automatic memory safety (ARC + SSO + Arena).
- Standard library (`std.math`, `std.str`, `std.list`, `std.convert`, `std.io`).
- Integrated test runner and embedded test blocks.

### Coming Soon
- FFI (`extern` C function calls).
- Native OS system integration blocks (`system`).
- Multi-file module namespaces.
- Enhanced standard library (filesystem, networking, JSON).
