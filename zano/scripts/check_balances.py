#!/usr/bin/env python3
import json, re, subprocess
from pathlib import Path
SW = Path.home()/"zano/build-sim/src/simplewallet"
BASE = Path.home()/"zanogen"
PREMINE, COIN = 17517203000000000000, 1000000000000
TOT = re.compile(r"/\s+([0-9]+\.[0-9]+)\s+ZANO")

total = 0
for rec in json.loads((BASE/"treasury.json").read_text()):
    r = subprocess.run([str(SW), f"--wallet-file={rec['wallet_file']}", "--password=sim",
                        "--daemon-address=127.0.0.1:11211", "balance"],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    m = TOT.search(r.stdout + r.stderr)
    if not m:
        print(f"{rec['id']}: НЕ РАЗОБРАН баланс\n{r.stdout[-400:]}"); break
    v = float(m.group(1)); total += round(v * COIN)
    print(f"  {rec['id']}: {v}")
print(f"\nсумма атомарных: {total}")
print(f"PREMINE_AMOUNT:  {PREMINE}")
print(f"сходится:        {total == PREMINE}")
