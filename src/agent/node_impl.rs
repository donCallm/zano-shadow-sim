//! Node-implementation abstraction.
//!
//! monerosim can launch more than one Monero *node implementation* (monerod
//! today; cuprate's `cuprated` next; a third later). This module separates
//! *what a node needs* (the impl-independent [`NodeLaunchSpec`]) from *how a
//! given daemon is invoked* (a [`NodeImplementation`] that renders the spec into
//! a concrete process — args plus any config files to materialize).
//!
//! Design/rationale: `docs/20260723_multi_node_type_architecture.md`.
//!
//! ## Staging
//! This is the P1 core: [`MonerodImpl`] reproduces the historical monerod
//! argument construction **byte-for-byte** (guarded by the `tests/golden/*`
//! snapshot tests). To keep that guarantee the spec currently carries the
//! already-formatted monerod peer-arg strings in [`NodeLaunchSpec::peer_args`]
//! rather than logical peers; those become a logical `PeerRef` list in P2 when a
//! second implementation ([`CupratedImpl`], not yet added) needs to render peers
//! its own way. Likewise `role`/`network` fields are added when an impl other
//! than monerod needs them.

use crate::config::OptionValue;
use crate::utils::options::options_to_args;
use std::collections::BTreeMap;

/// The implementation-independent description of a node process to launch.
///
/// Built once per agent from values the orchestrator already computes, then
/// handed to a [`NodeImplementation`] to render. Borrows its inputs; lives only
/// for the duration of a render call.
pub struct NodeLaunchSpec<'a> {
    /// The node's data directory (already resolved, e.g. `<root>/monero-<id>`).
    pub data_dir: String,
    /// The agent's own IP address (RPC/P2P bind address).
    pub agent_ip: &'a str,
    /// RPC bind port.
    pub rpc_port: u16,
    /// P2P bind port.
    pub p2p_port: u16,
    /// Convenience thread count (0 = unset). Maps to impl-specific thread flags.
    pub process_threads: u32,
    /// Whether a DNS server is present in the sim (gates DNS/seed flags).
    pub enable_dns_server: bool,
    /// Whether this node is a miner (affects seed-node handling).
    pub is_miner: bool,
    /// Merged daemon options (defaults + per-agent + injected floors) — already
    /// assembled by the caller.
    pub daemon_options: &'a BTreeMap<String, OptionValue>,
    /// Pre-formatted, implementation-specific peer/connection args, in order.
    ///
    /// STAGED: monerod flag strings (`--seed-node=…`, `--add-priority-node=…`)
    /// today; becomes a logical peer list in P2.
    pub peer_args: &'a [String],
}

/// What an implementation *can* do — used to gate role assignment (e.g. never
/// assign a miner/wallet role to an impl that can't) and to fail generation
/// early on an unsupported combination.
#[derive(Debug, Clone, Copy)]
pub struct NodeCaps {
    pub can_mine: bool,
    pub can_wallet: bool,
    /// Supports monerosim's regtest/fixed-difficulty fakechain network.
    pub supports_regtest: bool,
    /// Can be pinned to an explicit peer set.
    pub peer_pinning: bool,
}

/// A config file an implementation wants written next to the node before launch.
#[derive(Debug, Clone)]
pub struct ConfigFile {
    /// Path relative to the node's data directory (e.g. `Cuprated.toml`).
    pub rel_path: String,
    pub contents: String,
}

/// The rendered launch: process args plus any config files to materialize.
#[derive(Debug, Clone, Default)]
pub struct RenderedLaunch {
    pub args: Vec<String>,
    pub config_files: Vec<ConfigFile>,
}

/// A launchable Monero node implementation.
pub trait NodeImplementation {
    /// Stable identifier used in configs/logs (`"monerod"`, `"cuprated"`).
    fn id(&self) -> &'static str;
    /// Default binary shorthand (resolved to `~/.monerosim/bin/<name>`).
    fn default_binary(&self) -> &'static str;
    /// Capability set (see [`NodeCaps`]).
    fn capabilities(&self) -> NodeCaps;
    /// Refuse an unsupported spec (e.g. an impl that can't do regtest). Returns a
    /// human-readable reason on rejection.
    fn preflight(&self, spec: &NodeLaunchSpec) -> Result<(), String>;
    /// Render the spec into concrete process args (+ optional config files).
    /// `phase_args` are per-launch extra args (upgrade phases / custom args).
    fn render(&self, spec: &NodeLaunchSpec, phase_args: Option<&Vec<String>>) -> RenderedLaunch;
}

/// The stock `monerod` implementation.
pub struct MonerodImpl;

impl NodeImplementation for MonerodImpl {
    fn id(&self) -> &'static str {
        "monerod"
    }

    fn default_binary(&self) -> &'static str {
        "monerod"
    }

    fn capabilities(&self) -> NodeCaps {
        NodeCaps {
            can_mine: true,
            can_wallet: true,
            supports_regtest: true,
            peer_pinning: true,
        }
    }

    fn preflight(&self, _spec: &NodeLaunchSpec) -> Result<(), String> {
        Ok(())
    }

    fn render(&self, spec: &NodeLaunchSpec, phase_args: Option<&Vec<String>>) -> RenderedLaunch {
        // NOTE: this reproduces the historical `build_daemon_args_base` closure
        // verbatim; the `tests/golden/*` snapshots enforce byte-identical output.
        // Do not reorder or reformat without regenerating the goldens.
        let data_dir = &spec.data_dir;
        let mut args = vec![
            format!("--data-dir={}", data_dir),
            format!("--log-file={}/bitmonero.log", data_dir),
            "--regtest".to_string(),
            "--keep-fakechain".to_string(),
        ];

        // process_threads flags, unless overridden in the merged options.
        if spec.process_threads > 0 {
            if !spec.daemon_options.contains_key("prep-blocks-threads") {
                args.push(format!("--prep-blocks-threads={}", spec.process_threads));
            }
            if !spec.daemon_options.contains_key("max-concurrency") {
                args.push(format!("--max-concurrency={}", spec.process_threads));
            }
        }

        // Configurable options (merged daemon_defaults + per-agent daemon_options).
        args.extend(options_to_args(spec.daemon_options));

        // Required network binding flags (agent-specific values).
        args.extend(vec![
            format!("--rpc-bind-ip={}", spec.agent_ip),
            format!("--rpc-bind-port={}", spec.rpc_port),
            "--confirm-external-bind".to_string(),
            "--rpc-access-control-origins=*".to_string(),
            format!("--p2p-bind-ip={}", spec.agent_ip),
            format!("--p2p-bind-port={}", spec.p2p_port),
        ]);

        // DNS and seed-node settings.
        if !spec.enable_dns_server {
            args.push("--disable-dns-checkpoints".to_string());
        }
        if spec.is_miner && !spec.enable_dns_server {
            args.push("--disable-seed-nodes".to_string());
        }

        // Peer/connection args (pre-formatted by the caller to preserve the
        // exact historical ordering and mode logic).
        args.extend(spec.peer_args.iter().cloned());

        // Phase-specific / custom args last.
        if let Some(custom_args) = phase_args {
            for arg in custom_args {
                args.push(arg.clone());
            }
        }

        RenderedLaunch {
            args,
            config_files: Vec::new(),
        }
    }
}
