#!/usr/bin/env python3
import json, re, subprocess, sys
from pathlib import Path

SW    = Path.home() / "zano/build-sim/src/simplewallet"
BASE  = Path.home() / "zanogen"
PASS  = "sim"
ADDR_RE = re.compile(r"Generated new\s+wallet:\s+(Zx\S+)")

def make(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", ".keys", ".address.txt"):
        p = Path(str(path) + suffix)
        if p.exists():
            p.unlink()
    r = subprocess.run(
        [str(SW), f"--generate-new-wallet={path}", f"--password={PASS}",
         "--no-password-confirmation", "--offline-mode"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    m = ADDR_RE.search(r.stdout + r.stderr)
    if not m:
        sys.exit(f"НЕ РАЗОБРАН адрес для {path}\n--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}")
    return m.group(1)

def batch(kind: str, count: int, width: int):
    out = []
    for i in range(1, count + 1):
        wid  = f"{kind}-{i:0{width}d}"
        path = BASE / f"{kind}s" / f"{wid}.wallet"
        addr = make(path)
        out.append({"id": wid, "address": addr, "wallet_file": str(path)})
        if i % 20 == 0 or i == count:
            print(f"  {kind}: {i}/{count}")
    return out

print("Казна (16):")
treasury = batch("treasury", 16, 3)
print("Стейкеры (200):")
stakers  = batch("staker", 200, 3)

addrs = [w["address"] for w in treasury + stakers]
assert len(set(addrs)) == len(addrs), "НАЙДЕНЫ ДУБЛИ АДРЕСОВ"
assert all(a.startswith("Zx") for a in addrs), "адрес не начинается с Zx"

(BASE / "treasury.json").write_text(json.dumps(treasury, indent=2))
(BASE / "stakers.json").write_text(json.dumps(stakers, indent=2))
print(f"\nГотово: {len(treasury)} казначейских + {len(stakers)} стейкеров, все адреса уникальны")
