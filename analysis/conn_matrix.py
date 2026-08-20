#!/usr/bin/env python3
"""Cross-implementation P2P connection matrix for mixed monerod/cuprate runs.

Reconstructs the peer graph from daemon logs and classifies every observed peer
relationship by the implementation pair at its endpoints, answering: how many
monerod<->monerod, monerod<->cuprate, and cuprate<->cuprate connections formed,
and whether mixing is assortative (implementations preferring their own kind).

Peer identity is the IP. That is sound here because monerosim assigns every host
a distinct IP (verified: 1014 hosts -> 1014 distinct IPs). Ports are unusable as
identity: an OUT connection shows the peer's listen port (18080) while an INC
connection shows its ephemeral source port.

Log formats (see docs/20260724_cuprate_conn_matrix.md):
  monerod  `[<ip>:<port> INC]` / `[<ip>:<port> OUT]`  (net.p2p / net.p2p.msg, INFO)
  cuprate  `...:inbound_server:handshaker{addr=<ip>:<port>}`        -> inbound
           `...:connect_to_<x>:handshaker{addr=<ip>:<port>}`        -> outbound
           `...:connection{addr=<ip>:<port>}`                       -> direction unknown

Extraction runs through grep -oE per file (C speed) because these archives are
tens of GB; only the deduplicated token set is brought into Python.

Usage:
    python3 analysis/conn_matrix.py <archive_dir> [<archive_dir> ...]
"""

import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import yaml

IP = r"[0-9]{1,3}(?:\.[0-9]{1,3}){3}"

MONEROD_TOKEN = re.compile(r"\[(" + IP + r"):[0-9]+ (INC|OUT)\]")
CUPRATE_HS_TOKEN = re.compile(
    r"(inbound_server|connect_to_[a-z_]+):handshaker\{addr=(" + IP + r"):[0-9]+\}"
)
CUPRATE_CONN_TOKEN = re.compile(r"connection\{addr=(" + IP + r"):[0-9]+\}")

# grep -oE patterns (POSIX ERE; no non-capturing groups)
GREP_MONEROD = r"\[[0-9]{1,3}(\.[0-9]{1,3}){3}:[0-9]+ (INC|OUT)\]"
GREP_CUPRATE = (
    r"(inbound_server|connect_to_[a-z_]+):handshaker\{addr=[0-9]{1,3}(\.[0-9]{1,3}){3}:[0-9]+\}"
    r"|connection\{addr=[0-9]{1,3}(\.[0-9]{1,3}){3}:[0-9]+\}"
)


def grep_unique(pattern, path, time_prefix=None):
    """Deduplicated set of matching tokens in one file.

    `time_prefix` is an ERE anchored at line start (e.g. `^2000-01-01[ T]04:0[0-4]:`)
    that restricts extraction to a simulated-time window. Both log formats put the
    timestamp first, so one prefix pattern serves monerod (`YYYY-MM-DD HH:MM:SS`)
    and cuprate (`YYYY-MM-DDTHH:MM:SS...Z`) alike.
    """
    procs = []
    if time_prefix:
        first = subprocess.Popen(
            ["grep", "-E", time_prefix, str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        procs.append(first)
        src = first.stdout
        p1 = subprocess.Popen(
            ["grep", "-ohE", pattern], stdin=src,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        src.close()
    else:
        p1 = subprocess.Popen(
            ["grep", "-ohE", pattern, str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    procs.append(p1)
    p2 = subprocess.Popen(
        ["sort", "-u"], stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    p1.stdout.close()
    out, _ = p2.communicate()
    for p in procs:
        p.wait()
    return out.decode("utf-8", "replace").splitlines()


def load_hosts(archive):
    """ip -> (host_name, impl) from the generated shadow config."""
    cfg = yaml.safe_load(open(Path(archive) / "shadow_agents.yaml"))
    by_ip, by_name = {}, {}
    for name, host in cfg["hosts"].items():
        procs = [p["path"].rsplit("/", 1)[-1] for p in host.get("processes", [])]
        if any("cuprated" in p for p in procs):
            impl = "cuprate"
        elif any("monerod" in p for p in procs):
            impl = "monerod"
        else:
            impl = "non-daemon"  # dnsserver, light wallets, monitor, distributor
        ip = host.get("ip_addr")
        by_ip[ip] = (name, impl)
        by_name[name] = (ip, impl)
    return by_ip, by_name


def detect_impl_and_log(node_dir):
    """(impl, log_path) for one daemon_logs subdir. monerod writes bitmonero.log;
    cuprate writes a date-named file. Mirrors analysis/prop_delta.py:detect_node."""
    bitmonero = node_dir / "bitmonero.log"
    if bitmonero.is_file():
        return "monerod", bitmonero
    cands = sorted(e for e in node_dir.iterdir() if e.is_file())
    if cands:
        # cuprate rotates daily; take the largest (the run's main file)
        return "cuprate", max(cands, key=lambda e: e.stat().st_size)
    return None, None


def peers_for_node(impl, log_path, time_prefix=None):
    """{peer_ip: set(directions)} observed by this node. Directions: IN, OUT, ?

    With `time_prefix`, this is the set of peers this node exchanged P2P traffic
    with *inside that window* — a concurrency proxy. Without it, the set is
    cumulative over the whole run, which saturates (every cuprate node eventually
    handshakes every other node), so windowed is the meaningful comparison.
    """
    peers = defaultdict(set)
    if impl == "monerod":
        for tok in grep_unique(GREP_MONEROD, log_path, time_prefix):
            m = MONEROD_TOKEN.search(tok)
            if m:
                peers[m.group(1)].add("IN" if m.group(2) == "INC" else "OUT")
    else:
        for tok in grep_unique(GREP_CUPRATE, log_path, time_prefix):
            m = CUPRATE_HS_TOKEN.search(tok)
            if m:
                peers[m.group(2)].add("IN" if m.group(1) == "inbound_server" else "OUT")
                continue
            m = CUPRATE_CONN_TOKEN.search(tok)
            if m:
                peers[m.group(1)].add("?")
    return peers


def analyze(archive, time_prefix=None):
    archive = Path(archive)
    by_ip, by_name = load_hosts(archive)
    logs = archive / "daemon_logs"

    node_peers = {}   # host_name -> {peer_ip: dirs}
    node_impl = {}    # host_name -> impl (from the log layout, ground truth)

    dirs = sorted(d for d in logs.iterdir() if d.is_dir())
    for i, node_dir in enumerate(dirs, 1):
        impl, log_path = detect_impl_and_log(node_dir)
        if impl is None:
            continue
        # daemon_logs/monero-<agent> -> <agent>
        name = node_dir.name[len("monero-"):] if node_dir.name.startswith("monero-") else node_dir.name
        node_impl[name] = impl
        node_peers[name] = peers_for_node(impl, log_path, time_prefix)
        if i % 25 == 0:
            print(f"  ... parsed {i}/{len(dirs)} node logs", flush=True)

    # Undirected edge set. Endpoint impl comes from the log layout where we
    # parsed that node ourselves, else from the shadow config.
    def impl_of_ip(ip):
        if ip not in by_ip:
            return "unknown"
        name, cfg_impl = by_ip[ip]
        return node_impl.get(name, cfg_impl)

    edges = {}  # frozenset({a_ip, b_ip}) -> set(kinds)
    skipped_unknown = defaultdict(int)
    for name, peers in node_peers.items():
        self_ip = by_name.get(name, (None, None))[0]
        if self_ip is None:
            continue
        for peer_ip, dirs_seen in peers.items():
            peer_impl = impl_of_ip(peer_ip)
            if peer_impl in ("unknown", "non-daemon"):
                skipped_unknown[peer_impl] += 1
                continue
            if peer_ip == self_ip:
                continue
            edges.setdefault(frozenset((self_ip, peer_ip)), set()).update(dirs_seen)

    # Classify edges by implementation pair
    pair_counts = defaultdict(int)
    for edge in edges:
        a, b = tuple(edge) if len(edge) == 2 else (next(iter(edge)),) * 2
        ia, ib = impl_of_ip(a), impl_of_ip(b)
        pair_counts[tuple(sorted((ia, ib)))] += 1

    # Per-node peer degree split by peer implementation
    deg = defaultdict(lambda: {"monerod": 0, "cuprate": 0})
    for name, peers in node_peers.items():
        self_ip = by_name.get(name, (None, None))[0]
        for peer_ip in peers:
            if peer_ip == self_ip:
                continue  # a node's own IP appears in its logs; not a peer
            pi = impl_of_ip(peer_ip)
            if pi in ("monerod", "cuprate"):
                deg[name][pi] += 1

    # Directional: for each observing-node impl, how many distinct peers of each
    # impl did it DIAL (OUT) vs ACCEPT (IN)? Cuprate bootstraps from monerod
    # seed_nodes, so a monerod skew in cuprate's OUT set is expected.
    dirmat = defaultdict(lambda: defaultdict(int))
    for name, peers in node_peers.items():
        self_ip = by_name.get(name, (None, None))[0]
        me = node_impl.get(name)
        for peer_ip, dirs_seen in peers.items():
            if peer_ip == self_ip:
                continue
            pi = impl_of_ip(peer_ip)
            if pi not in ("monerod", "cuprate"):
                continue
            for d in dirs_seen:
                dirmat[(me, d)][pi] += 1

    return {
        "archive": archive.name + ("" if not time_prefix else f"  [window {time_prefix}]"),
        "node_impl": node_impl,
        "pair_counts": dict(pair_counts),
        "deg": dict(deg),
        "dirmat": {k: dict(v) for k, v in dirmat.items()},
        "edges": len(edges),
        "skipped": dict(skipped_unknown),
    }


def summarize(r):
    node_impl = r["node_impl"]
    n_mon = sum(1 for v in node_impl.values() if v == "monerod")
    n_cup = sum(1 for v in node_impl.values() if v == "cuprate")
    n = n_mon + n_cup

    print(f"\n{'=' * 68}\n{r['archive']}\n{'=' * 68}")
    print(f"daemon nodes parsed: {n}  ({n_mon} monerod, {n_cup} cuprate)")
    if r["skipped"]:
        print(f"peer observations dropped (non-daemon/unknown IP): {r['skipped']}")

    print(f"\nDISTINCT PEER PAIRS (undirected): {r['edges']}")
    pc = r["pair_counts"]
    mm = pc.get(("monerod", "monerod"), 0)
    mc = pc.get(("cuprate", "monerod"), 0)
    cc = pc.get(("cuprate", "cuprate"), 0)
    tot = mm + mc + cc or 1
    print(f"  monerod <-> monerod : {mm:6d}  ({100 * mm / tot:5.1f}%)")
    print(f"  monerod <-> cuprate : {mc:6d}  ({100 * mc / tot:5.1f}%)")
    print(f"  cuprate <-> cuprate : {cc:6d}  ({100 * cc / tot:5.1f}%)")

    # Null models. The node-count null assumes both implementations have the same
    # degree; they do NOT (cuprate defaults to 32 outbound vs monerod's 12), so it
    # attributes a pure degree effect to "clustering". The DEGREE-CORRECTED
    # (configuration-model) null is the one to read: expected cross-group edge
    # share goes by each group's share of degree stubs, not of nodes.
    obs = (100 * mm / tot, 100 * mc / tot, 100 * cc / tot)
    if n_cup and n_mon:
        def report(label, p_m, p_c):
            e = (100 * p_m * p_m, 100 * 2 * p_m * p_c, 100 * p_c * p_c)
            print(f"\n  {label}")
            print(f"    expected: mon-mon {e[0]:5.1f}%   mon-cup {e[1]:5.1f}%   cup-cup {e[2]:5.1f}%")
            print(f"    obs-exp:  mon-mon {obs[0] - e[0]:+5.1f}pp  "
                  f"mon-cup {obs[1] - e[1]:+5.1f}pp  cup-cup {obs[2] - e[2]:+5.1f}pp")

        deg = r["deg"]
        stubs = {"monerod": 0.0, "cuprate": 0.0}
        for name, impl in node_impl.items():
            if name in deg and impl in stubs:
                stubs[impl] += deg[name]["monerod"] + deg[name]["cuprate"]
        s_tot = stubs["monerod"] + stubs["cuprate"]
        if s_tot:
            report(
                f"DEGREE-CORRECTED null (monerod {100 * stubs['monerod'] / s_tot:.1f}% of "
                f"degree, cuprate {100 * stubs['cuprate'] / s_tot:.1f}%)  <-- read this one",
                stubs["monerod"] / s_tot, stubs["cuprate"] / s_tot,
            )
        report(
            f"node-count null ({n_mon}/{n} monerod, {n_cup}/{n} cuprate) "
            f"— MISLEADING when degrees differ",
            n_mon / n, n_cup / n,
        )

    # Degree: how many peers of each impl does a node of each impl see?
    print("\nPEER DEGREE by node implementation (distinct peers per node):")
    print(f"  {'node impl':<10} {'n':>4} {'peers/node':>11} {'->monerod':>10} {'->cuprate':>10} {'%cuprate':>9}")
    for impl in ("monerod", "cuprate"):
        names = [k for k, v in r["node_impl"].items() if v == impl and k in r["deg"]]
        if not names:
            continue
        tm = sum(r["deg"][k]["monerod"] for k in names)
        tc = sum(r["deg"][k]["cuprate"] for k in names)
        cnt = len(names)
        tot_d = tm + tc or 1
        print(f"  {impl:<10} {cnt:>4} {tot_d / cnt:>11.1f} {tm / cnt:>10.1f} "
              f"{tc / cnt:>10.1f} {100 * tc / tot_d:>8.1f}%")

    # Who dials whom. '?' = cuprate lines carrying an address but no direction.
    # NOTE: only meaningful in CUMULATIVE mode. In a narrow window, cuprate's
    # handshakes mostly happened earlier, so its IN/OUT rows go ~empty and all its
    # traffic lands in '?' — do not read direction from a windowed run.
    dm = r.get("dirmat", {})
    if dm:
        print("\nDIRECTIONALITY (distinct peer observations, by observer impl):")
        print(f"  {'observer':<10} {'dir':<4} {'->monerod':>10} {'->cuprate':>10}")
        for impl in ("monerod", "cuprate"):
            for d in ("OUT", "IN", "?"):
                row = dm.get((impl, d))
                if not row:
                    continue
                print(f"  {impl:<10} {d:<4} {row.get('monerod', 0):>10} {row.get('cuprate', 0):>10}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    win = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--window=")), None)
    if not args:
        print(__doc__)
        print("  --window=<ERE>   restrict to a sim-time window, e.g.")
        print("                   --window='^2000-01-01[ T]04:0[0-4]:'")
        sys.exit(1)
    for arch in args:
        summarize(analyze(arch, win))
