# Concurrency in Kol

Kol's concurrency model is small on purpose:

| You want to...                              | Use                                   |
|---------------------------------------------|---------------------------------------|
| run a function on another thread            | `task fn` + `spawn` / `await`          |
| run a fixed set of tasks and wait for all   | `let a, b = await all(f(), g())`       |
| fan out a variable number of tasks          | a list of futures: `[future(int)]`     |
| pass values between tasks                   | `channel(T, capacity)`                 |
| wait for the first of several events        | `select ... end`                       |
| protect shared state                        | `mutex()` and `atomic(n)`              |

Tasks run on a **thread pool** (one worker per CPU core by default), so starting a task is cheap and
you can start thousands of them. Everything below works on any platform with POSIX threads.

---

## 1. Tasks, `spawn` and `await`

A task is a normal function marked `task fn`:

```kol
task fn square(n: int) -> int
    return n * n
end
```

`spawn` starts it on the pool and immediately returns a **future**. `await` waits for the result:

```kol
let f = spawn square(7)       -- returns at once, the task runs in the background
print("doing other work")
print(await f)                -- 49
print(await f)                -- awaiting again is fine: still 49
```

`await` can also be applied directly to a call. That means "call it and wait", which simply runs the
function on the current thread - there is no thread and no overhead:

```kol
let y = await square(3)       -- same as square(3) in a normal function
```

Tasks can return `int`, `float`, `bool`, `str`, lists and structs, or nothing at all:

```kol
task fn log_line(msg: str)
    print("log: {msg}")
end

let l = spawn log_line("hi")
await l                       -- wait until it has finished
```

A future has one method: `done()` returns `true` when the task has finished (it never blocks).

Awaiting a task that has **not started yet** runs it right on the awaiting thread instead of waiting for
a free worker. That is why tasks may freely spawn and await other tasks (recursive parallel `fib` works
and cannot deadlock the pool).

### `all(...)`

```kol
let a, b, c = await all(square(2), square(3), square(4))
```

All tasks start at once; the variables are filled in when all of them are done. `all` takes calls to
`task fn` functions and must be used in `let a, b = await all(...)` form.

### Dynamic fan-out

Keep futures in a list when the number of tasks is only known at run time:

```kol
mut jobs: [future(int)] = []
for i in 0..100
    jobs.push(spawn square(i))
end
mut total = 0
for job in jobs
    total = total + await job
end
```

### What happens at the end of the program

When `main` returns, the program ends - tasks that are still running are abandoned (like Go).
`await` whatever you need before returning.

---

## 2. Channels

```kol
let ch = channel(int, 16)     -- bounded queue holding up to 16 ints
let one = channel(str)        -- capacity defaults to 1
```

| Method          | Meaning                                                                      |
|-----------------|------------------------------------------------------------------------------|
| `ch.send(v)`    | Append `v`. Blocks while the channel is full. Panics if the channel is closed. |
| `ch.receive()`  | Remove and return the oldest value. Blocks while empty. On a closed, empty channel it returns the zero value (`0`, `""`, `false`). |
| `ch.close()`    | No more values will be sent. Receivers still get what is buffered. Closing twice is harmless. |
| `ch.len()`      | Values currently buffered.                                                   |
| `ch.cap()`      | Capacity.                                                                    |
| `ch.is_closed()`| Whether `close()` was called.                                                |

The idiom for consuming until the producer is done is `for v in ch`; the loop ends when the channel is
closed **and** empty:

```kol
task fn produce(out: channel(int))
    for i in 1..=100
        out.send(i)
    end
    out.close()
end

fn main()
    let ch = channel(int, 8)
    let p = spawn produce(ch)
    mut sum = 0
    for v in ch
        sum = sum + v
    end
    await p
    print(sum)                -- 5050
end
```

Channel capacity must be at least 1 (`channel(int, 0)` is a compile error).
A full channel makes the sender wait, which gives you back-pressure for free.

Channels can carry `int`, `float`, `bool`, `str` and plain structs.

---

## 3. `select`

`select` waits until one of several channel operations can proceed and runs the matching branch:

```kol
select
    case v = jobs.receive()       -- a value arrived on jobs
        print("job {v}")
    case v, ok = results.receive()  -- ok is false when results is closed and empty
        ...
    case out.send(42)             -- there was room in out
        print("sent")
    case timeout(250)             -- nothing happened for 250 ms
        print("timeout")
end
```

* Add an `else` branch instead of `timeout` to make `select` **non-blocking**: `else` runs immediately
  if no case is ready. (`else` and `timeout` cannot be combined.)
* If several cases are ready, one is chosen at random so no channel can starve the others.
* `break` and `continue` inside a branch apply to the surrounding Kol loop, as you would expect.
* A `receive` case on a closed channel is always ready (with the zero value and `ok = false`), so
  check `ok` when a channel may be closed.

---

## 4. Shared state: `mutex` and `atomic`

Tasks that touch the same variable must coordinate. Kol gives you two tools:

```kol
let counter = atomic(0)       -- lock-free integer
counter.add(1)                -- returns the new value; also sub, load, store, swap
counter.cas(10, 11)           -- compare-and-swap, returns true if it swapped

let m = mutex()               -- mutual exclusion
m.lock()
-- ... critical section ...
m.unlock()
m.try_lock()                  -- true if the lock was free and is now yours
```

Both are *handles*: passing one to a task shares the same object, so every task sees the same counter.
`m.unlock()` on a mutex that is not locked is a panic.

Prefer channels where you can; reach for `mutex`/`atomic` for counters, caches and the like.

---

## 5. What is copied and what is shared

Tasks do not share ordinary variables. When you pass arguments to `spawn`:

| Argument type                         | What the task gets                              |
|---------------------------------------|--------------------------------------------------|
| `int`, `float`, `bool`                | a copy                                           |
| `str`                                 | a shared, reference-counted string (safe)        |
| lists                                 | **an independent copy** of the list              |
| structs                               | a copy (their strings are reference counted)     |
| `channel`, `mutex`, `atomic`, `future`| the **same** object (this is how tasks talk)     |

So a task can never corrupt the caller's list, and the caller can keep using its variables, even
before the task finishes. To share mutable state deliberately, use a channel, `mutex` or `atomic`.
Global `mut` variables are *not* protected - do not write to them from several tasks.

The same rules apply to results: a task's result is copied out of its future for each `await`.

String reference counts are atomic as soon as the first task is started; programs that never use
concurrency keep the cheaper non-atomic counts.

---

## 6. Utilities

| Function       | Meaning                                                                        |
|----------------|--------------------------------------------------------------------------------|
| `sleep(ms)`    | Pause the current task for `ms` milliseconds (frees its worker meanwhile).     |
| `cpu_count()`  | Number of CPU cores - a good number of tasks for CPU-bound work.               |
| `now_ms()`     | Monotonic clock in milliseconds, for timing.                                   |
| `yield_now()`  | Give other threads a chance to run.                                            |

---

## 7. The runtime

* **Thread pool.** Workers are started on demand, up to one per core (`KOL_THREADS=n` overrides this).
* **No starvation.** When a task blocks (waiting on a channel, a future, a mutex or `sleep`), the pool
  starts a replacement worker if work is queued. A thousand tasks all blocked on one channel cannot
  stop the task that would unblock them. Surplus workers retire when idle.
* **Deadlock detection.** If every task and the main program are asleep forever, the program stops with

  ```
  Kol Panic: all tasks are asleep - deadlock!
  ```

  (exit code 2) instead of hanging. Sleeping and timed waits are never treated as deadlocks.
  Set `KOL_NO_DEADLOCK_DETECT=1` to switch the check off.
* **Single-threaded programs pay nothing.** The concurrency runtime is only compiled in when a program
  uses it.

### Panics

| Message                          | Cause                                         |
|----------------------------------|-----------------------------------------------|
| `send on closed channel`         | `send` after `close()`                        |
| `unlock of an unlocked mutex`    | `unlock()` without `lock()`                   |
| `all tasks are asleep - deadlock!` | nothing can ever wake the blocked tasks     |

---

## 8. Limits

* Channels hold at least one value (there are no zero-capacity rendezvous channels).
* A task that is `spawn`ed must return a plain value or nothing - not an optional (`T?`) or fallible (`T!`).
  (A plain `await f()` call has no such limit.)
* Generic task functions and named arguments cannot be used with `spawn`.
* `select` works on channels; futures are awaited with `await`.
* Needs POSIX threads and a C compiler with `__atomic` builtins (gcc or clang).

---

## 9. Performance

See `benchmarks/concurrency/results.md` for measurements against C (pthreads), Go and Nim. In short:
CPU-bound parallel work runs at C speed, spawning 50,000 tasks is as fast as Go, and channels move a
message in about 100-200 ns when the other side is running.

Guidelines: keep tasks reasonably coarse (microseconds, not nanoseconds, of work), use a buffered channel
when producer and consumer run at different speeds, and use `cpu_count()` to size CPU-bound fan-out.
