#!/usr/bin/env python3
import json
from pathlib import Path

BASE = Path.home() / "zanogen"
PREMINE = 17517203000000000000          # currency_config.h:244
COIN    = 1000000000000                 # currency_config.h:73

t = json.loads((BASE / "treasury.json").read_text())
n = len(t)
assert PREMINE % n == 0, f"{PREMINE} не делится нацело на {n}"
share_atomic = PREMINE // n
share_coins  = share_atomic / COIN

# контроль: обратное преобразование, как в conn_tool.cpp:316
assert round(share_coins * COIN) == share_atomic, "double съел точность"
assert round(share_coins * COIN) * n == PREMINE, "сумма не сходится"

payments = [{
    "address_this":        w["address"],
    "amount_this": share_coins,
    "paid_btc":            "1",         # без этого самопроверки conn_tool ругаются
    "btc_usd_price":       "1",
} for w in t]

out = {"payments": payments,
       "proof_string": "monerosim zano simnet genesis 2026-08-24"}
(BASE / "genesis_source.json").write_text(json.dumps(out, indent=2))

print(f"кошельков:      {n}")
print(f"доля атомарных: {share_atomic}")
print(f"доля в монетах: {share_coins}")
print(f"сумма:          {share_atomic * n}  (PREMINE_AMOUNT: {PREMINE})")
print(f"сходится:       {share_atomic * n == PREMINE}")
