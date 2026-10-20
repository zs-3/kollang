# Kol vs Multi-Language Comprehensive Benchmark Results

**Benchmark Date:** 2026-10-20 08:30:42
**Target Environment:** Linux x86_64 (GCC -O2, Rustc -C opt-level=3, Go 1.22+, Python 3.12+, Java 21+, Node v22+)

## Execution Time Summary (Seconds — Lower is Better)

| Benchmark | Kol | C | Rust | Go | Python | Java | Node.js | Kol vs C Ratio |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Recursive Fibonacci (N=35) | 0.0364s | 0.0373s | 0.0439s | 0.0672s | 2.1623s | 0.1058s | 0.2441s | 0.98x |
| Tight Loop Sum (100M) | 0.0020s | 0.0020s | 0.0024s | 0.0510s | 16.4948s | 0.1014s | 0.1994s | 1.04x |
| String Build (100K concat) | 0.0024s | 0.0022s | 0.0025s | 0.0031s | 0.2656s | 0.0874s | 0.0822s | 1.10x |
| Method Dispatch (10M calls) | 0.0028s | 0.0029s | 0.0034s | 0.0237s | 1.7255s | 0.0567s | 0.0757s | 0.98x |
| Dynamic Array Push & Sum (10M) | 0.0765s | 0.0741s | 0.0761s | 1.8804s | 1.6765s | 0.7470s | 0.4905s | 1.03x |
| Concurrency Channel Passing (10K msgs) | 0.0065s | 0.0052s | 0.0056s | 0.0033s | 0.0736s | 0.0802s | 0.1230s | 1.24x |

## Peak Memory Usage Summary (Peak RSS in MB — Lower is Better)

| Benchmark | Kol | C | Rust | Go | Python | Java | Node.js |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Recursive Fibonacci (N=35) | 12.00 MB | 12.00 MB | 12.00 MB | 12.00 MB | 12.00 MB | 38.60 MB | 51.35 MB |
| Tight Loop Sum (100M) | 12.12 MB | 12.00 MB | 12.00 MB | 12.00 MB | 12.12 MB | 39.27 MB | 52.36 MB |
| String Build (100K concat) | 12.00 MB | 12.12 MB | 12.00 MB | 12.00 MB | 12.00 MB | 40.15 MB | 56.10 MB |
| Method Dispatch (10M calls) | 11.88 MB | 11.88 MB | 12.00 MB | 12.00 MB | 12.00 MB | 39.35 MB | 51.36 MB |
| Dynamic Array Push & Sum (10M) | 77.88 MB | 77.75 MB | 78.25 MB | 255.10 MB | 392.25 MB | 437.24 MB | 348.72 MB |
| Concurrency Channel Passing (10K msgs) | 11.88 MB | 12.00 MB | 12.00 MB | 12.00 MB | 11.88 MB | 41.77 MB | 67.48 MB |

## Binary Size & Compile Time Comparison

| Language | Avg Compile Time (ms) | Avg Binary Size (KB) |
| --- | --- | --- |
| Kol | 378.0 ms | 17.1 KB |
| C | 83.9 ms | 15.8 KB |
| Rust | 243.0 ms | 3875.5 KB |
| Go | 86.9 ms | 2154.1 KB |
| Python | N/A (JIT/Interp) | N/A |
| Java | 724.1 ms | 0.6 KB |
| Node.js | N/A (JIT/Interp) | N/A |

## Technical Analysis & Kol Key Strengths

1. **Native C99 Backend Speed:** Kol compiles to C99 and uses optimization flags (`-O2`), achieving native speed near pure C and Rust across recursion, loops, and object/method dispatch.
2. **Low Memory Footprint (SSO & ARC):** Kol uses Small String Optimization (14-byte inline strings) and deterministic Automatic Reference Counting, maintaining low memory consumption compared to garbage-collected runtimes (Java, Go, Node.js).
3. **M:N Thread-Pool Concurrency:** Kol's `task fn`, `channel`, and thread pool run concurrent passing operations cleanly with low overhead, operating efficiently alongside Go and C pthread implementations.
