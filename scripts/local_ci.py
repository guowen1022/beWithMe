#!/usr/bin/env python3
"""Local CI runner + status dashboard + local deploy.

Mirrors .github/workflows/ci.yml so "green locally" means the same thing as
"green on GitHub" -- the checks below are the same commands the workflow runs.

Why not `act`: it runs the real workflow in Docker, which is higher fidelity but
pulls multi-GB runner images and re-installs Playwright/npm every cycle. This
runs the same commands natively, so a full pass is seconds-to-minutes instead of
many minutes, and it records timings so you can see what is stale.

    python scripts/local_ci.py                  # run everything, print a table
    python scripts/local_ci.py --group service  # one group
    python scripts/local_ci.py --only arch unit
    python scripts/local_ci.py --status         # last results, no re-run
    python scripts/local_ci.py --watch          # re-run on file change
    python scripts/local_ci.py --serve          # dashboard on :8900
    python scripts/local_ci.py --deploy         # docker compose up + health

Results persist to .local-ci/status.json, so --status and the dashboard show
what ran and how long ago even in a fresh shell.

Stdlib only -- no install step, nothing to keep in sync with requirements.txt.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = REPO_ROOT / ".local-ci"
STATE_FILE = STATE_DIR / "status.json"

IS_WINDOWS = os.name == "nt"

# Keep in sync with infra/topology.py:SERVICE_OFFSETS.
SERVICES = ["shell", "persona", "knowledge", "transcribe", "speak", "browser", "maestro", "tuning"]

# infra/model/llm.py and infra/model/vision/__init__.py validate provider vars at
# IMPORT time. CI seeds fakes for the same reason; do it here so the runner works
# in a shell with no .env exported.
FAKE_PROVIDER_ENV = {
    "LLM_PROVIDER": "deepseek",
    "DEEPSEEK_API_KEY": "local-ci-not-a-real-key",
    "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
    "DEEPSEEK_MODEL": "deepseek-chat",
    "VISION_PROVIDER": "doubao",
    "DOUBAO_API_KEY": "local-ci-not-a-real-key",
    "DOUBAO_BASE_URL": "https://ark.cn-beijing.volces.com/api/v3",
    "DOUBAO_VISION_MODEL": "doubao-seed-2-0-lite-260428",
}

PASS, FAIL, SKIP, RUNNING = "pass", "fail", "skip", "running"


@dataclass
class Check:
    name: str
    group: str
    cmd: list[str]
    cwd: Path = REPO_ROOT
    # Returns a reason string when the check cannot run here, else None.
    precondition: object = None
    # Paths whose change should invalidate this check in --watch.
    watches: list[str] = field(default_factory=list)


def _npm(*args: str) -> list[str]:
    # npm on Windows is npm.cmd; shutil.which resolves whichever exists.
    exe = shutil.which("npm") or "npm"
    return [exe, *args]


def _npx(*args: str) -> list[str]:
    exe = shutil.which("npx") or "npx"
    return [exe, *args]


def _needs_node_modules(subdir: str):
    def check() -> str | None:
        if not (REPO_ROOT / subdir / "node_modules").is_dir():
            return f"{subdir}/node_modules missing - run: cd {subdir} && npm ci"
        return None
    return check


def _needs_docker() -> str | None:
    if shutil.which("docker") is None:
        return "docker not on PATH"
    return None


def build_checks() -> list[Check]:
    checks: list[Check] = [
        Check(
            "arch", "arch",
            [sys.executable, "scripts/check_arch.py"],
            watches=["infra", "silicon_brain", "persona"],
        ),
    ]

    for svc in SERVICES:
        checks.append(Check(
            f"service:{svc}", "service",
            [sys.executable, "scripts/smoke_service.py", svc],
            watches=[f"services/{svc}", "infra", "persona", "silicon_brain", "tools", "workshop"],
        ))

    checks.append(Check(
        "unit", "unit",
        [sys.executable, "-m", "pytest", "tests/unit", "-q"],
        watches=["tests/unit", "infra", "persona", "silicon_brain", "services", "tools", "workshop"],
    ))

    # Clients: typecheck by default (fast). --full swaps in the real build.
    checks += [
        Check("client:web", "client", _npm("run", "build"),
              cwd=REPO_ROOT / "frontend",
              precondition=_needs_node_modules("frontend"),
              watches=["frontend/app", "frontend/components", "frontend/lib"]),
        Check("client:desktop", "client", _npm("run", "build"),
              cwd=REPO_ROOT / "desktop",
              precondition=_needs_node_modules("desktop"),
              watches=["desktop/src"]),
        Check("client:mobile", "client", _npx("tsc", "--noEmit", "-p", "tsconfig.json"),
              cwd=REPO_ROOT / "mobile",
              precondition=_needs_node_modules("mobile"),
              watches=["mobile/src"]),
    ]
    return checks


def fast_client_checks(checks: list[Check]) -> list[Check]:
    """Swap the web/desktop builds for typechecks -- much faster inner loop."""
    out = []
    for c in checks:
        if c.name == "client:web":
            c = Check(c.name, c.group, _npx("tsc", "--noEmit", "-p", "tsconfig.json"),
                      c.cwd, c.precondition, c.watches)
        elif c.name == "client:desktop":
            c = Check(c.name, c.group, _npx("tsc", "--noEmit", "-p", "tsconfig.json"),
                      c.cwd, c.precondition, c.watches)
        out.append(c)
    return out


# --------------------------------------------------------------------- state


def load_state() -> dict:
    if STATE_FILE.is_file():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return {"checks": {}, "deploy": None}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)  # atomic, so --serve never reads a half-written file


def ago(iso: str | None) -> str:
    if not iso:
        return "never"
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return "?"
    secs = (datetime.now(timezone.utc) - then).total_seconds()
    if secs < 60:
        return f"{int(secs)}s ago"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


# --------------------------------------------------------------------- run


def run_check(check: Check, state: dict, verbose: bool = False) -> str:
    if check.precondition:
        reason = check.precondition()
        if reason:
            state["checks"][check.name] = {
                "status": SKIP, "reason": reason, "duration": 0.0,
                "finished_at": datetime.now(timezone.utc).isoformat(), "output": "",
            }
            return SKIP

    env = {**os.environ, **FAKE_PROVIDER_ENV, "PYTHONIOENCODING": "utf-8"}
    started = time.monotonic()
    try:
        proc = subprocess.run(
            check.cmd, cwd=check.cwd, env=env,
            capture_output=not verbose, text=True,
            encoding="utf-8", errors="replace",
            timeout=1800,
        )
        out = "" if verbose else ((proc.stdout or "") + (proc.stderr or ""))
        status = PASS if proc.returncode == 0 else FAIL
    except FileNotFoundError as exc:
        out, status = f"command not found: {exc}", SKIP
    except subprocess.TimeoutExpired:
        out, status = "timed out after 1800s", FAIL

    state["checks"][check.name] = {
        "status": status,
        "duration": round(time.monotonic() - started, 2),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        # Keep the tail only; the full log would bloat status.json fast.
        "output": "\n".join(out.splitlines()[-40:]),
        "group": check.group,
    }
    return status


ICON = {PASS: "PASS", FAIL: "FAIL", SKIP: "SKIP", RUNNING: "....", None: "  ? "}


def print_table(state: dict, checks: list[Check]) -> None:
    width = max((len(c.name) for c in checks), default=10) + 2
    print()
    print(f"  {'CHECK'.ljust(width)} {'STATUS':<6} {'TIME':>8}  LAST RUN")
    print(f"  {'-' * width} {'-' * 6} {'-' * 8}  {'-' * 12}")
    for c in checks:
        r = state["checks"].get(c.name)
        status = r["status"] if r else None
        dur = f"{r['duration']:.1f}s" if r else "-"
        print(f"  {c.name.ljust(width)} {ICON[status]:<6} {dur:>8}  "
              f"{ago(r['finished_at']) if r else 'never'}")

    failed = [c.name for c in checks
              if (state["checks"].get(c.name) or {}).get("status") == FAIL]
    skipped = [c.name for c in checks
               if (state["checks"].get(c.name) or {}).get("status") == SKIP]
    print()
    if skipped:
        for name in skipped:
            print(f"  SKIP {name}: {state['checks'][name].get('reason', '')}")
    if failed:
        print(f"  {len(failed)} FAILED: {', '.join(failed)}")
        for name in failed:
            print(f"\n  --- {name} ---")
            for line in state["checks"][name]["output"].splitlines()[-15:]:
                print(f"  | {line}")
    else:
        print("  all green")
    print()


# -------------------------------------------------------------------- doctor

def _has_chromium() -> bool:
    """Is a Playwright Chromium build present for this interpreter?"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as pw:
            return Path(pw.chromium.executable_path).exists()
    except Exception:
        return False


def doctor(fix: bool = False) -> int:
    """Report (and optionally install) everything the checks need.

    Two of these are easy to get wrong and produce a wall of confusing test
    failures rather than a clear message:
      * frontend/node_modules -- infra/render/mermaid.py loads mermaid straight
        out of the frontend tree, so ~9 render tests need it.
      * Playwright Chromium   -- the same render path rasterises through it,
        and services/browser drives it. requirements.txt installs the Python
        package; the browser binary is a separate download.
    """
    steps: list[tuple[str, bool, list[str] | None, Path]] = []

    try:
        import fastapi  # noqa: F401
        pydeps_ok = True
    except ImportError:
        pydeps_ok = False
    steps.append(("python deps (requirements.txt)", pydeps_ok,
                  [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], REPO_ROOT))

    steps.append(("playwright chromium", _has_chromium(),
                  [sys.executable, "-m", "playwright", "install", "chromium"], REPO_ROOT))

    for sub in ("frontend", "desktop", "mobile"):
        present = (REPO_ROOT / sub / "node_modules").is_dir()
        cmd = _npm("ci") if (REPO_ROOT / sub / "package-lock.json").is_file() else _npm("install")
        steps.append((f"{sub}/node_modules", present, cmd, REPO_ROOT / sub))

    env_ok = (REPO_ROOT / ".env").is_file()
    steps.append((".env (needed only for --deploy)", env_ok, None, REPO_ROOT))

    print()
    missing = [s for s in steps if not s[1]]
    for label, ok, cmd, cwd in steps:
        print(f"  {'OK  ' if ok else 'MISS'} {label}")
    print()

    if not missing:
        print("  everything the checks need is present")
        return 0

    if not fix:
        print("  run `python scripts/local_ci.py --setup` to install the missing pieces")
        for label, ok, cmd, cwd in missing:
            if cmd is None:
                print(f"    - {label}: copy .env.example to .env and fill in provider keys")
        return 1

    for label, ok, cmd, cwd in missing:
        if cmd is None:
            print(f"  SKIP {label} -- create it yourself (copy .env.example)")
            continue
        print(f"  installing {label} ...")
        rc = subprocess.run(cmd, cwd=cwd).returncode
        print(f"  {'done' if rc == 0 else 'FAILED'}: {label}")
    return 0


# ------------------------------------------------------------------- deploy


EMBED_MODEL = "nomic-embed-text"


def _dc(*args: str, capture: bool = False, check: bool = False):
    """docker compose, always rooted at the repo."""
    return subprocess.run(
        ["docker", "compose", *args], cwd=REPO_ROOT, check=check,
        capture_output=capture, text=True, encoding="utf-8", errors="replace",
    )


def _wait_http(url: str, attempts: int = 60, interval: float = 2.0) -> bool:
    import urllib.request
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=3):
                return True
        except Exception:
            time.sleep(interval)
    return False


def container_status() -> dict[str, str]:
    """Per-service container state, for the table and the dashboard."""
    proc = _dc("ps", "--format", "json", capture=True)
    out: dict[str, str] = {}
    if proc.returncode != 0:
        return out
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        # Compose emits either one object per line or a single JSON array.
        rows = row if isinstance(row, list) else [row]
        for r in rows:
            name = r.get("Service") or r.get("Name")
            if name:
                out[name] = r.get("State") or r.get("Status") or "?"
    return out


def deploy(state: dict, down: bool = False, rebuild: bool = False, init_db: bool = False) -> int:
    """Local CD: build the current tree, bring the topology up, prove it healthy.

    Mirrors what scripts/deploy-ecs.sh does on the server, minus the ACR pull --
    here the images are built from the working tree, which is the whole point of
    a *local* deploy: you are testing the code in front of you.
    """
    reason = _needs_docker()
    if reason:
        print(f"cannot deploy: {reason}")
        return 1
    if _dc("version", capture=True).returncode != 0:
        print("docker is installed but the daemon is not reachable -- start Docker Desktop")
        return 1

    if down:
        print("[deploy] stopping...")
        _dc("down")
        state["deploy"] = {"status": "down", "at": datetime.now(timezone.utc).isoformat()}
        save_state(state)
        print("[deploy] stopped (volumes kept; `docker compose down -v` also drops data)")
        return 0

    if not (REPO_ROOT / ".env").is_file():
        print("no .env found -- run: cp .env.example .env   then fill in provider keys")
        return 1

    started = time.monotonic()

    if rebuild:
        # Only two distinct images exist: five services share bewithme-core and
        # three share bewithme-media. A bare `docker compose build` fans out per
        # SERVICE, so several jobs build the same tag concurrently and collide
        # with `image "bewithme-core:dev": already exists`. Build one
        # representative of each image instead; the rest then just use it.
        print("[deploy] building core + media from the working tree "
              "(first run pulls Chromium; slow)...")
        if _dc("build", "shell", "browser").returncode != 0:
            print("[deploy] build FAILED")
            return 1

    print(f"[deploy] starting postgres + ollama...")
    if _dc("up", "-d", "postgres", "ollama").returncode != 0:
        print("[deploy] dependencies failed to start")
        return 1

    # The embedding model is a one-time ~275MB pull, but every retrieval path
    # needs it (infra/rag/embedding.py). Pull only when absent.
    listed = _dc("exec", "-T", "ollama", "ollama", "list", capture=True)
    if EMBED_MODEL not in (listed.stdout or ""):
        print(f"[deploy] pulling {EMBED_MODEL} (one-time, ~275MB)...")
        _dc("exec", "-T", "ollama", "ollama", "pull", EMBED_MODEL)

    if init_db:
        print("[deploy] initialising schema (pgvector, uuid-ossp, tables)...")
        if _dc("run", "--rm", "init-db").returncode != 0:
            print("[deploy] init-db FAILED")
            return 1

    print("[deploy] starting the 8 sidecars...")
    ok = _dc("up", "-d").returncode == 0

    health = "unknown"
    if ok:
        print("[deploy] waiting for the shell on :8000 ...")
        health = "healthy" if _wait_http("http://127.0.0.1:8000/api/health") else "unhealthy"

    containers = container_status()
    running = sum(1 for v in containers.values() if str(v).lower().startswith("run"))

    state["deploy"] = {
        "status": "up" if ok and health == "healthy" else "degraded",
        "health": health,
        "containers": containers,
        "running": running,
        "total": len(containers),
        "duration": round(time.monotonic() - started, 1),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    save_state(state)

    print()
    print(f"  [deploy] {state['deploy']['status']}  health={health}  "
          f"{running}/{len(containers)} containers up  ({state['deploy']['duration']}s)")
    for name, st in sorted(containers.items()):
        flag = "  " if str(st).lower().startswith("run") else "!!"
        print(f"    {flag} {name:<12} {st}")
    print()
    if health == "healthy":
        print("  shell:  http://127.0.0.1:8000/api/health")
        print("  logs:   docker compose logs -f shell")
        print("  stop:   python scripts/local_ci.py --deploy-down")
    else:
        print("  shell did not become healthy. Inspect with:")
        print("    docker compose logs --tail 50 shell knowledge")
    print()
    return 0 if health == "healthy" else 1


def cd_flow(checks: list[Check], state: dict, rebuild: bool, init_db: bool) -> int:
    """Gated local CD -- the actual point of a CD pipeline: only ship what passes.

    Skips the client checks: they gate the web/desktop/mobile builds, which are
    not what `docker compose up` deploys (the compose topology is the 8 Python
    sidecars). Running them here would fail the deploy for an unrelated reason.
    """
    gating = [c for c in checks if c.group != "client"]
    print(f"[cd] running {len(gating)} gating check(s) before deploy...")
    failed = run_all(gating, state)
    print_table(state, gating)
    if failed:
        print(f"[cd] {failed} check(s) failed -- NOT deploying")
        return 1
    print("[cd] checks green -- deploying")
    return deploy(state, rebuild=rebuild, init_db=init_db)


# -------------------------------------------------------------------- watch


def snapshot(checks: list[Check]) -> dict[str, float]:
    """mtime of every source file the checks care about."""
    roots = {w for c in checks for w in c.watches}
    out: dict[str, float] = {}
    for root in roots:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if any(part in {"__pycache__", "node_modules", ".next", ".git"} for part in p.parts):
                continue
            if p.suffix not in {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".yaml", ".yml"}:
                continue
            try:
                out[str(p)] = p.stat().st_mtime
            except OSError:
                pass
    return out


def watch(checks: list[Check], state: dict) -> None:
    print("watching for changes -- Ctrl-C to stop")
    prev = snapshot(checks)
    run_all(checks, state)
    while True:
        time.sleep(1.5)
        cur = snapshot(checks)
        changed = {p for p in cur if prev.get(p) != cur[p]} | (set(prev) - set(cur))
        if not changed:
            continue
        rel = {str(Path(p).relative_to(REPO_ROOT)).replace("\\", "/") for p in changed}
        affected = [c for c in checks if any(r.startswith(w) for w in c.watches for r in rel)]
        print(f"\n>>> {len(changed)} file(s) changed -> re-running {len(affected)} check(s)")
        run_all(affected or checks, state)
        prev = cur


# --------------------------------------------------------------------- serve

DASHBOARD = """<!doctype html>
<meta charset="utf-8"><title>beWithMe local CI</title>
<style>
 :root{color-scheme:light dark}
 body{font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;margin:0;padding:24px;
      background:Canvas;color:CanvasText}
 h1{font-size:16px;margin:0 0 4px}
 .sub{opacity:.6;font-size:12px;margin-bottom:20px}
 table{border-collapse:collapse;width:100%;max-width:820px}
 td,th{padding:6px 10px;text-align:left;border-bottom:1px solid rgba(128,128,128,.25)}
 th{font-weight:600;opacity:.6;font-size:11px;text-transform:uppercase;letter-spacing:.05em}
 .pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:600}
 .pass{background:#1a7f37;color:#fff}.fail{background:#cf222e;color:#fff}
 .skip{background:#9a6700;color:#fff}.none{background:#57606a;color:#fff}
 .grp{opacity:.5;font-size:11px;padding-top:14px}
 pre{background:rgba(128,128,128,.12);padding:8px;border-radius:6px;overflow:auto;
     font-size:11px;margin:4px 0 0;max-height:200px}
 .deploy{margin-top:24px;max-width:820px}
</style>
<h1>beWithMe -- local CI</h1>
<div class="sub" id="sub">loading…</div>
<table id="t"></table>
<div class="deploy" id="d"></div>
<script>
const ago = iso => {
  if(!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime())/1000;
  if(s<60) return Math.floor(s)+"s ago";
  if(s<3600) return Math.floor(s/60)+"m ago";
  if(s<86400) return Math.floor(s/3600)+"h ago";
  return Math.floor(s/86400)+"d ago";
};
async function tick(){
  let d;
  try { d = await (await fetch("status.json?"+Date.now())).json(); }
  catch { document.getElementById("sub").textContent = "no status.json yet -- run: python scripts/local_ci.py"; return; }
  const checks = d.checks||{};
  const names = Object.keys(checks);
  const bad = names.filter(n=>checks[n].status==="fail");
  document.getElementById("sub").innerHTML =
    `<b>${bad.length? bad.length+" failing" : names.length? "all green" : "nothing run yet"}</b>`
    + ` · data refreshed ${ago(d.updated_at)}`
    + ` · page refreshed just now (auto every 3s)`;
  let html = "<tr><th>Check</th><th>Status</th><th>Time</th><th>Last run</th></tr>", grp=null;
  for(const n of names){
    const c = checks[n];
    if(c.group && c.group!==grp){ grp=c.group; html += `<tr><td class="grp" colspan="4">${grp}</td></tr>`; }
    const cls = c.status||"none";
    html += `<tr><td>${n}</td>`
         +  `<td><span class="pill ${cls}">${(c.status||"?").toUpperCase()}</span></td>`
         +  `<td>${c.duration!=null? c.duration.toFixed(1)+"s":"-"}</td>`
         +  `<td>${ago(c.finished_at)}</td></tr>`;
    if(c.status==="fail" && c.output)
      html += `<tr><td colspan="4"><pre>${c.output.replace(/[<&]/g,m=>({"<":"&lt;","&":"&amp;"}[m]))}</pre></td></tr>`;
    if(c.status==="skip" && c.reason)
      html += `<tr><td colspan="4"><pre>${c.reason}</pre></td></tr>`;
  }
  document.getElementById("t").innerHTML = html;
  const dep = d.deploy;
  if(!dep){
    document.getElementById("d").innerHTML =
      `<b>local deploy:</b> <span class="pill none">never</span> -- run: python scripts/local_ci.py --cd`;
  } else {
    const cls = dep.status==="up" ? "pass" : dep.status==="down" ? "none" : "fail";
    let h = `<b>local deploy:</b> <span class="pill ${cls}">${dep.status}</span>`
          + (dep.health? ` health=${dep.health}` : "")
          + (dep.total? ` &middot; ${dep.running}/${dep.total} containers` : "")
          + ` &middot; ${ago(dep.at)}`
          + (dep.status==="up" ? ` &middot; <a href="http://127.0.0.1:8000/api/health">:8000</a>` : "");
    if(dep.containers && Object.keys(dep.containers).length){
      h += "<table>";
      for(const [n,st] of Object.entries(dep.containers).sort()){
        const up = String(st).toLowerCase().startsWith("run");
        h += `<tr><td>${n}</td><td><span class="pill ${up?"pass":"fail"}">${st}</span></td></tr>`;
      }
      h += "</table>";
    }
    document.getElementById("d").innerHTML = h;
  }
}
tick(); setInterval(tick, 3000);
</script>
"""


def serve(port: int) -> None:
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    STATE_DIR.mkdir(exist_ok=True)
    (STATE_DIR / "index.html").write_text(DASHBOARD, encoding="utf-8")

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(STATE_DIR), **kw)

        def end_headers(self):
            # status.json changes constantly; never let a browser cache it.
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, *a):
            pass  # keep the console clean for check output

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"dashboard: http://127.0.0.1:{port}/  (Ctrl-C to stop)")
    httpd.serve_forever()


# ---------------------------------------------------------------------- main


def run_all(checks: list[Check], state: dict, verbose: bool = False) -> int:
    # Overwrite the "running" line in place only on a real terminal; when piped
    # or redirected a bare \r just leaves both halves on one line.
    tty = sys.stdout.isatty()
    for c in checks:
        if tty:
            print(f"  .... {c.name}", end="", flush=True)
        status = run_check(c, state, verbose)
        r = state["checks"][c.name]
        prefix = "\r" if tty else ""
        print(f"{prefix}  {ICON[status]} {c.name} ({r['duration']:.1f}s)      ")
        save_state(state)  # after each, so the dashboard updates live
    return sum(1 for c in checks
               if state["checks"].get(c.name, {}).get("status") == FAIL)


def main() -> int:
    # Line-buffer our own output. Subprocesses inherit the fd and write straight
    # through, so with the default block buffering on a redirect (`> cd.log`) the
    # docker build appears BEFORE the check results that gated it.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", nargs="+", metavar="CHECK", help="run specific checks by name")
    p.add_argument("--group", choices=["arch", "service", "unit", "client"], help="run one group")
    p.add_argument("--status", action="store_true", help="show last results without running")
    p.add_argument("--watch", action="store_true", help="re-run affected checks on file change")
    p.add_argument("--serve", action="store_true", help="serve the status dashboard")
    p.add_argument("--port", type=int, default=8900)
    p.add_argument("--deploy", action="store_true", help="build + up + health check")
    p.add_argument("--deploy-down", action="store_true", help="docker compose down")
    p.add_argument("--cd", action="store_true",
                   help="gated deploy: run the checks, deploy only if they pass")
    p.add_argument("--rebuild", action="store_true",
                   help="rebuild images from the working tree before deploying")
    p.add_argument("--init-db", action="store_true",
                   help="run scripts/init_db.py in the stack (first deploy / schema change)")
    p.add_argument("--full", action="store_true", help="real client builds instead of typechecks")
    p.add_argument("--verbose", action="store_true", help="stream check output instead of capturing")
    p.add_argument("--list", action="store_true", help="list check names")
    p.add_argument("--doctor", action="store_true", help="report missing prerequisites")
    p.add_argument("--setup", action="store_true", help="install missing prerequisites")
    args = p.parse_args()

    if args.doctor or args.setup:
        return doctor(fix=args.setup)

    checks = build_checks()
    if not args.full:
        checks = fast_client_checks(checks)

    if args.list:
        for c in checks:
            print(f"{c.group:<8} {c.name}")
        return 0

    state = load_state()

    if args.serve:
        # Serve in the background so --serve composes with --watch / a run.
        threading.Thread(target=serve, args=(args.port,), daemon=True).start()
        if args.watch:
            try:
                watch(checks, state)
            except KeyboardInterrupt:
                return 0
        else:
            print("dashboard running; press Ctrl-C to stop")
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                return 0
        return 0

    if args.cd:
        return cd_flow(checks, state, rebuild=args.rebuild, init_db=args.init_db)

    if args.deploy or args.deploy_down:
        return deploy(state, down=args.deploy_down,
                      rebuild=args.rebuild, init_db=args.init_db)

    if args.status:
        print_table(state, checks)
        return 0

    if args.only:
        known = {c.name for c in checks}
        unknown = [n for n in args.only if n not in known]
        if unknown:
            print(f"unknown check(s): {', '.join(unknown)}\nsee --list")
            return 2
        checks = [c for c in checks if c.name in args.only]
    elif args.group:
        checks = [c for c in checks if c.group == args.group]

    if args.watch:
        try:
            watch(checks, state)
        except KeyboardInterrupt:
            return 0
        return 0

    failed = run_all(checks, state, args.verbose)
    print_table(state, checks)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
