# Cuprate at scale: a monerod vs monerod+cuprate network experiment

**Date:** 2026-07-24
**Status:** Results — complete.
**Companion:** design/architecture in `docs/20260723_multi_node_type_architecture.md`.

## TL;DR

A 300-node regtest network that is **half cuprate (149 `cuprated` relays) / half
monerod** behaves like an all-monerod network of the same size — and propagates
transactions **noticeably faster**:

- **Full sync:** all 306 nodes (including all 149 cuprate relays) reached the
  chain tip (height 142). Zero failures, all success checks passed, in both runs.
- **Propagation reach:** every block and every transaction reached **100% of
  nodes** in both runs — cuprate relays strand nothing.
- **Block propagation:** comparable (median ~0.1s network-wide in both).
- **Transaction propagation: ~2× faster with cuprate relays** — monerod nodes
  saw txs propagate in **2.31s** median (half-cuprate) vs **4.95s** (all-monerod),
  measured by the *identical* monerod log event, so the speedup is real, not a
  measurement artifact. Likely due to cuprate's dandelion++ timing.
- **Connection behavior** (Rucknium analysis of the user nodes): consistent — 0%
  of connections exceed 6h in both; duration distributions are the same shape.

**Conclusion: `cuprated` is a viable drop-in relay at 300-node scale, and a
faster transaction relay than monerod in this environment.**

## Question

Does swapping a large fraction of a Monero simulation's relay nodes from monerod
to cuprate change network behavior — and if so, by how much (the "delta")? This is
the first scale test of the multi-node-type feature; prior work only proved
interop at 15 nodes.

## Experimental design

Two runs, **identical except node implementation**, for a clean A/B:

| | Run A — baseline | Run B — cuprate |
|---|---|---|
| Total daemon nodes | 300 | 300 |
| Miners (monerod, mine + seed) | 5 | 5 |
| Users (monerod + wallet, transact) | 50 | 50 |
| Relays | 245 monerod | **96 monerod + 149 cuprated** |
| `node_implementations` | (none) | `cuprated: 0.61` |
| Topology | `gml_processing/1200_nodes_caida_with_loops.gml` | same |
| `simulation_seed` | 20260723 | 20260723 (same) |
| Sim duration | 8h (coinbase matures ~2h → users transact from 3h) | same |

Cuprate nodes are relay-class only (they cannot mine or run a wallet), so the 5
miners and 50 users stay monerod. Configs: `test_configs/cuprate_exp_*_300_8h.yaml`.
Both launched via `run_sim.sh` (niced, to share the box politely). Run A completed
overnight; Run B was interrupted once by a power outage and re-run (Shadow has no
checkpoint/resume) — no data was lost since both archives are on persistent disk.

## Making cuprate data first-class ("handle properly")

Cuprate logs are not monerod logs, and for a rigorous cross-impl analysis they
can't be second-class. Three changes (committed `72cd80e0`, `5fe400b5`):

1. **`CupratedImpl` emits `[tracing.file] level="debug"`** — cuprate logs *block*
   events at INFO but *tx-relay, P2P connection, and handshake* events only at
   DEBUG, so a debug **file** sink (in each node's data dir) is required to
   capture propagation data comparable to monerod's `net.p2p.msg` logging. (stdout
   stays lean at INFO.)
2. **`run_sim.sh archive_daemon_logs`** also collects cuprate logs into
   `daemon_logs/<node>/` alongside monerod's `bitmonero.log`. Confirmed end-to-end
   on Run B: *"157 monerod (bitmonero.log) + 149 cuprate log file(s) archived"* —
   all 306 nodes uniform in the archive.
3. **`analysis/prop_delta.py`** normalizes both log formats into one event schema
   (block/tx receive, sim timestamps) and computes propagation latency + reach,
   **split by node implementation**.

## Results

### 1. Network health — equivalent

| Metric | Baseline (all monerod) | Cuprate (149 cuprate) |
|---|---|---|
| Completion | 8h, exit 0, **ALL CHECKS PASS** | 8h, exit 0, **ALL CHECKS PASS** |
| Blocks mined | 142 | 141 (same production; same seed) |
| Block intervals | mean 3.4m / median 2.2m | identical |
| Txs created / in blocks | 2290 / 2052 | 2304 / 2024 |
| Failures / alerts | 0 / 0 | **0 / 0** |

### 2. Full-network sync — including all cuprate nodes

Every node reached height 142. The 149 cuprate relays were verified from their own
logs (each added all 142 blocks, top height 142) — the run summary's "157 online"
is only because the monitor polls monerod RPC and doesn't count cuprate nodes; the
cuprate nodes *are* synced.

### 3. Propagation reach — 100% in both runs

Every block hash (142) and every tx hash (~2100) was observed by **all 306 nodes**
in both runs (median & p90 reach = 306). Inserting 149 cuprate relays does not
strand any block or transaction.

### 4. Block propagation latency — comparable

Latency = each node's receipt time minus the block's network-first-appearance
(seconds, sim-time):

| | median | p90 |
|---|---|---|
| Baseline — monerod | 0.136 | 0.211 |
| Cuprate run — monerod nodes | 0.115 | 0.188 |
| Cuprate run — cuprate nodes | 0.081 | 0.141 |

Blocks propagate network-wide in ~0.1s in both runs; cuprate nodes keep pace (in
fact slightly tighter). **Tail note:** the cuprate run shows a max block latency of
~152s, but this is a **boot-timing artifact, not a propagation failure** — the
outliers are all late-booting nodes (staggered start times) syncing the *earliest*
blocks at boot, each within ~6-8s of coming online. It affects both cuprate and
monerod nodes; the median/p90 exclude it.

### 5. Transaction propagation — ~2× faster with cuprate relays

Latency to receive+pool a transaction (sim-time seconds):

| | median | p90 |
|---|---|---|
| Baseline — monerod (`Including transaction`) | 4.95 | 5.88 |
| **Cuprate run — monerod nodes (same event)** | **2.31** | 3.64 |
| Cuprate run — cuprate nodes (`passing tx to tx-pool`) | 1.73 | 3.16 |

The robust result is the **monerod-vs-monerod** comparison (rows 1 and 2): the
*same* monerod `Including transaction` log event, so no cross-implementation
matching bias. Monerod nodes in the half-cuprate network see transactions ~2×
faster than in the all-monerod network. The cuprate nodes themselves are faster
still. This is most likely cuprate's dandelion++ timing (`fluff_probability=0.12`,
`time_between_hop=175ms`) diffusing transactions more aggressively.

*Matching caveat:* cuprate **batches** its tx broadcasts and logs only a batch
count (no per-tx send event), so a send-vs-send cross-impl comparison isn't
possible; the parser pairs the receive/process events (monerod `Including
transaction` ↔ cuprate `passing tx to tx-pool manager`), which the scouting of
both codebases confirmed are the closest semantic match. The monerod-vs-monerod
number above sidesteps this caveat entirely and is the one to trust.

### 6. Rucknium connection-duration analysis — consistent

`analysis/ruck_analysis.r` samples the 50 monerod **user** nodes (present in both
runs) and ignores cuprate logs by construction (it globs `monero-user-*/bitmonero.log`).

| | Baseline | Cuprate |
|---|---|---|
| Connections > 6h | **0%** | **0%** |
| Conn-duration median | 102 min | 127 min |
| Conn-duration max | 243 min | 245 min |
| Messages with >10 txs | 45.0% | 41.0% |

The distributions are the same shape (0% > 6h, max ~4h in both). The modest median
shift (102 → 127 min) suggests cuprate relays are slightly more stable connection
partners, but the overall connection behavior of the monerod user nodes is
unchanged by the presence of cuprate. (Outputs: `ruck_{baseline,cuprate}_output.txt`
+ `p2p-connection-duration.png` in each archive.)

## Mechanism: why cuprate propagates transactions faster

> **UPDATE 2026-07-25 — this section identifies relay *cadence* as the mechanism, but
> cadence is not the only contributor.** A follow-up connection analysis
> (`docs/20260725_cuprate_connection_matrix.md`) found cuprate nodes hold **~2.4× more
> concurrent peers** than monerod (61.5 vs 26.1), because cuprate defaults to 32 outbound
> connections against monerod's 12. Higher fan-out means fewer hops to cover the network,
> which is an independent second mechanism for the speedup. **The two have not been
> separated** — that needs a run with cuprate's `outbound_connections` pinned to 12. Read
> the speedup below as attributable to *cadence and fan-out together*, not cadence alone.

The tx-propagation speedup (§5) is structural, not incidental — it's a difference
in how the two implementations run dandelion++ (the privacy relay layer). On paper
the configs are near-identical; the difference is **timing granularity**:

| Knob | monerod | cuprate |
|---|---|---|
| Relay driver | **1-second poll loop** (`relay_txpool_transactions()` every 1s) | **event-driven, 175ms** between stem hops |
| Stem → fluff | timer-based embargo, poisson **avg 39s** ceiling | epoch role; hop until a Fluff-role node (~1.46s expected) |
| Fluff probability | 20% | 12% |
| Fluff flush | ~5s poisson | fast diffusion timer |
| Epoch | 10 min | 10 min |
| Regtest change | none (fakechain → mainnet params) | none |

The dominant factor is the **relay driver**. Monerod advances every relay step on a
**1-second poll loop**, so each dandelion hop waits up to ~1s (plus a ~5s poisson
fluff-flush); cuprate is **event-driven with 175ms hops** and immediate diffusion.
Each propagation hop is ~5-6× faster in cuprate, and over the multi-hop path to all
306 nodes that accumulates to the ~2× network-wide spread measured in §5.
(Sources: monero `src/cryptonote_config.h` + `tx_pool.cpp` + `cryptonote_core.cpp`
on-idle relay loop; cuprate `binaries/cuprated/src/txpool/dandelion.rs` +
`p2p/dandelion-tower/`.)

**End-to-end check (measured).** Joining each transaction's creation time
(`transaction_registry/transactions.json`) to its first network appearance confirms
that §5's latency *is* the full origin→network time: the originating node logs a tx
only **~0.14s median** after creation in *both* runs, so §5's spread already captures
the entire stem+fluff propagation. And monerod's **39s embargo does not materially
factor** — it is a rare safety ceiling a well-connected regtest network never hits
(max tx latency ~12s baseline / ~19s cuprate, far below 39s). So the ~2× is the real,
full end-to-end difference, driven by the per-hop relay cadence (monerod's 1s poll vs
cuprate's 175ms), *not* the embargo. (Earlier drafts speculated a large hidden
embargo delta; the measurement falsifies that.)

**Privacy dimension (nuanced — a trade-off, not a simple downgrade).** Dandelion++
obscures a tx's origin two ways: *graph* decorrelation (relay through N private stem
hops before broadcasting, so the broadcast point is N hops from the source) and
*time* decorrelation (delay before broadcast). Cuprate's lower fluff probability
(12% vs 20%) means *more* stem hops (~8 vs ~5) → arguably **stronger** graph
decorrelation; but its faster hops (175ms vs ~1s) make the stem ~3× shorter in time →
**weaker** against a timing-correlation adversary. So cuprate is not simply "less
private" at the network layer — it trades time-obfuscation for graph-obfuscation, and
the net effect is threat-model-dependent. Separately, the distinct relay *timing
signature* is a behavioral fingerprint. Both are treated in the privacy/fingerprinting
follow-up.

## Caveats & limitations

- **Wall-clock is not comparable.** Run A took 3h28m, Run B 2h41m — but this is a
  **shared box** (another user runs their own monero workloads), and the two runs
  ran under different co-tenant load, so wall-time carries co-tenancy noise. It is
  *not* evidence of a cuprate speedup. All the deterministic metrics above (sync,
  height, tx counts, propagation in sim-time) are unaffected by co-tenancy.
- **Monerod-centric monitoring.** The run summary counts only monerod nodes as
  "online"; cuprate sync was verified separately from cuprate logs.
- **Tx-event matching** (see §5): the cross-impl tx pairing is receive-vs-receive
  (best available); the trustworthy figure is the monerod-vs-monerod same-event
  comparison. A packet-level or added-instrumentation measurement could tighten it.
- Single seed / single run per arm. Repeats would quantify run-to-run variance.

## Conclusion

At 300-node scale, a network that is half `cuprated` is **functionally equivalent
to all-monerod** on every health and coverage metric — full sync of all 306 nodes,
identical block production, zero failures, 100% propagation reach — and is
**materially better at transaction propagation** (~2× faster, robustly measured).
Cuprate is a viable drop-in relay, and this experiment is the runtime evidence that
the multi-node-type feature works at scale.

## Reproducibility

- Configs: `test_configs/cuprate_exp_{baseline,cuprate}_300_8h.yaml`
- Analysis: `analysis/prop_delta.py` (propagation delta), `analysis/ruck_analysis.r` (connection duration)
- Archives: `archived_runs/20260723_212123_cuprate_exp_baseline`, `archived_runs/20260724_133050_cuprate_exp_cuprate`
- Commits: `72cd80e0` (debug file-logging + collection + parser + configs), `5fe400b5` (cuprate tx-regex fix)
- Run: `nice -n10 ./run_sim.sh --config test_configs/cuprate_exp_<arm>_300_8h.yaml --name <arm>`
- Delta: `python3 analysis/prop_delta.py <baseline>/daemon_logs <cuprate>/daemon_logs`

## Follow-ups

- **End-to-end tx-delay** (in progress): measure the pre-broadcast stem delay
  directly (tx-creation timestamp → first network broadcast) to quantify the hidden
  delta the §5 broadcast-phase measurement misses — expected to show a larger cuprate
  advantage (monerod 39s embargo vs cuprate ~1.46s stem).
- **Privacy / fingerprinting** (planned): can cuprate nodes be fingerprinted on the
  network (dandelion timing, version/handshake, peer-exchange cadence, message
  format, RPC surface), and does cuprate's faster/shorter dandelion stem weaken
  transaction-origin privacy? Both carry anonymity-set implications for a
  mixed-implementation Monero network.
- Quantify run-to-run variance (repeat each arm with different seeds).
- Push the cuprate fraction higher (all-cuprate relays) and to larger node counts.
