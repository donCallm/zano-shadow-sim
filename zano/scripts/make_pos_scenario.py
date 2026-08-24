#!/usr/bin/env python3
"""Генератор сценария Shadow для стенда PoS: нода, драйвер PoW, казна,
раздатчик и N стейкеров.

Пункт 2.5. Руками такой конфиг не пишут: каждому кошельку нужен свой
--rpc-bind-port, а их полтора десятка.

Казначейский кошелёк запускается БЕЗ --do-pos-mining. Он только источник
средств: его премайн больше любой достижимой сложности и ретаргет его не
сдерживает (см. 2.4), поэтому стейкать должен не он.

Стейкеры стартуют сразу, ещё без монет. Ждать раздачи им не нужно —
is_transfer_okay_for_pos сама пропускает незрелые выходы, и стейкер включится,
как только перевод дозреет.

Пример:
    python3 make_pos_scenario.py --stakers 16 --stop-time 14400 \
        --out ~/zanosim/pos-stakers.yaml
"""
import argparse, json, os

p = argparse.ArgumentParser()
p.add_argument("--stakers", type=int, default=16)
p.add_argument("--stop-time", type=int, default=14400)
p.add_argument("--per-staker", type=float, default=5000)
p.add_argument("--seed", type=int, default=12345)
p.add_argument("--pow-target", type=int, default=120)
p.add_argument("--zano", default=os.path.expanduser("~/zano/build-sim/src"))
p.add_argument("--gen", default=os.path.expanduser("~/zanogen"))
p.add_argument("--data-dir", default="/tmp/zpos")
p.add_argument("--home", default=os.path.expanduser("~"))
p.add_argument("--python", default="/usr/bin/python3.12")
p.add_argument("--out", required=True)
a = p.parse_args()

treasury = json.load(open(f"{a.gen}/treasury.json"))[0]
stakers = json.load(open(f"{a.gen}/stakers.json"))[:a.stakers]

IP, P2P, RPC = "11.0.0.1", 11121, 11211
TREASURY_RPC, STAKER_RPC0 = 11212, 11300
ENV = f"{{HOME: {a.home}, MALLOC_ARENA_MAX: '1'}}"

out = [f"""general:
  stop_time: {a.stop_time}s
  seed: {a.seed}
  parallelism: 0
  model_unblocked_syscall_latency: true
  log_level: warning
  progress: true
experimental:
  use_dynamic_runahead: true
  native_preemption_enabled: true
network:
  graph:
    type: 1_gbit_switch
hosts:
  zano1:
    network_node_id: 0
    ip_addr: {IP}
    processes:
      - path: {a.zano}/zanod
        args: [--data-dir={a.data_dir}/n1, --no-predownload, --disable-upnp, --no-console,
               --db-engine=lmdb, --allow-local-ip, --p2p-bind-ip={IP},
               --p2p-bind-port={P2P}, --rpc-bind-ip={IP}, --rpc-bind-port={RPC},
               --rpc-ignore-offline, --log-level=1]
        environment: {ENV}
        start_time: 1s
        expected_final_state: running

      - path: {a.python}
        args: [{a.gen}/agents/pow_driver.py]
        environment:
          HOME: {a.home}
          ZANO_RPC: {IP}:{RPC}
          ZANO_MINER_ADDRESS: "{treasury['address']}"
          POW_TARGET_SEC: '{a.pow_target}'
          SIMULATION_SEED: '{a.seed}'
          AGENT_ID: pow-001
        start_time: 20s
        expected_final_state: running

      - path: {a.zano}/simplewallet
        args: [--wallet-file={treasury['wallet_file']}, --password=sim,
               --daemon-address={IP}:{RPC},
               --rpc-bind-ip={IP}, --rpc-bind-port={TREASURY_RPC},
               --unsecure-no-auth, --log-level=1]
        environment: {ENV}
        start_time: 25s
        expected_final_state: running

      - path: {a.python}
        args: [{a.gen}/agents/distributor.py]
        environment:
          HOME: {a.home}
          ZANO_WALLET_RPC: {IP}:{TREASURY_RPC}
          ZANO_DAEMON_RPC: {IP}:{RPC}
          STAKERS_JSON: {a.gen}/stakers.json
          STAKER_COUNT: '{a.stakers}'
          AMOUNT_PER_STAKER: '{a.per_staker:g}'
          AGENT_ID: dist-001
        start_time: 60s
        expected_final_state: {{exited: 0}}
"""]

for i, s in enumerate(stakers):
    out.append(f"""
      - path: {a.zano}/simplewallet
        args: [--wallet-file={s['wallet_file']}, --password=sim,
               --daemon-address={IP}:{RPC},
               --rpc-bind-ip={IP}, --rpc-bind-port={STAKER_RPC0 + i},
               --do-pos-mining, --unsecure-no-auth, --log-level=1]
        environment: {ENV}
        start_time: {30 + i}s
        expected_final_state: running
""")

open(a.out, "w", newline="\n").write("".join(out))
total = a.stakers * a.per_staker
print(f"записано: {a.out}")
print(f"  процессов: 1 нода + 1 драйвер + 1 казна + 1 раздатчик + {a.stakers} стейкеров "
      f"= {a.stakers + 4}")
print(f"  раздача: {a.stakers} × {a.per_staker:g} = {total:g} монет "
      f"из {1094825.1875:.0f} на казне")
print(f"  порты кошельков: казна {TREASURY_RPC}, стейкеры "
      f"{STAKER_RPC0}..{STAKER_RPC0 + a.stakers - 1}")
