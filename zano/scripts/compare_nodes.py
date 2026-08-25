#!/usr/bin/env python3
"""Сравнение цепей нескольких нод: сходятся ли они и где расходятся.

Пункт 3.0 (контроль: должны сойтись) и 3.3 (расхождение по расписанию
хардфорков: должны разойтись в известной точке).

Читает zanod.log каждой ноды, строит отображение высота -> хеш блока и находит
первую высоту, где хеши не совпадают. Заодно считает альтернативные блоки
(признак реорганизаций) и отказы по версии блока — то, чем расхождение по
хардфорку отличается от поломки стенда.

Пример:
    python3 compare_nodes.py /tmp/zpos/n1/zanod.log /tmp/zpos/n2/zanod.log
"""
import re, sys

BLOCK = re.compile(r"BLOCK SUCCESSFULLY ADDED \[(PoS|PoW)\]")
IDLINE = re.compile(r"^id:\s+([0-9a-f]{64})")
HEIGHT = re.compile(r"HEIGHT (\d+), difficulty: (\d+)")

def read(path):
    chain, pending, alt, verrej = {}, None, 0, 0
    for line in open(path, errors="ignore"):
        if BLOCK.search(line):
            pending = {"type": BLOCK.search(line).group(1)}
            continue
        if "BLOCK ADDED AS ALTERNATIVE" in line:
            alt += 1
            continue
        if "incorrect block major version" in line or "prevalidation failed" in line:
            verrej += 1
            continue
        if pending is not None:
            m = IDLINE.match(line)
            if m:
                pending["id"] = m.group(1)
                continue
            m = HEIGHT.search(line)
            if m and "id" in pending:
                chain[int(m.group(1))] = (pending["id"], pending["type"])
                pending = None
    return chain, alt, verrej

if len(sys.argv) < 3:
    sys.exit("нужно минимум два файла zanod.log")

nodes = []
for path in sys.argv[1:]:
    chain, alt, verrej = read(path)
    nodes.append((path, chain, alt, verrej))
    top = max(chain) if chain else -1
    print(f"{path}")
    print(f"  блоков {len(chain)}, вершина {top}"
          + (f", хеш {chain[top][0][:16]}…" if chain else "")
          + f", альтернативных {alt}, отказов по версии {verrej}")

base = nodes[0][1]
ok = True
print()
for path, chain, _, _ in nodes[1:]:
    common = sorted(set(base) & set(chain))
    if not common:
        print(f"РАСХОЖДЕНИЕ: с {path} нет общих высот"); ok = False; continue
    split = next((h for h in common if base[h][0] != chain[h][0]), None)
    if split is None:
        lo, hi = max(base), max(chain)
        print(f"согласие с {path}: {len(common)} общих высот, все хеши совпадают")
        if lo != hi:
            print(f"  ⚠️ вершины разные: {lo} и {hi} — отставание на {abs(lo-hi)} блоков,"
                  " одна нода просто не догнала")
    else:
        ok = False
        print(f"РАСХОЖДЕНИЕ с {path} на высоте {split}")
        print(f"  {nodes[0][0]}: {base[split][0][:16]}… [{base[split][1]}]")
        print(f"  {path}: {chain[split][0][:16]}… [{chain[split][1]}]")
        print(f"  общий предок — высота {split - 1}")

print()
print("ИТОГ: цепи согласованы" if ok else "ИТОГ: цепи разошлись")
sys.exit(0 if ok else 1)
