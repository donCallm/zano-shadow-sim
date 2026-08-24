#!/usr/bin/env python3
import json, re, subprocess, sys
from pathlib import Path

SW   = Path.home() / "zano/build-sim/src/simplewallet"
BASE = Path.home() / "zanogen"
PASS = "sim"
ADDR_RE = re.compile(r"(Zx[1-9A-HJ-NP-Za-km-z]{90,})")

def check(rec):
    p = Path(rec["wallet_file"])
    if not p.exists():
        return f"{rec['id']}: файла нет"
    if p.stat().st_size < 100:
        return f"{rec['id']}: файл подозрительно мал ({p.stat().st_size} б)"
    r = subprocess.run(
        [str(SW), f"--wallet-file={p}", f"--password={PASS}", "--offline-mode", "address"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    found = set(ADDR_RE.findall(r.stdout + r.stderr))
    if rec["address"] not in found:
        return (f"{rec['id']}: адрес не совпал\n  ожидался: {rec['address']}\n"
                f"  найдено:  {found or 'ничего'}\n--- вывод ---\n{r.stdout}{r.stderr}")
    if "truncated" in (r.stdout + r.stderr).lower():
        return f"{rec['id']}: кошелёк ОБРЕЗАН — несовпадение CURRENCY_FORMATION_VERSION"
    return None

bad = 0
for name in ("treasury.json", "stakers.json"):
    recs = json.loads((BASE / name).read_text())
    print(f"{name}: проверяю {len(recs)}")
    for i, rec in enumerate(recs, 1):
        err = check(rec)
        if err:
            print("  ОШИБКА " + err); bad += 1
            if bad >= 3:
                sys.exit("останов после трёх ошибок")
        if i % 50 == 0:
            print(f"  {i}/{len(recs)}")
print(f"\nПроверено без ошибок: {bad == 0}")
