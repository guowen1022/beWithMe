# Deploying beWithMe to Alibaba Cloud

Companion to `CLAUDE.md` (operator's manual) and `ARCHITECTURE.md` (the layer model).
This file covers **shipping**: what runs where, how images are built, and what has to
change before the backend faces the public internet.

---

## 0. STOP -- read this before exposing anything publicly

**The current auth model is a full authentication bypass the moment the shell has a
public IP.** This is not a hardening nit; it is total.

`ARCHITECTURE.md` section 6 already says so:

> This trust model is appropriate for single-machine dev and small private deploys.
> For internet-facing deployments, swap shell-side cookie verification for a signed-token
> scheme (out of scope until needed).

Concretely, the bypass is a two-request chain:

| step | request | why it works |
|---|---|---|
| 1 | `GET /api/users` | Public, no auth -- `services/shell/auth.py:PUBLIC`. Returns **every user's UUID** (`services/knowledge/routers/users.py:38`). |
| 2 | any request with `X-User-Id: <that uuid>` | Auth = "does this UUID exist" (`services/knowledge/routers/auth.py:31`). It does. Request is forwarded. |

There is no secret anywhere in the chain. The `X-User-Id` header is an *assertion of
identity*, not proof of it, and step 1 hands out the values to assert. Any sidecar
reached directly (not through the shell) skips even that check.

**This is fine today** -- everything binds to localhost on one machine, and that is the
deployment the design targets. It stops being fine the instant a public SLB points at
the shell.

### What has to happen first

Pick one, in rough order of effort:

1. **Don't expose it publicly.** Deploy into a VPC and reach it over VPN / Alibaba Cloud
   private access. Zero code change, and honestly the right answer for a single-user
   personal assistant. **Recommended.**
2. **Put a real identity gateway in front.** Alibaba API Gateway or an nginx ingress with
   OIDC/OAuth2-proxy terminating a real login, injecting `X-User-Id` only after
   authenticating, and stripping any client-supplied `X-User-Id`. No app change, but the
   gateway must be airtight -- a single path that forwards the raw header reopens the hole.
3. **Implement the signed-token scheme** section 6 anticipates. Shell verifies a signature
   (JWT/PASETO) instead of existence. This is the durable fix and touches
   `services/shell/auth.py` plus the frontend's login flow. Also remove `GET /api/users`
   from `PUBLIC`, or scope it down -- enumerating users should not be anonymous.

Nothing in this repo's CI/CD changes that decision; the pipeline will happily deploy an
open backend. **Decide this before step 3 below.**

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

## 3. Path A -- ECS + Docker Compose (recommended to start)

`docker-compose.yml` brings up the whole topology: 8 sidecars, Postgres/pgvector, Ollama.
Only the shell publishes a port, which matches the section 6 trust model exactly.

```bash
cp .env.example .env          # fill in DEEPSEEK_API_KEY, DOUBAO_API_KEY, ...
docker compose up -d postgres ollama
docker compose exec ollama ollama pull nomic-embed-text
docker compose run --rm init-db
docker compose up -d
```

On an ECS instance this is the entire deployment. Deploy = pull new images, `up -d`.

**Why start here:** it is one VM, it matches the app's own trust model, it costs roughly
an order of magnitude less than a managed cluster, and the images are identical to the
ones ACK would run. Moving to ACK later re-uses everything except the manifests.

Size it for the media sidecars, which dominate: Whisper + Kokoro + Chromium want
**~8 vCPU / 16 GB** to be comfortable. `ecs.g7.2xlarge` or similar.

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

I have **not** written these manifests yet -- see "Next step" below.

## 5. The CD pipeline

Once a target is chosen, the delivery half:

```
push to main
  -> GitHub Actions: OIDC -> Alibaba RAM role (no static AccessKey stored)
  -> docker build core + media, push to ACR tagged with the git SHA
  -> Path A: SSH/Ops-orchestrated pull + `up -d` on the ECS box
     Path B: bump the tag in a manifests dir; Argo CD in-cluster syncs it
```

The OIDC part matters: GitHub gets a short-lived STS credential scoped by a RAM condition
to *this repo on main*, instead of a long-lived AccessKey pasted into repo secrets.

---

## 6. CI (already wired -- `.github/workflows/ci.yml`)

Runs on every PR and push to main. No cloud account, no secrets.

| job | what it does |
|---|---|
| `arch` | `scripts/check_arch.py` -- enforces `ARCHITECTURE.md` section 9 invariants 1-4 |
| `unit` | `pytest tests/unit` + the section 2.6 user-data-map guard |
| `frontend` | `npm run lint` + `npm run build` |

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

Decide two things and the rest follows:

1. **Public or private?** (section 0) -- private VPC is the low-effort correct answer; anything
   public needs the auth work first.
2. **ECS or ACK?** (sections 3/4) -- ECS unless you specifically need per-sidecar scaling.

Then the remaining artifacts are: the ACR build-and-push workflow, and either the ECS
deploy step or the ACK manifests + Argo CD application.
