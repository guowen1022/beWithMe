# Deploying beWithMe to Alibaba Cloud

Companion to `CLAUDE.md` (operator's manual) and `ARCHITECTURE.md` (the layer model).
This file covers **shipping**: what runs where, how images are built, and what has to
change before the backend faces the public internet.

---

## 0. STOP -- set the auth mode before exposing anything publicly

The original model was a full authentication bypass on a public IP: `GET /api/users`
published every user UUID anonymously, and auth only checked that a UUID *existed*, so
`X-User-Id` was an unverified assertion with the values handed out on request.

**That is fixed** -- see [`docs/SECURITY.md`](./SECURITY.md) for the full writeup. But
the fix ships **off by default**, because turning it on requires keys only you can
generate. The default (`BEWITHME_AUTH_MODE=legacy`) reproduces the old behaviour exactly
so nothing breaks locally.

**Before the shell gets a public address, set:**

```bash
BEWITHME_AUTH_MODE=strict
BEWITHME_SECRET_KEY=<python -c "from infra.session_token import generate_secret_key as g; print(g())">
BEWITHME_ACCESS_KEY=<python -c "import secrets; print(secrets.token_urlsafe(32))">
BEWITHME_CORS_ORIGINS=https://your-frontend-domain
BEWITHME_DEBUG=0
```

and give the clients the same access key (`NEXT_PUBLIC_BEWITHME_ACCESS_KEY` for
web/desktop, `EXPO_PUBLIC_BEWITHME_ACCESS_KEY` for mobile). In strict mode the shell
**refuses to boot** without the keys rather than serving something nobody can log into.

Two constraints no auth mode removes:

- **Only the shell may be public.** Sidecars `+1`..`+8` trust the forwarded `X-User-Id`
  unconditionally (invariant 9). Keep them on the private network.
- **Terminate TLS in front of the shell.** Tokens are bearer credentials.

One honest limitation: `BEWITHME_ACCESS_KEY` is a single shared secret for the whole
deployment, not a per-user password. It is what lets the pick-your-name screen keep
working with no login form. Right for a personal assistant or one household; **not**
multi-tenant auth. `docs/SECURITY.md` section 4 sketches the per-user upgrade.

If you would rather not think about any of this: **deploy into a VPC and reach it over
VPN**. Zero exposure, and for a single-user assistant it is the sane default.

---

## 1. What actually ships

Not everything in the repo is a cloud workload.

| component | where it goes | note |
|---|---|---|
| 8 Python sidecars (`services/`) | Alibaba Cloud | the actual deploy target |
| PostgreSQL + pgvector | **RDS PostgreSQL** | managed; enable the `vector` extension |
| Ollama (`nomic-embed-text`) | ECS/ACK workload w/ volume | embeddings; needs the model pulled once |
| Next.js frontend (`frontend/`) | OSS + CDN, or alongside the shell | static build output |
| **Electron desktop (`desktop/`)** | **not deployed** | a *client*. CI builds installers; users download them. |
| Model artifacts (Whisper/Kokoro/EOU) | OSS bucket -> mounted at `/models` | large, not in git, licensed separately |

The desktop app is the easiest thing to get wrong: it is distributed, not hosted. Its
pipeline is "build a signed installer on tag and attach it to a GitHub Release," which is
a separate workflow from anything below.

### Region

Keep everything in a **mainland-China region** (`cn-hangzhou`, `cn-beijing`). Every model
provider the app calls -- DeepSeek, MiniMax, Doubao via Volces Ark -- is China-based.
Deploying to Singapore or Frankfurt adds a cross-border round trip to the hottest path in
the system (`infra/model/llm.py` streaming) for no benefit.

---

## 2. The image model -- 2 images, not 8

Every sidecar is `uvicorn services.<name>.main:app` over the *same* source tree. Building
8 images would be 8x the build time and registry storage for identical layers.

So: **two images, each running any of its services via a different command.** The split
is system dependencies, not Python code (`docker/Dockerfile`):

| target | sidecars | extra system deps |
|---|---|---|
| `core` | shell (+0), persona (+1), knowledge (+2), maestro (+6), tuning (+8) | none -- slim |
| `media` | transcribe (+3), speak (+4), browser (+5) | `ffmpeg`, Playwright Chromium |

```bash
docker build -f docker/Dockerfile --target core  -t bewithme-core:dev  .
docker build -f docker/Dockerfile --target media -t bewithme-media:dev .
```

Model artifacts are **not** baked in -- they are large and separately licensed. Mount them
at `/models` and point `WHISPER_MODEL_PATH` / `KOKORO_*` / `EOU_MODEL_PATH` there.

### The seam that makes containers work

`infra/topology.py:upstream_url()` checks `<NAME>_SERVICE_URL` before computing
`host:port`. That single override is why the topology containerizes with **zero source
changes** -- each sidecar finds its peers by DNS name instead of `localhost`:

```
PERSONA_SERVICE_URL=http://persona:8001
KNOWLEDGE_SERVICE_URL=http://knowledge:8002
...
```

---

## 3. ECS + Docker Compose -- the chosen path

One VM running the whole topology. It matches the app's own trust model, costs roughly
an order of magnitude less than a managed cluster, and the images are identical to the
ones ACK would run -- so moving to ACK later re-uses everything except the manifests.

Size it for the media sidecars, which dominate: Whisper + Kokoro + Chromium want
**~8 vCPU / 16 GB**. `ecs.g7.2xlarge` or similar, with a data disk for `/opt/bewithme`.

### 3.1 Local / first run

```bash
cp .env.example .env          # fill in DEEPSEEK_API_KEY, DOUBAO_API_KEY, ...
docker compose up -d postgres ollama
docker compose exec ollama ollama pull nomic-embed-text
docker compose run --rm init-db
docker compose up -d
```

### 3.2 One-time ECS setup

```bash
# On the instance
sudo mkdir -p /opt/bewithme && sudo chown "$USER" /opt/bewithme
git clone https://github.com/guowen1022/beWithMe.git /opt/bewithme
cd /opt/bewithme

cp .env.example .env          # real provider keys + strict-mode auth (section 0)

cat > .deploy-env <<'EOF'
ACR_REGISTRY=registry.cn-hangzhou.aliyuncs.com
ACR_NAMESPACE=bewithme
EOF

# Model artifacts -- not in git, not in the image. Pull from your OSS bucket.
mkdir -p models && ossutil cp -r oss://<your-bucket>/models/ models/

docker compose up -d postgres ollama
docker compose exec ollama ollama pull nomic-embed-text
docker compose run --rm init-db
```

Give the instance a RAM role with `AliyunContainerRegistryReadOnlyAccess` so it can pull
from ACR without any credential on disk.

### 3.3 The pipeline

`.github/workflows/cd.yml` runs on `workflow_run` after CI:

```
CI passes on main
  -> guard      pin the exact commit CI validated (not head-of-main)
  -> build      OIDC -> RAM role -> ACR login -> push core + media as sha-<12>
  -> deploy     OIDC -> Cloud Assistant RunCommand -> scripts/deploy-ecs.sh
```

Two things worth calling out:

- **No stored AccessKey.** GitHub OIDC exchanges for a short-lived STS credential,
  scoped by a RAM condition to this repo on this branch.
- **No inbound SSH.** Cloud Assistant `RunCommand` executes the deploy script through the
  instance agent, so port 22 never needs to face the internet and no private key lives in
  GitHub.

`scripts/deploy-ecs.sh` is health-gated: it pulls, restarts, then polls `/api/health`. If
the new tag does not come up it **automatically restores the previous one** and exits
non-zero, so a bad release does not leave the box down.

```bash
./scripts/deploy-ecs.sh sha-a1b2c3d4e5f6   # deploy a specific tag
./scripts/deploy-ecs.sh --rollback         # previous known-good tag
./scripts/deploy-ecs.sh --current          # what is running
```

Rollback is also a button: **Actions -> CD -> Run workflow -> rollback**.

The `deploy` job targets a `production` GitHub Environment -- add a required reviewer
there if you want deploys to pause for approval.

### 3.4 What `docker-compose.prod.yml` changes

The overlay applied on the server:

- images come from ACR instead of a local `build:` (and the `build:` blocks are removed,
  so a stray `--build` cannot rebuild on the box)
- the shell binds **`127.0.0.1:8000`**, not `0.0.0.0` -- put nginx/Caddy in front for TLS.
  Tokens are bearer credentials and must not cross the wire in cleartext.
- log rotation (`max-size: 10m`, 3 files). The default json-file driver is unbounded and
  will eventually fill the system disk.

### 3.5 One-time Alibaba Cloud setup

1. **ACR** -- create namespace `bewithme` and two repos: `bewithme-core`, `bewithme-media`.
2. **RAM OIDC provider** -- issuer `https://token.actions.githubusercontent.com`,
   audience `sts.aliyuncs.com`.
3. **RAM role** `gha-bewithme-deployer` trusting that provider, with the condition
   `oidc:sub StringEquals repo:guowen1022/beWithMe:ref:refs/heads/main` -- this is what
   stops any other repo assuming it. Permissions: ACR push +
   `ecs:RunCommand` / `ecs:DescribeInvocationResults` on the one instance.
4. Fill the `env:` block at the top of `.github/workflows/cd.yml` with your region,
   account id, and `ECS_INSTANCE_ID`.

## 4. Path B -- ACK (Kubernetes)

Worth it when you need multi-replica, autoscaling, or independent rollout per sidecar.
For a single-user assistant it is mostly cost and operational surface, so choose it
deliberately.

Shape:

- One `Deployment` per sidecar (8), each running the shared image with its own command.
- `Service` per sidecar; only the shell behind an `Ingress` + SLB.
- `transcribe`/`speak`/`browser` get bigger resource requests and a shared read-only
  `/models` PVC (or an OSS-backed CSI volume).
- Per-user state under `data/` (see `infra/user_data.py`) needs a **`ReadWriteMany`** PVC
  -- Alibaba **NAS**, not a block disk -- if more than one pod writes it.
- Postgres -> **RDS**, not a pod. Ollama -> its own Deployment with a model volume.

> The `data/` volume is the subtle one. `infra/user_data.py` registers on-disk roots
> (`data/sessions/`, note caches, browser profile). Several sidecars touch them, so a
> `ReadWriteOnce` block disk will pin those pods to one node or fail to mount. Use NAS.

These manifests are **not written** -- ECS is the chosen path (section 3). If you later
outgrow one box, the images and the CI half carry over unchanged; only the runtime
description is new work.

---

## 5. CI (`.github/workflows/ci.yml`)

Runs on every PR and push to main. No cloud account, no secrets.

| job | count | what it does |
|---|---|---|
| `arch` | 1 | `scripts/check_arch.py` -- enforces `ARCHITECTURE.md` section 9 invariants 1-4 |
| `service` | 8 | one per sidecar: installs its dependencies, then `scripts/smoke_service.py` imports it and asserts it mounts an app |
| `unit` | 1 | `pytest tests/unit` + the section 2.6 user-data-map guard |
| `client` | 3 | web (`next build`), desktop (`tsc`), mobile (Expo/RN typecheck) |

The per-service matrix earns its keep immediately: it caught `networkx` missing from
`requirements.txt` entirely, which meant the `knowledge` sidecar could not boot from a
clean install. Coverage is uneven (`shell`, `speak` and `browser` have no unit tests at
all), so for those the boot check is the only gate.

`scripts/check_arch.py` is an AST walker, not the `grep` from section 10, specifically so it
honors invariant 3: a `if TYPE_CHECKING:` import of `silicon_brain` from `persona/` is
**allowed** and must not fail the build, while a runtime one must. Grep cannot tell those
apart.

CI seeds **fake** `DEEPSEEK_*` / `DOUBAO_*` values, because `infra/model/llm.py:45` and
`infra/model/vision/__init__.py:30` raise `RuntimeError` at *import* time when the active
provider's vars are unset. Tests never make a real call; the values exist only so
`import infra...` succeeds.

`tests/e2e/` is deliberately **not** in CI -- it needs the full running topology plus
Postgres and Ollama. It belongs in a separate workflow built on `docker-compose.yml`.

---

## Next step

The pipeline is written. What remains is account-specific configuration that only you
can do:

1. **Create the Alibaba resources** -- section 3.5 (ACR repos, RAM OIDC provider, RAM role,
   ECS instance).
2. **Fill the `env:` block** at the top of `.github/workflows/cd.yml` -- region, account
   id, ACR namespace, `ECS_INSTANCE_ID`. Every value there is currently a placeholder.
3. **Bootstrap the box** -- section 3.2.
4. **Set strict auth before it faces the internet** -- section 0. The pipeline will happily
   deploy an open backend; nothing here decides that for you.

Nothing about `LLM_PROVIDER`/`VISION_PROVIDER` changes: the app calls DeepSeek and Doubao
as external APIs from the ECS box, so keep the deployment in a mainland-China region
(section 1).
