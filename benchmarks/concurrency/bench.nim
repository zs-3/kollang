import std/[os, strutils]

proc work(n, seed: int64): int64 =
  var s = seed
  var i = 0'i64
  while i < n:
    s = (s * 1103515245 + 12345) mod 2147483648'i64
    inc i
  s

type Args = tuple[a, b: int64, idx: int]
var results: array[512, int64]
var chan: Channel[int64]
var chanSum: int64

proc tramp(x: Args) {.thread.} =
  {.cast(gcsafe).}:
    results[x.idx] = work(x.a, x.b)

proc noopT(x: Args) {.thread.} =
  {.cast(gcsafe).}:
    results[0] = x.a + 1

proc prod(n: int64) {.thread.} =
  {.cast(gcsafe).}:
    for i in 0'i64 ..< n: chan.send(i)

proc cons(n: int64) {.thread.} =
  {.cast(gcsafe).}:
    var s = 0'i64
    for i in 0'i64 ..< n: s += chan.recv()
    chanSum = s

proc main() =
  let mode = paramStr(1)
  case mode
  of "spawn":
    let n = parseInt(paramStr(2))
    var s = 0'i64
    for i in 0 ..< n:
      var t: Thread[Args]
      createThread(t, noopT, (int64(i), 0'i64, 0))
      joinThread(t)
      s += results[0]
    echo s
  of "split":
    let k = parseInt(paramStr(2))
    let total = parseBiggestInt(paramStr(3))
    var ts = newSeq[Thread[Args]](k)
    for i in 0 ..< k:
      createThread(ts[i], tramp, (total div k, int64(i + 1), i))
    var s = 0'i64
    for i in 0 ..< k:
      joinThread(ts[i])
      s += results[i]
    echo s
  of "chan":
    let m = parseBiggestInt(paramStr(2))
    chan.open(1)  # capacity 1 == Kol's single-slot channel
    var tp, tc: Thread[int64]
    createThread(tp, prod, m)
    createThread(tc, cons, m)
    joinThread(tp); joinThread(tc)
    echo chanSum
main()
