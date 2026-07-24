#!/usr/bin/env python3
"""Block/tx propagation-delta analysis across monerod + cuprate daemon logs.

Normalizes daemon logs from either node implementation into a common event
schema (node, impl, event, hash, ts) and computes block/tx propagation
statistics for a run, or the delta between two runs (e.g. an all-monerod
baseline vs a mixed monerod+cuprate network) so we can see whether cuprate
nodes lag the monerod flood.

Node-type autodetection, per subdir of a daemon_logs/ directory:
    - monerod: subdir contains bitmonero.log
    - cuprate: subdir has no bitmonero.log; the first regular file in the
      subdir (e.g. a date-named file like "2000-01-01") is treated as the
      log.

Usage:
    python3 analysis/prop_delta.py <daemon_logs_dir>
    python3 analysis/prop_delta.py <baseline_daemon_logs> <cuprate_daemon_logs>
    python3 analysis/prop_delta.py <daemon_logs_dir> [--csv PATH]
"""

import argparse
import csv
import re
import sys
from pathlib import Path

# --- log-line patterns --------------------------------------------------

_HASH = r"[0-9a-f]{64}"
TS_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})\.(\d+)")

MONEROD_BLOCK_RE = re.compile(r"Received NOTIFY_NEW_FLUFFY_BLOCK <(" + _HASH + ")>")
MONEROD_TX_RE = re.compile(r"Including transaction <(" + _HASH + ")>")
CUPRATE_BLOCK_RE = re.compile(r'Successfully added block hash="(' + _HASH + ')"')
# Cuprate logs tx receipt as `... passing tx to tx-pool manager tx="<hash>"`
# (and `handle_incoming_tx{tx_id="<hash>"}`); the hash is QUOTED. Gated by the
# phrase check in iter_events, so match either quoted field. (Validated against
# real cuprate tx data 2026-07-24.)
CUPRATE_TX_RE = re.compile(r'tx(?:_id)?="(' + _HASH + ')"')


def parse_ts(line: str):
    """Seconds-of-day from a line's leading timestamp.

    Handles both monerod ("2000-01-01 HH:MM:SS.mmm") and cuprate
    ("2000-01-01THH:MM:SS.ffffffZ") by regex-extracting HH:MM:SS.frac from
    the first ~40 chars; frac is normalized by its own digit count so it
    doesn't matter whether it's milli- or micro-seconds. Returns None if no
    timestamp is found.
    """
    m = TS_RE.search(line[:40])
    if not m:
        return None
    hh, mm, ss, frac = m.groups()
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(frac) / 10 ** len(frac)


def detect_node(node_dir: Path):
    """Return (impl, log_path) for a daemon_logs subdir, or (None, None)."""
    bitmonero = node_dir / "bitmonero.log"
    if bitmonero.is_file():
        return "monerod", bitmonero
    for entry in sorted(node_dir.iterdir()):
        if entry.is_file():
            return "cuprate", entry
    return None, None


def iter_events(impl: str, log_path: Path):
    """Yield (event, hash, ts) for one node's log file."""
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        if impl == "monerod":
            for line in f:
                if "NOTIFY_NEW_FLUFFY_BLOCK" in line:
                    m = MONEROD_BLOCK_RE.search(line)
                    if m is None:
                        continue
                    ts = parse_ts(line)
                    if ts is not None:
                        yield "block", m.group(1), ts
                elif "Including transaction" in line:
                    m = MONEROD_TX_RE.search(line)
                    if m is None:
                        continue
                    ts = parse_ts(line)
                    if ts is not None:
                        yield "tx", m.group(1), ts
        else:  # cuprate
            for line in f:
                if "Successfully added block" in line:
                    m = CUPRATE_BLOCK_RE.search(line)
                    if m is None:
                        continue
                    ts = parse_ts(line)
                    if ts is not None:
                        yield "block", m.group(1), ts
                elif "passing tx to tx-pool manager" in line or "handle_incoming_tx" in line:
                    m = CUPRATE_TX_RE.search(line)
                    if m is None:
                        continue
                    ts = parse_ts(line)
                    if ts is not None:
                        yield "tx", m.group(1), ts


# --- stats helpers --------------------------------------------------------

def _percentile(sorted_values, p):
    """Linear-interpolation percentile (p in [0, 100]) over a sorted list."""
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    k = (n - 1) * (p / 100)
    f, c = int(k), min(int(k) + 1, n - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def summarize(values):
    """{n, median, p90, max} over a list of numbers (None fields if empty)."""
    if not values:
        return {"n": 0, "median": None, "p90": None, "max": None}
    s = sorted(values)
    return {"n": len(s), "median": _percentile(s, 50), "p90": _percentile(s, 90), "max": s[-1]}


def compute_stats(observations, node_impl):
    """observations[event][hash][node] = ts  ->  per-event-type stats dict."""
    stats = {}
    for event in ("block", "tx"):
        reach_values = []
        lat_all, lat_monerod, lat_cuprate = [], [], []
        for node_ts in observations[event].values():
            if len(node_ts) < 2:
                continue
            first_seen = min(node_ts.values())
            reach_values.append(len(node_ts))
            for node, ts in node_ts.items():
                lat = ts - first_seen
                lat_all.append(lat)
                impl = node_impl.get(node)
                if impl == "monerod":
                    lat_monerod.append(lat)
                elif impl == "cuprate":
                    lat_cuprate.append(lat)
        stats[event] = {
            "n_hashes": len(observations[event]),
            "n_hashes_multi": len(reach_values),
            "reach": summarize(reach_values),
            "latency_all": summarize(lat_all),
            "latency_monerod": summarize(lat_monerod),
            "latency_cuprate": summarize(lat_cuprate),
        }
    return stats


# --- top-level API ---------------------------------------------------------

def analyze_run(daemon_logs_dir) -> dict:
    """Walk a run's daemon_logs/ dir and return node counts + propagation stats."""
    root = Path(daemon_logs_dir)
    node_impl = {}
    node_counts = {"monerod": 0, "cuprate": 0}
    observations = {"block": {}, "tx": {}}
    skipped = []

    for node_dir in sorted(root.iterdir()):
        if not node_dir.is_dir():
            continue
        impl, log_path = detect_node(node_dir)
        if impl is None:
            skipped.append(node_dir.name)
            continue
        node = node_dir.name
        node_impl[node] = impl
        node_counts[impl] += 1
        for event, h, ts in iter_events(impl, log_path):
            slot = observations[event].setdefault(h, {})
            prev = slot.get(node)
            if prev is None or ts < prev:
                slot[node] = ts

    if skipped:
        preview = ", ".join(skipped[:5]) + ("..." if len(skipped) > 5 else "")
        print(f"warning: {len(skipped)} subdir(s) had no usable log, skipped: {preview}",
              file=sys.stderr)

    return {
        "path": str(root),
        "node_counts": node_counts,
        "node_impl": node_impl,
        "observations": observations,
        "stats": compute_stats(observations, node_impl),
    }


# --- CSV export -------------------------------------------------------------

def events_to_rows(run):
    rows = []
    for event, by_hash in run["observations"].items():
        for h, node_ts in by_hash.items():
            for node, ts in node_ts.items():
                rows.append((node, run["node_impl"].get(node, ""), event, h, ts))
    return rows


def write_csv(path, rows):
    rows = sorted(rows, key=lambda r: (r[2], r[3], r[4]))  # event, hash, ts
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["node", "impl", "event", "hash", "ts"])
        w.writerows(rows)


# --- text rendering ----------------------------------------------------------

def _fmt(v, nd=3):
    return "n/a" if v is None else f"{v:.{nd}f}"


def _table(headers, rows):
    widths = [len(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(str(c)))
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths)),
              "  ".join("-" * w for w in widths)]
    for r in rows:
        lines.append("  ".join(str(c).ljust(w) for c, w in zip(r, widths)))
    return "\n".join(lines)


def _indent(text, prefix="  "):
    return "\n".join(prefix + line for line in text.splitlines())


def render_run(label, run):
    lines = [
        f"=== {label}: {run['path']} ===",
        f"nodes: monerod={run['node_counts']['monerod']}  cuprate={run['node_counts']['cuprate']}",
        "",
    ]
    for event, title in (("block", "BLOCKS"), ("tx", "TXS")):
        st = run["stats"][event]
        lines.append(title)
        lines.append(f"  distinct hashes: {st['n_hashes']}  ({st['n_hashes_multi']} with >=2 observers)")
        rows = [
            ["reach (nodes/hash)", _fmt(st["reach"]["median"], 1), _fmt(st["reach"]["p90"], 1),
             _fmt(st["reach"]["max"], 1), str(st["reach"]["n"])],
            ["latency all (s)", _fmt(st["latency_all"]["median"]), _fmt(st["latency_all"]["p90"]),
             _fmt(st["latency_all"]["max"]), str(st["latency_all"]["n"])],
            ["latency monerod-only (s)", _fmt(st["latency_monerod"]["median"]),
             _fmt(st["latency_monerod"]["p90"]), _fmt(st["latency_monerod"]["max"]),
             str(st["latency_monerod"]["n"])],
            ["latency cuprate-only (s)", _fmt(st["latency_cuprate"]["median"]),
             _fmt(st["latency_cuprate"]["p90"]), _fmt(st["latency_cuprate"]["max"]),
             str(st["latency_cuprate"]["n"])],
        ]
        lines.append(_indent(_table(["metric", "median", "p90", "max", "n"], rows)))
        lines.append("")
    return "\n".join(lines)


def render_delta(run_a, run_b, label_a="baseline", label_b="cuprate"):
    def d(a, b, nd=3):
        return "n/a" if a is None or b is None else f"{b - a:+.{nd}f}"

    rows = [
        ["nodes.monerod", str(run_a["node_counts"]["monerod"]), str(run_b["node_counts"]["monerod"]),
         f"{run_b['node_counts']['monerod'] - run_a['node_counts']['monerod']:+d}"],
        ["nodes.cuprate", str(run_a["node_counts"]["cuprate"]), str(run_b["node_counts"]["cuprate"]),
         f"{run_b['node_counts']['cuprate'] - run_a['node_counts']['cuprate']:+d}"],
    ]
    for event in ("block", "tx"):
        a, b = run_a["stats"][event], run_b["stats"][event]
        rows.append([f"{event}.distinct_hashes", str(a["n_hashes"]), str(b["n_hashes"]),
                     f"{b['n_hashes'] - a['n_hashes']:+d}"])
        for stat in ("median", "p90"):
            av, bv = a["reach"][stat], b["reach"][stat]
            rows.append([f"{event}.reach.{stat}", _fmt(av, 1), _fmt(bv, 1), d(av, bv, 1)])
        for stat in ("median", "p90", "max"):
            av, bv = a["latency_all"][stat], b["latency_all"][stat]
            rows.append([f"{event}.latency_all.{stat}(s)", _fmt(av), _fmt(bv), d(av, bv)])
        for metric in ("latency_monerod", "latency_cuprate"):
            av, bv = a[metric]["median"], b[metric]["median"]
            rows.append([f"{event}.{metric}.median(s)", _fmt(av), _fmt(bv), d(av, bv)])

    header = ["metric", label_a, label_b, "delta(b-a)"]
    return f"=== DELTA ({label_b} - {label_a}) ===\n" + _table(header, rows)


# --- CLI ---------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("daemon_logs_dir", type=Path, help="run's daemon_logs/ dir (baseline, if a second dir is given)")
    p.add_argument("daemon_logs_dir2", type=Path, nargs="?", default=None,
                    help="optional second run's daemon_logs/ dir (the cuprate/comparison run)")
    p.add_argument("--csv", type=Path, default=None, metavar="PATH",
                    help="dump normalized events (node,impl,event,hash,ts) to PATH")
    args = p.parse_args(argv)

    run_a = analyze_run(args.daemon_logs_dir)
    runs = [run_a]

    if args.daemon_logs_dir2 is not None:
        run_b = analyze_run(args.daemon_logs_dir2)
        runs.append(run_b)
        print(render_run("BASELINE", run_a))
        print(render_run("CUPRATE", run_b))
        print(render_delta(run_a, run_b))
    else:
        print(render_run("RUN", run_a))

    if args.csv is not None:
        rows = []
        for r in runs:
            rows.extend(events_to_rows(r))
        write_csv(args.csv, rows)
        print(f"wrote {len(rows)} events to {args.csv}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
