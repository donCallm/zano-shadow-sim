#!/usr/bin/env python3
"""Генератор сценария Shadow для стенда PoS: N нод, драйверы PoW, казна,
раздатчик и M стейкеров, поделённых между нодами.

Пункты 2.5 и 3.0. Руками такой конфиг не пишут: каждому кошельку нужен свой
--rpc-bind-port, а их полтора десятка на ноду.

Казначейский кошелёк запускается БЕЗ --do-pos-mining. Он только источник
средств: его премайн больше любой достижимой сложности и ретаргет его не
сдерживает (см. 2.4), поэтому стейкать должен не он.

Стейкеры стартуют сразу, ещё без монет. Ждать раздачи им не нужно —
is_transfer_okay_for_pos сама пропускает незрелые выходы, и стейкер включится,
как только перевод дозреет.

Ноды связываются взаимными --add-priority-node. Драйвер PoW ставится на каждую
ноду с равным весом: контрольный опыт 3.0 должен отличаться от расхождения 3.3
только расписанием хардфорков, а значит обе ноды обязаны производить блоки.

ВНИМАНИЕ: связь между нодами АСИММЕТРИЧНА, и это не стиль, а обход тупика.
Нода набирает только ноды с меньшим номером. При взаимных --add-priority-node
обе стороны звонят друг другу одновременно, у каждой возникает по два
соединения на пару, is_peer_used (net_node.inl:815) видит дубликат и рвёт
исходящее — а исходящее одной ноды это входящее другой. Обе рвут симметрично,
в живых не остаётся ни одного соединения, и через период повтора всё
повторяется. Замер 3.0 до правки: 3592 разрыва за два часа, ноль успешных
рукопожатий, ноль обменов блоками, цепи разошлись с высоты 1. Отметки времени
разрывов на двух нодах отличались на одну микросекунду.

Старты нод при этом тоже разнесены, шагом в ОДНУ секунду. Шаг в две секунды
брать нельзя: он совпадает с периодом повтора соединения, фазы выравниваются и
тупик воспроизводится даже при асимметричной связи.

Примеры:
    python3 make_pos_scenario.py --stakers 16 --out ~/zanosim/pos-stakers.yaml
    python3 make_pos_scenario.py --nodes 2 --stakers 16 --stop-time 7200 \
        --out ~/zanosim/two-nodes.yaml
"""
import argparse, json, os

p = argparse.ArgumentParser()
p.add_argument("--nodes", type=int, default=1)
p.add_argument("--stakers", type=int, default=16)
p.add_argument("--stop-time", type=int, default=14400)
p.add_argument("--per-staker", type=float, default=5000)
p.add_argument("--seed", type=int, default=12345)
p.add_argument("--pow-target", type=int, default=120)
p.add_argument("--pos-starter", default="",
               help="аргумент --pos-starter-difficulty демона; пусто — не задавать")
p.add_argument("--hard-fork", default="",
               help="общее расписание хардфорков вида 1:5,3:10 — уходит каждому zanod и "
                    "каждому simplewallet; пусто — не задавать. Кошелёк не может узнать "
                    "расписание от демона по RPC (проброс — задача генератора), а сверка "
                    "hardforks missmatch — лишь вторая линия защиты (см. §21)")
p.add_argument("--hard-fork-node", action="append", default=[], metavar="N=SPEC",
               help="расписание для ОДНОЙ ноды, вместо общего: --hard-fork-node 2=4:10,6:999999. "
                    "Повторяемый. Нумерация нод с единицы, как в именах хостов zanoN. "
                    "Кошельки ноды получают расписание СВОЕГО демона — рассинхрон "
                    "кошелёк-демон внутри ноды невозможен по построению. "
                    "ВНИМАНИЕ, ловушка set_hardfork_height: незаданные РАННИЕ форки "
                    "подтягиваются к младшей заданной высоте, поэтому отстающая нода "
                    "обязана перечислить ранние форки явно: 4:10,6:999999 — а не 6:999999")
p.add_argument("--zano", default=os.path.expanduser("~/zano/build-sim/src"))
p.add_argument("--gen", default=os.path.expanduser("~/zanogen"))
p.add_argument("--data-dir", default="/tmp/zpos")
p.add_argument("--home", default=os.path.expanduser("~"))
p.add_argument("--python", default="/usr/bin/python3.12")
p.add_argument("--out", required=True)
a = p.parse_args()

treasury = json.load(open(f"{a.gen}/treasury.json"))[0]
stakers = json.load(open(f"{a.gen}/stakers.json"))[:a.stakers]

P2P, RPC = 11121, 11211
TREASURY_RPC, STAKER_RPC0 = 11212, 11300
ENV = f"{{HOME: {a.home}, MALLOC_ARENA_MAX: '1'}}"
ip = lambda i: f"11.0.0.{i + 1}"

# Разнос стартов: см. предупреждение про peer_id в шапке.
NODE_T0, NODE_STEP = 1, 1
node_start = lambda n: NODE_T0 + n * NODE_STEP
agents_t0 = lambda: node_start(a.nodes - 1) + 10

# Стейкеры делятся между нодами по кругу: сосед по номеру попадает на другую
# ноду, так что расхождение в 3.3 не совпадёт с границей раздачи.
by_node = [stakers[i::a.nodes] for i in range(a.nodes)]
starter = f", --pos-starter-difficulty={a.pos_starter}" if a.pos_starter else ""
def mk_hf(spec):
    return "".join(f", --hard-fork={x.strip()}" for x in spec.split(",") if x.strip()) if spec else ""

hf_by_node = {}
for item in a.hard_fork_node:
    node_s, sep, spec = item.partition("=")
    if not sep or not node_s.isdigit() or not (1 <= int(node_s) <= a.nodes):
        raise SystemExit(f"--hard-fork-node {item!r}: ожидается N=SPEC, N в 1..{a.nodes}")
    hf_by_node[int(node_s) - 1] = spec

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
"""]

for n in range(a.nodes):
    # Только к нодам с меньшим номером: см. предупреждение про тупик в шапке.
    peers = "".join(f", --add-priority-node={ip(k)}:{P2P}" for k in range(n))
    hf = mk_hf(hf_by_node.get(n, a.hard_fork))
    out.append(f"""  zano{n + 1}:
    network_node_id: 0
    ip_addr: {ip(n)}
    processes:
      - path: {a.zano}/zanod
        args: [--data-dir={a.data_dir}/n{n + 1}, --no-predownload, --disable-upnp, --no-console,
               --db-engine=lmdb, --allow-local-ip, --p2p-bind-ip={ip(n)},
               --p2p-bind-port={P2P}, --rpc-bind-ip={ip(n)}, --rpc-bind-port={RPC},
               --rpc-ignore-offline{starter}{hf}{peers}, --log-level=1]
        environment: {ENV}
        start_time: {node_start(n)}s
        expected_final_state: running

      - path: {a.python}
        args: [{a.gen}/agents/pow_driver.py]
        environment:
          HOME: {a.home}
          ZANO_RPC: {ip(n)}:{RPC}
          ZANO_MINER_ADDRESS: "{treasury['address']}"
          POW_TARGET_SEC: '{a.pow_target}'
          MINER_WEIGHT: '{100 / a.nodes:g}'
          TOTAL_WEIGHT: '100'
          SIMULATION_SEED: '{a.seed}'
          AGENT_ID: pow-{n + 1:03d}
        start_time: {agents_t0() + n}s
        expected_final_state: running
""")

    # Казна и раздатчик живут только на первой ноде.
    if n == 0:
        out.append(f"""
      - path: {a.zano}/simplewallet
        args: [--wallet-file={treasury['wallet_file']}, --password=sim,
               --daemon-address={ip(0)}:{RPC},
               --rpc-bind-ip={ip(0)}, --rpc-bind-port={TREASURY_RPC},
               --unsecure-no-auth{hf}, --log-level=1]
        environment: {ENV}
        start_time: {agents_t0() + a.nodes + 1}s
        expected_final_state: running

      - path: {a.python}
        args: [{a.gen}/agents/distributor.py]
        environment:
          HOME: {a.home}
          ZANO_WALLET_RPC: {ip(0)}:{TREASURY_RPC}
          ZANO_DAEMON_RPC: {ip(0)}:{RPC}
          STAKERS_JSON: {a.gen}/stakers.json
          STAKER_COUNT: '{a.stakers}'
          AMOUNT_PER_STAKER: '{a.per_staker:g}'
          AGENT_ID: dist-001
        start_time: {agents_t0() + a.nodes + 40}s
        expected_final_state: {{exited: 0}}
""")

    for j, s in enumerate(by_node[n]):
        out.append(f"""
      - path: {a.zano}/simplewallet
        args: [--wallet-file={s['wallet_file']}, --password=sim,
               --daemon-address={ip(n)}:{RPC},
               --rpc-bind-ip={ip(n)}, --rpc-bind-port={STAKER_RPC0 + j},
               --do-pos-mining, --unsecure-no-auth{hf}, --log-level=1]
        environment: {ENV}
        start_time: {agents_t0() + a.nodes + 2 + n * 100 + j}s
        expected_final_state: running
""")
    out.append("\n")

open(a.out, "w", newline="\n").write("".join(out))
procs = a.nodes * 2 + 2 + a.stakers
print(f"записано: {a.out}")
print(f"  нод: {a.nodes}, стейкеров: {a.stakers} "
      f"({', '.join(str(len(g)) for g in by_node)} по нодам)")
print(f"  процессов всего: {procs} "
      f"({a.nodes} нод + {a.nodes} драйверов + казна + раздатчик + {a.stakers} стейкеров)")
print(f"  раздача: {a.stakers} × {a.per_staker:g} = {a.stakers * a.per_staker:g} монет")
print(f"  старты нод: " + ", ".join(f"{node_start(n)}s" for n in range(a.nodes))
      + " — разнесены ради разных peer_id, см. шапку")
if a.pos_starter:
    print(f"  посев сложности PoS: {a.pos_starter}")
if a.hard_fork or hf_by_node:
    for n in range(a.nodes):
        spec = hf_by_node.get(n, a.hard_fork)
        tag = " (индивидуальное)" if n in hf_by_node else ""
        print(f"  хардфорки zano{n + 1} и её кошельков: {spec or '(дефолт сборки)'}"+tag)
else:
    print("  посев сложности PoS: по умолчанию сборки (1e17)")
