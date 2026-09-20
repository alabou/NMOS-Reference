// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Managing the local etcd member process.
//!
//! Scope, stated first because it is the thing most easily got wrong: a
//! registry supervises **exactly one** etcd process, its own local member. It
//! never starts, stops, reconfigures or removes any other member's etcd --
//! peers are reached only as gRPC clients -- and it never changes cluster
//! *membership*. Bootstrap and resizing are explicit operator actions; this
//! refuses to automate them.
//!
//! # The ownership rule
//!
//! **Stop what you started, never stop what you adopted.**
//!
//! | Situation | Starts it? | Stops it on exit? |
//! |---|---|---|
//! | Nothing on the configured port | yes | yes |
//! | etcd running, identity matches | no, adopts | **no** |
//! | etcd running, identity differs | refuses | n/a |
//!
//! Terminating a self-launched child is what stops a Ctrl-C'd development run
//! from orphaning a process that still holds the client port and the
//! data-directory lock. Never terminating an adopted one is what stops the
//! registry from killing a service-managed etcd out from under systemd. In
//! production the recommended shape is exactly that: etcd under systemd,
//! registry adopting it, so a registry restart costs one reconnect instead of
//! a member leave/rejoin with the election and catch-up that implies.
//!
//! # What is never done
//!
//! The legacy dRDS start scripts did three things on every single start:
//! `rm -rf` the data directory, `member remove` followed by `member add`, and
//! pinned an end-of-life binary. Each is a way to lose data on what is meant
//! to be a routine restart. None happens here: the data directory is reused
//! and never deleted, membership is never touched, and the binary's version is
//! checked rather than assumed.

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use nmos_cluster::ClusterLayout;
use tokio::process::{Child, Command};
use tokio::sync::Mutex;

use crate::channel::{Credentials, Endpoint, EtcdChannelPool, UnaryMethod};
use crate::generated::etcdserverpb as pb;

const STATUS: UnaryMethod = UnaryMethod::new("/etcdserverpb.Maintenance/Status");

/// The oldest etcd this design will run against.
///
/// It depends on `--watch-progress-notify-interval` being a stable flag and on
/// watch progress semantics settled in 3.4+; 3.5 still spells the flag
/// `--experimental-...`. Rather than support both spellings, require 3.6.
pub const MINIMUM_ETCD_VERSION: (u32, u32) = (3, 6);

/// Restart backoff.
///
/// Capped low enough that a member which crashed for a transient reason
/// rejoins inside one garbage-collection interval, and high enough that a
/// member crashing on every start does not spin.
const RESTART_BACKOFF_INITIAL: Duration = Duration::from_millis(500);
const RESTART_BACKOFF_MAX: Duration = Duration::from_secs(30);

/// How long to wait for a launched member to answer.
const DEFAULT_STARTUP_TIMEOUT: Duration = Duration::from_secs(30);

/// Whether this supervisor may stop the member it is talking to.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProcessOwnership {
    /// We started it, so we stop it on shutdown.
    Launched,
    /// It was already running and matched; someone else owns its lifetime.
    Adopted,
    /// `--etcdExternal`: no process management at all.
    External,
}

impl ProcessOwnership {
    /// The wire spelling, matching the Python enum's values.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Launched => "launched",
            Self::Adopted => "adopted",
            Self::External => "external",
        }
    }
}

/// Who a running etcd says it is.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MemberIdentity {
    /// The cluster it belongs to.
    pub cluster_id: u64,
    /// Its own member id.
    pub member_id: u64,
    /// Its configured name.
    pub name: String,
    /// The peer URLs it advertises.
    pub peer_urls: Vec<String>,
    /// Its version string.
    pub version: String,
}

/// The local etcd member cannot be brought up safely.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SupervisorError(pub String);

impl std::fmt::Display for SupervisorError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for SupervisorError {}

type Result<T> = std::result::Result<T, SupervisorError>;

/// Everything the supervisor needs to bring a member up.
#[derive(Debug, Clone)]
pub struct SupervisorConfig {
    /// The derived cluster.
    pub layout: ClusterLayout,
    /// The etcd executable.
    pub binary: String,
    /// The member's persistent data directory. **Never deleted.**
    pub data_dir: PathBuf,
    /// Whether this member bootstraps a new cluster.
    pub bootstrap: bool,
    /// Whether client and peer traffic are secured.
    pub tls: bool,
    /// The shared certificate serving all four roles.
    pub certificate: String,
    /// Its private key.
    pub key: String,
    /// Roots to trust. May be several; etcd takes one file.
    pub trusted_root_ca: Vec<String>,
    /// The shared SAN every member's certificate carries.
    pub certificate_name: String,
    /// CRL for client certificates.
    pub client_crl_file: String,
    /// CRL for peer certificates.
    pub peer_crl_file: String,
    /// How long a launched member has to answer.
    pub startup_timeout: Duration,
}

impl SupervisorConfig {
    /// A config with the default startup timeout.
    #[must_use]
    pub fn new(layout: ClusterLayout, binary: String, data_dir: PathBuf) -> Self {
        Self {
            layout,
            binary,
            data_dir,
            bootstrap: false,
            tls: true,
            certificate: String::new(),
            key: String::new(),
            trusted_root_ca: Vec::new(),
            certificate_name: String::new(),
            client_crl_file: String::new(),
            peer_crl_file: String::new(),
            startup_timeout: DEFAULT_STARTUP_TIMEOUT,
        }
    }
}

/// Owns the local etcd member process.
pub struct EtcdSupervisor {
    config: Mutex<SupervisorConfig>,
    ownership: Mutex<Option<ProcessOwnership>>,
    process: Mutex<Option<Child>>,
    monitor: Mutex<Option<tokio::task::JoinHandle<()>>>,
    stopping: Arc<std::sync::atomic::AtomicBool>,
    /// A trust store this supervisor wrote, to remove on shutdown.
    generated_ca: Mutex<Option<PathBuf>>,
}

impl std::fmt::Debug for EtcdSupervisor {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("EtcdSupervisor").finish_non_exhaustive()
    }
}

impl EtcdSupervisor {
    /// Build a supervisor. Nothing is started until [`Self::start`].
    #[must_use]
    pub fn new(config: SupervisorConfig) -> Self {
        Self {
            config: Mutex::new(config),
            ownership: Mutex::new(None),
            process: Mutex::new(None),
            monitor: Mutex::new(None),
            stopping: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            generated_ca: Mutex::new(None),
        }
    }

    /// Whether this supervisor started the member it is talking to.
    pub async fn owns_process(&self) -> bool {
        *self.ownership.lock().await == Some(ProcessOwnership::Launched)
    }

    /// How the member this supervisor is talking to came to be running.
    pub async fn ownership(&self) -> Option<ProcessOwnership> {
        *self.ownership.lock().await
    }

    /// Bring the local member up, or adopt one that is already running.
    ///
    /// # Errors
    ///
    /// `SupervisorError` when the member cannot be brought up safely -- which
    /// includes finding something that is not the configured member already
    /// listening on its port.
    pub async fn start(self: &Arc<Self>) -> Result<ProcessOwnership> {
        if cfg!(target_os = "windows") {
            return Err(SupervisorError(
                "the etcd supervisor is POSIX-only; on Windows run the \
                 registry with --etcdExternal against a cluster managed \
                 elsewhere"
                    .to_owned(),
            ));
        }

        self.validate_data_dir().await?;

        if let Some(existing) = self.probe().await? {
            self.verify_identity(&existing).await?;
            *self.ownership.lock().await = Some(ProcessOwnership::Adopted);
            tracing::info!(
                "etcd: adopted running member {} (cluster {:x}, version {}); \
                 this registry did not start it and will not stop it",
                existing.name,
                existing.cluster_id,
                existing.version,
            );
            return Ok(ProcessOwnership::Adopted);
        }

        self.launch().await?;
        *self.ownership.lock().await = Some(ProcessOwnership::Launched);
        Ok(ProcessOwnership::Launched)
    }

    /// Check the data directory, and decide bootstrap against it.
    ///
    /// The two rules here are the ones that keep a routine restart from
    /// becoming data loss:
    ///
    /// * `--etcdBootstrap` on a non-empty directory is refused. Bootstrapping
    ///   an existing member creates a *new* cluster whose data is the old
    ///   member's, which is how a cluster silently forks.
    /// * A missing or empty directory is **never** taken as "bootstrap me". It
    ///   means `initial-cluster-state=existing`, i.e. this member was added to
    ///   the cluster by an explicit membership operation and is now starting
    ///   for the first time. Inferring a new cluster from an absent directory
    ///   is how an operator recovering one dead member ends up with two
    ///   clusters.
    async fn validate_data_dir(&self) -> Result<()> {
        let config = self.config.lock().await;
        let data_dir = config.data_dir.clone();
        let bootstrap = config.bootstrap;
        drop(config);

        let Some(parent) = data_dir.parent() else {
            return Err(SupervisorError(format!(
                "data directory has no parent: {}",
                data_dir.display(),
            )));
        };
        if !parent.is_dir() {
            return Err(SupervisorError(format!(
                "data directory parent does not exist: {}",
                parent.display(),
            )));
        }

        let populated = data_dir.is_dir()
            && std::fs::read_dir(&data_dir)
                .map(|mut entries| entries.next().is_some())
                .unwrap_or(false);

        if bootstrap && populated {
            return Err(SupervisorError(format!(
                "--etcdBootstrap was given but {} is not empty. Bootstrapping \
                 an existing member forks the cluster. Remove the flag to \
                 start normally, or move the directory aside deliberately if \
                 you really are creating a new cluster.",
                data_dir.display(),
            )));
        }

        if data_dir.exists() {
            // Someone else's readable data directory is a security problem as
            // well as an ownership one: it holds every registered resource.
            set_owner_only(&data_dir)?;
        } else {
            std::fs::create_dir_all(&data_dir).map_err(|exc| {
                SupervisorError(format!("cannot create {}: {exc}", data_dir.display(),))
            })?;
            set_owner_only(&data_dir)?;
        }
        Ok(())
    }

    /// Look for an etcd already listening on the local client port.
    ///
    /// `None` when the port is closed, meaning we should launch. An error when
    /// the port is open but does not answer as an etcd we recognise -- never
    /// `None` in that case, because launching on top of an unrelated process
    /// is exactly the interference this must not cause.
    async fn probe(&self) -> Result<Option<MemberIdentity>> {
        let config = self.config.lock().await;
        let member = config.layout.local.clone();
        drop(config);

        if !port_is_open(&member.bind_address, member.client_port).await {
            return Ok(None);
        }

        let pool = self.probe_pool().await?;
        let status: pb::StatusResponse = pool
            .call(STATUS, pb::StatusRequest {}, None)
            .await
            .map_err(|exc| {
                SupervisorError(format!(
                    "something is listening on {} but does not answer as etcd: \
                     {exc}. Refusing to launch on top of it.",
                    member.client_target(),
                ))
            })?;

        let header = status.header.unwrap_or_default();
        // The member's own name and peer URLs come from the membership list,
        // which `Status` does not carry.
        let members: pb::MemberListResponse = pool
            .call(
                UnaryMethod::new("/etcdserverpb.Cluster/MemberList"),
                pb::MemberListRequest {
                    linearizable: false,
                },
                None,
            )
            .await
            .map_err(|exc| SupervisorError(format!("cannot list members: {exc}")))?;

        let me = members
            .members
            .into_iter()
            .find(|m| m.id == header.member_id)
            .ok_or_else(|| {
                SupervisorError("the running etcd does not list itself as a member".to_owned())
            })?;

        Ok(Some(MemberIdentity {
            cluster_id: header.cluster_id,
            member_id: header.member_id,
            name: me.name,
            peer_urls: me.peer_ur_ls,
            version: status.version,
        }))
    }

    /// Adopt only a member that is unmistakably the one we are configured as.
    ///
    /// Name and peer URL both have to match. Name alone is not enough: two
    /// deployments on one machine can easily share a member name while
    /// advertising different peer ports, and adopting the wrong one would make
    /// this registry serve another cluster's data.
    async fn verify_identity(&self, identity: &MemberIdentity) -> Result<()> {
        let config = self.config.lock().await;
        let expected = config.layout.local.clone();
        let tls = config.tls;
        drop(config);

        if identity.name != expected.name {
            return Err(SupervisorError(format!(
                "an etcd is running on {} but calls itself {:?}, not {:?}. \
                 Refusing to adopt a member belonging to a different \
                 configuration.",
                expected.client_target(),
                identity.name,
                expected.name,
            )));
        }

        let wanted_peer = expected.advertise_peer_url(tls);
        if !identity.peer_urls.contains(&wanted_peer) {
            return Err(SupervisorError(format!(
                "the running member {:?} advertises peer URLs {:?}, which do \
                 not include {wanted_peer}. Refusing to adopt it.",
                identity.name, identity.peer_urls,
            )));
        }

        require_supported_version(&identity.version)
    }

    /// The exact etcd command line, derived entirely from the layout.
    ///
    /// # Errors
    ///
    /// `SupervisorError` when TLS is on and the certificate set is incomplete.
    pub async fn build_argv(&self) -> Result<Vec<String>> {
        let config = self.config.lock().await;
        let member = &config.layout.local;
        let tls = config.tls;

        let mut argv = vec![
            config.binary.clone(),
            "--name".to_owned(),
            member.name.clone(),
            "--data-dir".to_owned(),
            config.data_dir.to_string_lossy().into_owned(),
            "--listen-client-urls".to_owned(),
            member.listen_client_url(tls),
            "--advertise-client-urls".to_owned(),
            member.advertise_client_url(tls),
            "--listen-peer-urls".to_owned(),
            member.listen_peer_url(tls),
            "--initial-advertise-peer-urls".to_owned(),
            member.advertise_peer_url(tls),
            "--initial-cluster".to_owned(),
            config.layout.initial_cluster(Some(tls)),
            "--initial-cluster-token".to_owned(),
            config.layout.token.clone(),
            "--initial-cluster-state".to_owned(),
            if config.bootstrap { "new" } else { "existing" }.to_owned(),
        ];

        if !tls {
            return Ok(argv);
        }

        if config.certificate.is_empty() || config.key.is_empty() {
            return Err(SupervisorError(
                "TLS is enabled but --etcdCertificate/--etcdKey were not \
                 supplied"
                    .to_owned(),
            ));
        }
        if config.trusted_root_ca.is_empty() {
            return Err(SupervisorError(
                "TLS is enabled but --etcdTrustedRootCA was not supplied".to_owned(),
            ));
        }

        let roots = config.trusted_root_ca.clone();
        let data_dir = config.data_dir.clone();
        let certificate = config.certificate.clone();
        let key = config.key.clone();
        let certificate_name = config.certificate_name.clone();
        let client_crl = config.client_crl_file.clone();
        let peer_crl = config.peer_crl_file.clone();
        drop(config);

        let ca = self.trusted_ca_file(&roots, &data_dir).await?;

        // One certificate serves all four roles -- client listener, peer
        // listener, outbound peer connection, and the registry's own client
        // connection -- which is why it carries both serverAuth and clientAuth.
        argv.extend([
            "--cert-file".to_owned(),
            certificate.clone(),
            "--key-file".to_owned(),
            key.clone(),
            "--trusted-ca-file".to_owned(),
            ca.clone(),
            "--client-cert-auth".to_owned(),
            "--peer-cert-file".to_owned(),
            certificate,
            "--peer-key-file".to_owned(),
            key,
            "--peer-trusted-ca-file".to_owned(),
            ca,
            "--peer-client-cert-auth".to_owned(),
            "--tls-min-version".to_owned(),
            "TLS1.2".to_owned(),
        ]);

        if !certificate_name.is_empty() {
            // The control that stops any device sharing the Product CA from
            // writing to the registry database: the CA alone is not enough,
            // the certificate must also carry the etcd SAN.
            argv.extend([
                "--client-cert-allowed-hostname".to_owned(),
                certificate_name.clone(),
                "--peer-cert-allowed-hostname".to_owned(),
                certificate_name,
            ]);
        }
        if !client_crl.is_empty() {
            argv.extend(["--client-crl-file".to_owned(), client_crl]);
        }
        if !peer_crl.is_empty() {
            argv.extend(["--peer-crl-file".to_owned(), peer_crl]);
        }
        Ok(argv)
    }

    /// One file for etcd, however many roots the registry was given.
    ///
    /// `--trusted-ca-file` and `--peer-trusted-ca-file` each take a *single*
    /// path, while `--etcdTrustedRootCA` is repeatable and the client trusts
    /// every root it is handed. Passing only the first would split the trust
    /// store in half: this member would reject peers and clients that the
    /// registry's own client channel, in the very same process, accepts -- and
    /// reject them with a certificate error naming a certificate that is
    /// perfectly valid.
    ///
    /// So several roots are concatenated into one file. That is what a PEM
    /// trust store is.
    ///
    /// The bundle is written *beside* the data directory rather than inside
    /// it. Inside, it would make an empty data directory non-empty and so trip
    /// the bootstrap refusal -- turning a first start into "the data directory
    /// is already initialised".
    async fn trusted_ca_file(&self, roots: &[String], data_dir: &Path) -> Result<String> {
        if let [only] = roots {
            return Ok(only.clone());
        }

        let name = data_dir
            .file_name()
            .map_or_else(|| "etcd".to_owned(), |n| n.to_string_lossy().into_owned());
        let bundle = data_dir.with_file_name(format!("{name}.trusted-roots.pem"));

        let mut combined = Vec::new();
        for path in roots {
            combined.extend(read_root(path)?);
        }
        if let Some(parent) = bundle.parent() {
            std::fs::create_dir_all(parent).map_err(|exc| {
                SupervisorError(format!("cannot create {}: {exc}", parent.display()))
            })?;
        }
        std::fs::write(&bundle, &combined).map_err(|exc| {
            SupervisorError(format!(
                "cannot write the combined trust store {}: {exc}",
                bundle.display(),
            ))
        })?;

        *self.generated_ca.lock().await = Some(bundle.clone());
        Ok(bundle.to_string_lossy().into_owned())
    }

    /// A short-lived pool aimed only at the local member.
    async fn probe_pool(&self) -> Result<crate::channel::SharedPool> {
        let config = self.config.lock().await;
        let member = config.layout.local.clone();
        let connector = if config.tls && !config.certificate.is_empty() && !config.key.is_empty() {
            Some(
                Credentials {
                    trusted_root_ca: config.trusted_root_ca.clone(),
                    certificate: config.certificate.clone(),
                    key: config.key.clone(),
                }
                .connector()
                .map_err(|exc| SupervisorError(exc.message().to_owned()))?,
            )
        } else {
            None
        };
        let name = config.certificate_name.clone();
        let tls = config.tls;
        drop(config);

        EtcdChannelPool::new(
            vec![Endpoint {
                target: member.client_target(),
                local: true,
            }],
            connector,
            tls.then_some(name),
            Duration::from_secs(2),
        )
        .map(Arc::new)
        .map_err(|exc| SupervisorError(exc.message().to_owned()))
    }

    /// Start etcd as a child process and wait for it to answer.
    async fn launch(self: &Arc<Self>) -> Result<()> {
        let argv = self.build_argv().await?;
        let config = self.config.lock().await;
        let name = config.layout.local.name.clone();
        let data_dir = config.data_dir.clone();
        let state = if config.bootstrap { "new" } else { "existing" };
        let binary = config.binary.clone();
        drop(config);

        tracing::info!(
            "etcd: launching member {name} (data-dir {}, cluster-state {state})",
            data_dir.display(),
        );
        tracing::debug!("etcd: {}", argv.join(" "));

        let child = spawn(&argv).map_err(|exc| {
            SupervisorError(format!(
                "cannot execute {binary:?}: {exc}. Install it with \
                 ./install-etcd.sh, or point --etcdBinary at one.",
            ))
        })?;
        *self.process.lock().await = Some(child);

        self.await_ready().await?;

        let me = Arc::clone(self);
        *self.monitor.lock().await = Some(tokio::spawn(async move {
            me.supervise().await;
        }));
        Ok(())
    }

    /// Restart the child if it exits, with bounded exponential backoff.
    ///
    /// The data directory is reused every time. Nothing is deleted and
    /// membership is never touched: a member that crashed still *is* a member,
    /// and re-adding it would be a membership change nobody asked for.
    async fn supervise(self: Arc<Self>) {
        let mut backoff = RESTART_BACKOFF_INITIAL;

        while !self.stopping.load(std::sync::atomic::Ordering::Relaxed) {
            // Polled rather than awaited, and the child stays in the slot.
            //
            // `child.wait()` would have to hold the lock across the await, or
            // take the child out of the slot to release it. The first
            // deadlocks a shutdown behind a member that is still running; the
            // second leaves `stop` with nothing to terminate, so a launched
            // member outlives the supervisor that started it. Measured: the
            // second is what the first version did, and two live-etcd tests
            // caught it.
            //
            // A quarter-second poll on a process that is expected to run for
            // weeks costs nothing worth counting.
            let code = loop {
                if self.stopping.load(std::sync::atomic::Ordering::Relaxed) {
                    return;
                }
                let exited = {
                    let mut slot = self.process.lock().await;
                    match slot.as_mut() {
                        None => return,
                        Some(child) => child.try_wait().ok().flatten(),
                    }
                };
                if let Some(status) = exited {
                    break status.code();
                }
                tokio::time::sleep(Duration::from_millis(250)).await;
            };

            if self.stopping.load(std::sync::atomic::Ordering::Relaxed) {
                return;
            }

            let name = self.config.lock().await.layout.local.name.clone();
            tracing::error!(
                "etcd: member {name} exited with status {code:?}; Registration \
                 is DEGRADED until it returns. Restarting in {:.1}s.",
                backoff.as_secs_f64(),
            );
            tokio::time::sleep(backoff).await;
            backoff = backoff.saturating_mul(2).min(RESTART_BACKOFF_MAX);

            // Always "existing" on a restart: the cluster already knows this
            // member, and re-bootstrapping would fork it.
            self.config.lock().await.bootstrap = false;

            match self.relaunch().await {
                Ok(()) => {
                    backoff = RESTART_BACKOFF_INITIAL;
                    tracing::info!("etcd: member {name} restarted");
                }
                Err(exc) => tracing::error!("etcd: restart failed: {exc}"),
            }
        }
    }

    /// Start the child again, without re-running the adoption decision.
    async fn relaunch(&self) -> Result<()> {
        let argv = self.build_argv().await?;
        let child =
            spawn(&argv).map_err(|exc| SupervisorError(format!("cannot execute etcd: {exc}")))?;
        *self.process.lock().await = Some(child);
        self.await_ready().await
    }

    /// Wait until the launched member answers, or fail with a reason.
    async fn await_ready(&self) -> Result<()> {
        let config = self.config.lock().await;
        let member = config.layout.local.clone();
        let timeout = config.startup_timeout;
        let state = if config.bootstrap { "new" } else { "existing" };
        drop(config);

        let deadline = tokio::time::Instant::now()
            .checked_add(timeout)
            .unwrap_or_else(tokio::time::Instant::now);
        let mut last: Option<String> = None;

        while tokio::time::Instant::now() < deadline {
            {
                let exited = {
                    let mut process = self.process.lock().await;
                    process
                        .as_mut()
                        .and_then(|child| child.try_wait().ok().flatten())
                };
                if let Some(status) = exited {
                    {
                        return Err(SupervisorError(format!(
                            "etcd exited with status {status} during startup. \
                             With --initial-cluster-state={state} this usually \
                             means the member set or the data directory \
                             disagrees with the cluster.",
                        )));
                    }
                }
            }

            if port_is_open(&member.bind_address, member.client_port).await {
                match self.probe_pool().await {
                    Ok(pool) => {
                        match pool
                            .call::<_, pb::StatusResponse>(STATUS, pb::StatusRequest {}, None)
                            .await
                        {
                            Ok(status) => {
                                require_supported_version(&status.version)?;
                                tracing::info!(
                                    "etcd: member {} is serving (version {})",
                                    member.name,
                                    status.version,
                                );
                                return Ok(());
                            }
                            Err(exc) => last = Some(exc.message().to_owned()),
                        }
                    }
                    Err(exc) => last = Some(exc.0),
                }
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }

        self.stop().await;
        Err(SupervisorError(format!(
            "etcd did not become ready within {:.0}s{}",
            timeout.as_secs_f64(),
            last.map_or_else(String::new, |detail| format!(": {detail}")),
        )))
    }

    /// Stop the member -- but only if we started it.
    ///
    /// An adopted member is left running: this supervisor did not start it, so
    /// something else owns its lifetime, and killing a service-managed etcd on
    /// registry shutdown would take the cluster down with the registry.
    pub async fn stop(&self) {
        self.stopping
            .store(true, std::sync::atomic::Ordering::Relaxed);

        let monitor = self.monitor.lock().await.take();
        if let Some(handle) = monitor {
            handle.abort();
            drop(handle.await);
        }

        let ownership = *self.ownership.lock().await;
        if ownership != Some(ProcessOwnership::Launched) {
            if ownership == Some(ProcessOwnership::Adopted) {
                let name = self.config.lock().await.layout.local.name.clone();
                tracing::info!("etcd: leaving adopted member {name} running");
            }
            return;
        }

        self.discard_generated_ca().await;

        let Some(mut child) = self.process.lock().await.take() else {
            return;
        };
        if matches!(child.try_wait(), Ok(Some(_))) {
            return;
        }

        let name = self.config.lock().await.layout.local.name.clone();
        tracing::info!("etcd: stopping member {name}");
        terminate(&mut child);
        match tokio::time::timeout(Duration::from_secs(15), child.wait()).await {
            Ok(_) => {}
            Err(_) => {
                tracing::warn!("etcd: member {name} did not exit on SIGTERM; killing it");
                drop(child.kill().await);
            }
        }
    }

    /// Remove a trust store this supervisor wrote, if it wrote one.
    ///
    /// Best effort: the file holds public certificates, so leaving one behind
    /// after an abrupt exit leaks nothing, and failing a shutdown over it
    /// would be worse than the litter.
    async fn discard_generated_ca(&self) {
        let bundle = self.generated_ca.lock().await.take();
        if let Some(path) = bundle
            && let Err(exc) = std::fs::remove_file(&path)
        {
            tracing::debug!("etcd: could not remove {}: {exc}", path.display());
        }
    }
}

/// Spawn a child with no shell.
///
/// The argv carries certificate paths and hostnames from configuration, and a
/// shell would give any of them the chance to be interpreted rather than
/// passed.
fn spawn(argv: &[String]) -> std::io::Result<Child> {
    let (program, rest) = argv
        .split_first()
        .ok_or_else(|| std::io::Error::other("empty argv"))?;
    Command::new(program)
        .args(rest)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .kill_on_drop(false)
        .spawn()
}

/// SIGTERM on POSIX; `kill` elsewhere.
///
/// etcd flushes and releases its data-directory lock on SIGTERM. A hard kill
/// leaves the lock behind, which the next start then has to break.
fn terminate(child: &mut Child) {
    #[cfg(unix)]
    {
        if let Some(pid) = child.id() {
            // SAFETY-free: `Command` is not used here, this is a plain signal
            // through the standard library's process handle where available.
            // `tokio::process::Child` exposes no `terminate`, so the signal is
            // sent with `kill(1)` rather than `libc`, keeping this crate's
            // `forbid(unsafe_code)` intact.
            drop(
                std::process::Command::new("kill")
                    .arg("-TERM")
                    .arg(pid.to_string())
                    .status(),
            );
        }
    }
    #[cfg(not(unix))]
    {
        let _ = child;
    }
}

/// Make a directory readable only by its owner.
fn set_owner_only(path: &Path) -> Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt as _;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700)).map_err(|exc| {
            SupervisorError(format!(
                "cannot secure the data directory {}: {exc}",
                path.display(),
            ))
        })
    }
    #[cfg(not(unix))]
    {
        let _ = path;
        Ok(())
    }
}

/// Read one trusted root, failing with the path rather than a bare error.
///
/// PEM files do not always end in a newline, and two roots concatenated
/// without one between them produce a single unparseable block: etcd would
/// then trust the first root only, which is the failure the bundling exists to
/// prevent.
fn read_root(path: &str) -> Result<Vec<u8>> {
    let mut content = std::fs::read(path).map_err(|exc| {
        SupervisorError(format!("cannot read --etcdTrustedRootCA {path:?}: {exc}"))
    })?;
    if !content.ends_with(b"\n") {
        content.push(b'\n');
    }
    Ok(content)
}

/// Whether something accepts TCP on `host:port`.
async fn port_is_open(host: &str, port: u16) -> bool {
    tokio::time::timeout(
        Duration::from_millis(500),
        tokio::net::TcpStream::connect((host, port)),
    )
    .await
    .is_ok_and(|result| result.is_ok())
}

/// Parse `"3.6.14"` into `(3, 6, 14)`, stopping at any suffix.
///
/// Pre-release builds label themselves `3.7.0-rc.1`. Parsing must stop at the
/// first component that is not purely numeric and return `(3, 7, 0)` --
/// continuing past it would splice the release-candidate number in as a fourth
/// version component.
///
/// # Errors
///
/// `SupervisorError` when nothing numeric could be read at all.
pub fn parse_etcd_version(version: &str) -> Result<Vec<u32>> {
    let mut parts: Vec<u32> = Vec::new();
    for chunk in version.split('.') {
        let digits: String = chunk.chars().take_while(char::is_ascii_digit).collect();
        if digits.is_empty() {
            break;
        }
        let Ok(value) = digits.parse::<u32>() else {
            break;
        };
        parts.push(value);
        if digits.len() != chunk.len() {
            // A suffix such as "0-rc" ends the version proper; whatever
            // follows belongs to the pre-release label, not to the version.
            break;
        }
    }
    if parts.is_empty() {
        return Err(SupervisorError(format!(
            "cannot parse etcd version {version:?}",
        )));
    }
    Ok(parts)
}

/// Refuse an etcd older than [`MINIMUM_ETCD_VERSION`].
///
/// Checked through `Maintenance.Status` rather than by parsing
/// `etcd --version` from a launched child, so the same gate applies in every
/// mode -- managed, adopted, and `--etcdExternal` -- instead of only where
/// this process happens to spawn the binary.
///
/// # Errors
///
/// `SupervisorError` naming the version found and the one required.
pub fn require_supported_version(version: &str) -> Result<()> {
    let parsed = parse_etcd_version(version)?;
    let major = parsed.first().copied().unwrap_or(0);
    let minor = parsed.get(1).copied().unwrap_or(0);
    if (major, minor) < MINIMUM_ETCD_VERSION {
        return Err(SupervisorError(format!(
            "etcd {version} is too old; {}.{} or later is required. This \
             design uses watch progress requests and the stable \
             --watch-progress-notify-interval flag, which 3.5 spells \
             --experimental-watch-progress-notify-interval.",
            MINIMUM_ETCD_VERSION.0, MINIMUM_ETCD_VERSION.1,
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_version_stops_at_a_prerelease_suffix() {
        // `3.7.0-rc.1` must be (3, 7, 0). Continuing past the suffix would
        // splice the release-candidate number in as a fourth component.
        assert_eq!(parse_etcd_version("3.6.14").unwrap(), vec![3, 6, 14]);
        assert_eq!(parse_etcd_version("3.7.0-rc.1").unwrap(), vec![3, 7, 0]);
        assert_eq!(parse_etcd_version("3.6").unwrap(), vec![3, 6]);
        assert_eq!(parse_etcd_version("4").unwrap(), vec![4]);
    }

    #[test]
    fn an_unparseable_version_is_refused_rather_than_guessed() {
        assert!(parse_etcd_version("").is_err());
        assert!(parse_etcd_version("unknown").is_err());
        assert!(parse_etcd_version("v3.6.0").is_err());
    }

    #[test]
    fn the_version_gate_is_on_major_and_minor_only() {
        // The patch level is irrelevant to the flags this design depends on.
        assert!(require_supported_version("3.6.0").is_ok());
        assert!(require_supported_version("3.6.14").is_ok());
        assert!(require_supported_version("3.7.0-rc.1").is_ok());
        assert!(require_supported_version("4.0.0").is_ok());

        // 3.5 spells the progress-notify flag `--experimental-...`, which is
        // the whole reason for the floor.
        let refusal = require_supported_version("3.5.9").unwrap_err();
        assert!(refusal.0.contains("too old"), "{}", refusal.0);
        assert!(
            refusal
                .0
                .contains("--experimental-watch-progress-notify-interval"),
            "the refusal must say WHY 3.5 is not enough: {}",
            refusal.0,
        );
        assert!(require_supported_version("2.9.9").is_err());
    }

    #[test]
    fn ownership_spells_itself_the_way_python_does() {
        assert_eq!(ProcessOwnership::Launched.as_str(), "launched");
        assert_eq!(ProcessOwnership::Adopted.as_str(), "adopted");
        assert_eq!(ProcessOwnership::External.as_str(), "external");
    }

    #[test]
    fn a_root_without_a_trailing_newline_gets_one() {
        // Two roots concatenated without a newline between them produce a
        // single unparseable block, and etcd then trusts the first only.
        let dir = std::env::temp_dir().join(format!("nmos-roots-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("root.pem");
        std::fs::write(&path, b"-----BEGIN CERTIFICATE-----").unwrap();
        let read = read_root(path.to_str().unwrap()).unwrap();
        assert!(read.ends_with(b"\n"));
        // And one that already ends in a newline is not given a second.
        std::fs::write(&path, b"x\n").unwrap();
        assert_eq!(read_root(path.to_str().unwrap()).unwrap(), b"x\n");
        drop(std::fs::remove_dir_all(&dir));
    }

    #[test]
    fn a_missing_root_names_the_path() {
        let error = read_root("/nonexistent/root.pem").unwrap_err();
        assert!(error.0.contains("/nonexistent/root.pem"), "{}", error.0);
        assert!(error.0.contains("--etcdTrustedRootCA"), "{}", error.0);
    }
}
