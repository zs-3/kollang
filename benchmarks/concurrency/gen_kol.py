import sys
mode = sys.argv[1]
HEAD_WORK = """task fn work(n: int, seed: int) -> int
    mut s = seed
    mut i = 0
    while i < n
        s = (s * 1103515245 + 12345) % 2147483648
        i = i + 1
    end
    return s
end
"""
if mode == "spawn":
    n = int(sys.argv[2])
    print(f"""task fn noop(x: int) -> int
    return x + 1
end

fn main()
    mut s = 0
    for i in 0..{n}
        let r = await noop(i)
        s = s + r
    end
    print(s)
end""")
elif mode == "split":
    k, total = int(sys.argv[2]), int(sys.argv[3])
    print(HEAD_WORK)
    names = ", ".join(f"r{i}" for i in range(k))
    if k == 1:
        print(f"fn main()\n    let r0 = await work({total}, 1)\n    print(r0)\nend")
    else:
        calls = ", ".join(f"work({total//k}, {i+1})" for i in range(k))
        total_expr = " + ".join(f"r{i}" for i in range(k))
        print(f"fn main()\n    let {names} = await all({calls})\n    print({total_expr})\nend")
elif mode == "chan":
    m = int(sys.argv[2])
    print(f"""task fn producer(ch: channel(int), n: int) -> int
    for i in 0..n
        ch.send(i)
    end
    return 0
end

task fn consumer(ch: channel(int), n: int) -> int
    mut s = 0
    for i in 0..n
        let v = ch.receive()
        s = s + v
    end
    return s
end

fn main()
    let ch = channel(int)
    let p, c = await all(producer(ch, {m}), consumer(ch, {m}))
    print(c)
end""")

if mode == "fanout":
    n = int(sys.argv[2])
    print(f"""task fn inc(x: int) -> int
    return x + 1
end

fn main()
    mut fs: [future(int)] = []
    for i in 0..{n}
        fs.push(spawn inc(i))
    end
    mut s = 0
    for f in fs
        s = s + await f
    end
    print(s)
end""")
elif mode == "chanbuf":
    m = int(sys.argv[2])
    print(f"""task fn producer(ch: channel(int), n: int) -> int
    for i in 0..n
        ch.send(i)
    end
    return 0
end

task fn consumer(ch: channel(int), n: int) -> int
    mut s = 0
    for i in 0..n
        let v = ch.receive()
        s = s + v
    end
    return s
end

fn main()
    let ch = channel(int, 1024)
    let p, c = await all(producer(ch, {m}), consumer(ch, {m}))
    print(c)
end""")
