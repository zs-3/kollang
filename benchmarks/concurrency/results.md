# Concurrency benchmarks: Kol vs C, Go, Nim

Machine: 2 vCPU container, gcc -O3, median of 7. Sources and raw log: this directory (`run.py`, `run.log`).

| Benchmark | Kol before | Kol after | C (pthreads) | Go |
|---|---|---|---|---|
| Real fan-out, 50k tasks | n/a | 0.038s / 8.1MB | 2.29s | 0.052s / 8.6MB |
| CPU split over 2/8/256 tasks | - | ~1.2s, ~1.7MB RSS | ~1.2s | ~1.6s |
| Channel, cap 1, 200k msgs | 4.9s | 0.132s | 7.9s | 0.039s |
| Buffered channel, cap 1024, 1M msgs | - | 0.223s | - | 0.073s |
| Spawn+join 50k sequential | 1.7s | 0.001s* | 2.48s | 0.034s |

\* `await f(x)` on a plain call is compiled to a direct call, so this row is not a real spawn; use the fan-out row for spawn cost.

Takeaways: Kol now matches C on CPU-bound splitting, beats the pthread-per-task C baseline and is on par with Go for fan-out, but Go's channel hand-off is still roughly 3x faster.
