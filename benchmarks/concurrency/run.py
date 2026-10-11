import os, sys, time, json, statistics, subprocess
RUNS = int(os.environ.get("RUNS", 7))
def run(cmd):
    p = subprocess.run(["./launch"] + cmd, capture_output=True)
    rss_kb, ut, st, wall, status = p.stderr.decode().split()
    assert int(status) == 0, (cmd, status)
    return p.stdout.decode().strip(), float(wall), float(ut) + float(st), int(rss_kb) / 1024
B = {
 "spawn":    {"kol": ["kol/spawn"],    "c": ["c/bench","spawn","50000"],   "go": ["go/bench","spawn","50000"],   "nim": ["nim/bench","spawn","50000"]},
 "split1":   {"kol": ["kol/split1"],   "c": ["c/bench","split","1","800000000"],   "go": ["go/bench","split","1","800000000"],   "nim": ["nim/bench","split","1","800000000"]},
 "split2":   {"kol": ["kol/split2"],   "c": ["c/bench","split","2","800000000"],   "go": ["go/bench","split","2","800000000"],   "nim": ["nim/bench","split","2","800000000"]},
 "split8":   {"kol": ["kol/split8"],   "c": ["c/bench","split","8","800000000"],   "go": ["go/bench","split","8","800000000"],   "nim": ["nim/bench","split","8","800000000"]},
 "split256": {"kol": ["kol/split256"], "c": ["c/bench","split","256","800000000"], "go": ["go/bench","split","256","800000000"], "nim": ["nim/bench","split","256","800000000"]},
 "chan":     {"kol": ["kol/chan"],     "c": ["c/bench","chan","200000"],   "go": ["go/bench","chan","200000"],   "go0": ["go/bench","chan0","200000"], "nim": ["nim/bench","chan","200000"]},
 "fanout":   {"kol": ["kol/fanout"],   "c": ["c/bench","fanout","50000"], "go": ["go/bench","fanout","50000"]},
 "chanbuf":  {"kol": ["kol/chanbuf"],  "go": ["go/bench","chanbuf","1000000"]},
}
res = {}
for bn, langs in B.items():
    for lang, cmd in langs.items():
        rows = [run(cmd) for _ in range(RUNS)]
        outs = {r[0] for r in rows}
        res[f"{bn}/{lang}"] = dict(out=sorted(outs), wall=statistics.median(r[1] for r in rows), wall_min=min(r[1] for r in rows),
                                   wall_max=max(r[1] for r in rows), cpu=statistics.median(r[2] for r in rows), rss=max(r[3] for r in rows))
        r = res[f"{bn}/{lang}"]
        print(f"{bn:9} {lang:4} wall={r['wall']:.3f}s [{r['wall_min']:.3f}-{r['wall_max']:.3f}] cpu={r['cpu']:.3f}s rss={r['rss']:.1f}MB out={r['out']}", flush=True)
json.dump(res, open("results.json","w"), indent=1)
