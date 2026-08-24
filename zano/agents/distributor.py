#!/usr/bin/env python3
"""Раздатчик: разводит премайн с казначейского кошелька по стейкерам.

Пункт 2.5 плана. Без раздачи стейкает только казна, а её премайн больше любой
достижимой сложности — `final_diff = basic_diff / stake_amount` в
pos_mining.cpp:87 даёт для неё ноль, и ретаргет её не сдерживает (см. 2.4).

Сигнализировать стейкерам о готовности не нужно: они запускаются сразу с
`--do-pos-mining`, а `is_transfer_okay_for_pos` сама пропускает выходы моложе
`min_coinstake_age`. Как только перевод дозреет, стейкер включится сам.

Потолок получателей в одной транзакции — CURRENCY_TX_MAX_ALLOWED_OUTS = 32,
жёсткое правило с HF6. До HF4 действует 2000, но закладываться на это нельзя:
фаза 3 включает хардфорки и сценарий сломается молча. Отсюда BATCH_SIZE = 30.

MIXIN по умолчанию 0: на свежей цепи нет выходов, из которых набрать ложные
входы, и запрос с ненулевым mixin отказывает.
"""
import json, os, sys, time, urllib.request

WALLET  = os.environ.get("ZANO_WALLET_RPC", "127.0.0.1:11212")
DAEMON  = os.environ.get("ZANO_DAEMON_RPC", "127.0.0.1:11211")
STAKERS = os.environ.get("STAKERS_JSON", "/home/user/zanogen/stakers.json")
COUNT   = int(os.environ.get("STAKER_COUNT", "16"))
PER     = float(os.environ.get("AMOUNT_PER_STAKER", "5000"))    # монет
BATCH   = int(os.environ.get("BATCH_SIZE", "30"))
MIXIN   = int(os.environ.get("MIXIN", "0"))
FEE     = int(os.environ.get("FEE_ATOMIC", "10000000000"))      # TX_DEFAULT_FEE = 0.01
AGE     = int(os.environ.get("COINSTAKE_AGE", "10"))            # min_coinstake_age
POLL    = float(os.environ.get("POLL_SEC", "10"))
AGENT   = os.environ.get("AGENT_ID", "dist-001")
COIN    = 10**12                                                # CURRENCY_DISPLAY_DECIMAL_POINT

def log(m):
    print(f"[{AGENT}] {m}", flush=True)

def rpc(host, method, params=None):
    body = json.dumps({"jsonrpc": "2.0", "id": "0",
                       "method": method, "params": params or {}}).encode()
    req = urllib.request.Request(f"http://{host}/json_rpc", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())

def height():
    return rpc(DAEMON, "getblockcount")["result"]["count"] - 1

def unlocked():
    return rpc(WALLET, "getbalance")["result"]["unlocked_balance"]

def wait_for(what, probe, want, limit=3600):
    """Ждёт, пока probe() не достигнет want. Возвращает достигнутое значение."""
    spent = 0.0
    while spent < limit:
        try:
            v = probe()
        except Exception as e:
            log(f"опрос {what} не удался: {e}")
            v = None
        if v is not None and v >= want:
            return v
        time.sleep(POLL)
        spent += POLL
    raise TimeoutError(f"{what} не достиг {want} за {limit} с")

targets = json.load(open(STAKERS))[:COUNT]
per_atomic = int(PER * COIN)
need = len(targets) * per_atomic + FEE * ((len(targets) + BATCH - 1) // BATCH)

log(f"старт: кошелёк={WALLET} нода={DAEMON}")
log(f"получателей={len(targets)} по {PER:g} монет, всего с комиссией "
    f"{need / COIN:.2f}, партиями по {BATCH}, mixin={MIXIN}")

# Премайн разблокируется на высоте CURRENCY_MINED_MONEY_UNLOCK_WINDOW = 10.
h = wait_for("высота", height, AGE + 1)
log(f"цепь доросла до {h}, премайн разблокирован")

bal = wait_for("баланс", unlocked, need)
log(f"на кошельке {bal / COIN:.2f} монет, хватает")

sent, hashes = 0, []
for i in range(0, len(targets), BATCH):
    part = targets[i:i + BATCH]
    dests = [{"amount": per_atomic, "address": t["address"]} for t in part]
    for attempt in range(1, 6):
        try:
            r = rpc(WALLET, "transfer",
                    {"destinations": dests, "fee": FEE, "mixin": MIXIN,
                     "payment_id": "", "comment": f"stake funding {i // BATCH + 1}"})
        except Exception as e:
            log(f"партия {i // BATCH + 1}: ошибка RPC ({e}), попытка {attempt}")
            time.sleep(POLL); continue
        if "error" in r:
            log(f"партия {i // BATCH + 1}: отказ {r['error']}, попытка {attempt}")
            time.sleep(POLL); continue
        h = r["result"]["tx_hash"]
        hashes.append(h)
        sent += len(part)
        log(f"партия {i // BATCH + 1}: {len(part)} получателей, tx {h[:16]}…")
        break
    else:
        log(f"партия {i // BATCH + 1}: НЕ ОТПРАВЛЕНА после 5 попыток")
    time.sleep(POLL)

if sent != len(targets):
    log(f"ОШИБКА: отправлено {sent} из {len(targets)}")
    sys.exit(1)

# Выходы становятся пригодными для стейка через min_coinstake_age блоков
# после включения в блок, а не после отправки.
h0 = height()
log(f"все {sent} переводов отправлены на высоте {h0}, ждём {AGE + 2} блоков дозревания")
h1 = wait_for("высота", height, h0 + AGE + 2)
log(f"высота {h1}: переводы дозрели, стейкеры включаются сами")
log(f"готово: {sent} получателей, {len(hashes)} транзакций, "
    f"остаток казны {unlocked() / COIN:.2f} монет")
