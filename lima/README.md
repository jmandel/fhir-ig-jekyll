# Lima VM `igbuild`: parallel IG Publisher builds

Docker Desktop's VM is capped at ~7.7 GiB, so builds there run one at a time. `igbuild` is a separate
Lima VM (Ubuntu 24.04, rootful Docker Engine) sized for many builds at once. Docker Desktop is
untouched; both daemons can run side by side.

| | |
|---|---|
| Config | `lima/igbuild.yaml` (self-contained; derived from Lima's `docker-rootful` template) |
| VM | vmType `vz`, aarch64, 16 CPUs, 96 GiB RAM, 300 GiB disk (sparse), Ubuntu 24.04 |
| Mount | `~/fhir-ig-jekyll` → same path in the VM, **writable**, virtiofs. Nothing else from `$HOME` is mounted. |
| Docker socket on host | `~/.lima/igbuild/sock/docker.sock` (Lima `portForwards` of `/var/run/docker.sock`) |
| Docker context | `igbuild` (not made current; Docker Desktop's `desktop-linux` stays the default) |

## Commands

```bash
# create + start (first time; ~1 min, installs Docker via get.docker.com)
limactl start --tty=false --name=igbuild ~/fhir-ig-jekyll/lima/igbuild.yaml
# host-side docker context (once)
docker context create igbuild --docker "host=unix://$HOME/.lima/igbuild/sock/docker.sock"

limactl stop igbuild          # stop (frees the 96 GiB)
limactl start igbuild         # start again (config is kept in ~/.lima/igbuild)
limactl shell igbuild         # shell in the VM
limactl delete igbuild        # remove it entirely (then re-create with the first command)

docker --context igbuild info   # expect CPUs: 16, Total Memory: ~94 GiB
```

Changing CPU/memory/disk later: `limactl edit igbuild --cpus N --memory M` (VM must be stopped), or
edit `igbuild.yaml`, `limactl delete igbuild` and re-create.

## Image

The image must exist on this daemon too. **Build it on the same base digest as Docker Desktop**:
`ghcr.io/fhir/ig-publisher-localdev:latest` moved on 2026-11-19 (new `:latest` = Debian trixie,
Ruby 3.4.13, kramdown 2.6.1), while our reference (D6) is Ruby 3.2.3 / kramdown 2.5.2 / Jekyll 4.4.1.
So pin the base by digest and tag it `:latest` locally before building:

```bash
D=sha256:39509f4138197f0b93a99ed3ae00e6dce2bbd76df1a2e4e2bd6dec4180dc34b5   # = Docker Desktop's copy
docker --context igbuild pull ghcr.io/fhir/ig-publisher-localdev@$D
docker --context igbuild tag  ghcr.io/fhir/ig-publisher-localdev@$D ghcr.io/fhir/ig-publisher-localdev:latest
docker --context igbuild build --pull=false -t ig-publisher-localdev-ca ~/fhir-ig-jekyll/docker
```

Verified identical to Docker Desktop's image (ruby -v, gem versions, OS). Check with
`docker --context X run --rm --entrypoint bash ig-publisher-localdev-ca -c 'ruby -v; gem list | grep -E "^(jekyll|liquid|kramdown|rouge)"'`.

## Using it from the scripts

All docker-using scripts honor the docker CLI's own `DOCKER_CONTEXT` / `DOCKER_HOST` env vars; unset =
current context (Docker Desktop), exactly as before.

```bash
DOCKER_CONTEXT=igbuild scripts/golden-build.sh <repo-name> [6g]
DOCKER_CONTEXT=igbuild scripts/golden-batch.sh -j 8 -m -Xmx8g -f names.txt
DOCKER_CONTEXT=igbuild shim/build.sh liquid        # shim/run.sh, shim/oracle/oracle.sh likewise
# or: DOCKER_HOST=unix://$HOME/.lima/igbuild/sock/docker.sock ...
```

`golden-batch.sh`: `-j N` parallel jobs (default 4), `-m HEAP` per-build heap (default `-Xmx6g`),
`-f FILE` names one per line, `-i` isolated package caches (below). Per-build console in
`work/logs/<name>.log`, summary in `work/batch-summary.tsv` (name, valid, full_build_seconds,
jekyll_seconds, exit). Sizing: a build uses roughly its heap + ~1 GiB, so `-j 10 -m 7g` fits in 96 GiB;
US Core-sized IGs want `-m 10g` or more.

## TLS-inspecting proxy

The VM trusts `docker/extra-ca/proxy-root.pem` in the OS store via Lima's `caCerts.files` (cloud-init
`ca_certs`, applied before provisioning), with a provision-time `update-ca-certificates` fallback. That
covers apt, curl/get.docker.com, and dockerd's registry pulls (ghcr.io). The image adds the same CA
for Java/Node (docker/Dockerfile). Host-side `limactl` downloads use the macOS keychain, which already
trusts it.

## Gotchas

1. **virtiofs holds one host fd per guest-cached inode** (Apple Virtualization.framework). With 96 GiB
   the guest never evicts inodes, so the VM process accumulates host fds until the Mac hits
   `kern.maxfiles` (491,520). The first 3-way smoke test died at ~1 min with
   `Too many open files in system` (ENFILE) in all three builds. That limit is **system-wide**, so it
   can also break Docker Desktop builds and other host processes. Fix (in `igbuild.yaml`): a guest
   systemd timer `drop-inode-cache.timer` runs `echo 2 > /proc/sys/vm/drop_caches` every 10 s. With
   it, the igbuild VM peaked at ~31k host fds over a 3-way run. Watch it with
   `lsof -p $(pgrep -f 'Virtualization.VirtualMachine' | tail -1) | wc -l` and `sysctl kern.num_files`.
   Docker Desktop's VM process has the same behavior (it was at ~195k fds during the US Core build;
   per-process limit 245,760), and we can't change it. More headroom would need
   `sudo sysctl kern.maxfiles=... kern.maxfilesperproc=...` on the host (user action, not done).
2. **Package cache `work/fhir-cache` sharing.** The publisher locks per package with `.lock` files plus
   fcntl locks. Tested: fcntl locks *are* enforced between containers in the VM over virtiofs (the guest
   kernel handles them), so concurrent builds **within one daemon** sharing the cache are safe (default
   mode). Locks do **not** cross VMs: a Docker Desktop build and igbuild builds downloading the same
   package at the same moment could race. When Desktop is also building against the cache, use
   `golden-batch.sh -i`. Each job then gets an APFS copy-on-write clone (`cp -c`) under
   `work/igs/<name>.fhir-cache`, and new complete package dirs are merged back afterwards (never
   overwriting). Cost: roughly 1–1.5 min extra per job to clone and delete the 5.7 GB / many-file cache.
3. `ghcr.io/fhir/ig-publisher-localdev:latest` drifts (see Image). Don't `docker build --pull` blindly.
4. Files written by containers (which run as root) show up on the host owned by your user (virtiofs
   maps them), same as with Docker Desktop.
5. Lima re-runs `provision` scripts at every boot. They're idempotent.
