#!/usr/bin/env python3
"""Драйвер PoW: спит пуассоновский интервал, просит ноду сделать один блок.

Заменяет штатный майнер-поток zanod, который под Shadow вешает симулированное
время (miner.cpp:385 — цикл ProgPoW без системных вызовов).

БЕЗ обратной связи по сложности. Множитель по текущей сложности образует
неустойчивую петлю с ретаргетом цепи — урок коммита ec973683 монеросима.
"""
import json, os, random, sys, time, urllib.request

RPC     = os.environ.get("ZANO_RPC", "127.0.0.1:11311")
ADDR    = os.environ["ZANO_MINER_ADDRESS"]
TARGET  = float(os.environ.get("POW_TARGET_SEC", "120"))   # DIFFICULTY_POW_TARGET
WEIGHT  = float(os.environ.get("MINER_WEIGHT", "100"))     # доля этого майнера
TOTAL   = float(os.environ.get("TOTAL_WEIGHT", "100"))
SEED    = int(os.environ.get("SIMULATION_SEED", "12345"))
AGENT   = os.environ.get("AGENT_ID", "pow-001")
MAXB    = int(os.environ.get("MAX_BLOCKS", "0"))           # 0 = без предела

random.seed(SEED + sum(bytearray(AGENT.encode())))
MEAN = TARGET / (WEIGHT / TOTAL)     # средний интервал ЭТОГО майнера

def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": "0",
                       "method": method, "params": params}).encode()
    req = urllib.request.Request(f"http://{RPC}/json_rpc", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())

def log(m):
    print(f"[{AGENT}] {m}", flush=True)

log(f"старт: RPC={RPC} средний интервал={MEAN:.1f}с вес={WEIGHT}/{TOTAL}")
made = 0
while MAXB == 0 or made < MAXB:
    wait = random.expovariate(1.0 / MEAN)
    time.sleep(wait)
    try:
        r = rpc("generateblocks", {"amount_of_blocks": 1, "wallet_address": ADDR})
    except Exception as e:
        log(f"ошибка RPC: {e}"); continue
    if "error" in r:
        log(f"отказ: {r['error']}"); continue
    made += 1
    log(f"блок #{made} ждал={wait:.1f}с высота={r['result']['height']}")
log(f"готово: {made} блоков")
