"""Block template loader — shared between the engineer agent (which reads
templates for keyword-matched suggestion) and the mount-template endpoint
(which materializes a template into a user's per-user-git workspace).

The loader returns a self-contained object that includes:

  * `id_default`: a kebab-case id derived from the template filename.
  * `js`: the raw template source (with `__BLOCK_ID__` etc. placeholders
    still in place).
  * `md`: the human-readable design doc body (frontmatter stripped).
  * `keywords`: list of keywords from the frontmatter (engineer routing).
  * `manifest`: the structured manifest (backend calls + publishes +
    subscribes) — the contract `helpers.backend.<name>(args)` reads.

Frontmatter shape we expect (YAML):

  ---
  keywords: upload file paper document pdf
  publishes: [__DOC_TOPIC__]
  subscribes: []
  backend:
    upload:
      method: POST
      path: /api/documents/upload
      auth: user
      content_type: multipart/form-data
      returns: json
  ---

Both `keywords` and `publishes`/`subscribes` accept either a YAML list or
a comma-separated string (back-compat with the older skill-style frontmatter).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = _REPO_ROOT / "frontend" / "templates" / "blocks"

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_KEBAB = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass
class TemplateManifest:
    keywords: list[str] = field(default_factory=list)
    publishes: list[str] = field(default_factory=list)
    subscribes: list[str] = field(default_factory=list)
    backend: dict[str, dict[str, Any]] = field(default_factory=dict)
    purpose: str = ""
    grid: dict[str, int] | None = None    # template-preferred default grid

    def to_json(self) -> dict[str, Any]:
        """Subset of the manifest the frontend consumes (helpers.backend etc.)."""
        return {
            "publishes": self.publishes,
            "subscribes": self.subscribes,
            "backend": self.backend,
        }


@dataclass
class Template:
    name: str             # filename stem (e.g. "upload_file")
    id_default: str       # kebab id we'll assign by default (e.g. "upload-file")
    js: str               # raw template source with __BLOCK_ID__ placeholders
    md: str               # design doc body (frontmatter stripped)
    manifest: TemplateManifest


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (parsed YAML dict, body)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text.strip()
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"template frontmatter is not valid YAML: {e}") from e
    if not isinstance(meta, dict):
        raise ValueError("template frontmatter must parse to a mapping")
    body = text[m.end():].strip()
    return meta, body


def _coerce_str_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        # comma- or space-separated
        return [p.strip() for p in re.split(r"[,\s]+", v) if p.strip()]
    return [str(v).strip()]


def _kebab_id_from_name(name: str) -> str:
    """`upload_file` → `upload-file`. Validates the result is engineer-safe."""
    candidate = name.replace("_", "-").lower()
    if not _KEBAB.match(candidate):
        raise ValueError(f"template name {name!r} doesn't yield a kebab-safe id")
    return candidate


def list_templates() -> list[str]:
    """Names of every available template (filename stems)."""
    if not TEMPLATES_DIR.is_dir():
        return []
    return sorted(p.stem for p in TEMPLATES_DIR.glob("*.js"))


def load_template(name: str) -> Template:
    """Read a template by name (filename stem). Raises FileNotFoundError /
    ValueError on missing file or bad frontmatter."""
    # Allowlist: filename must be kebab-or-snake of [a-z0-9_-].
    if not re.match(r"^[a-z0-9][a-z0-9_\-]*$", name):
        raise ValueError(f"invalid template name: {name!r}")

    js_path = TEMPLATES_DIR / f"{name}.js"
    md_path = TEMPLATES_DIR / f"{name}.md"
    if not js_path.is_file():
        raise FileNotFoundError(f"template not found: {name}")

    js = js_path.read_text(encoding="utf-8")
    md_raw = md_path.read_text(encoding="utf-8") if md_path.is_file() else ""
    meta, body = _split_frontmatter(md_raw)

    backend = meta.get("backend") or {}
    if not isinstance(backend, dict):
        raise ValueError(f"template {name}: `backend` must be a mapping")
    # Each backend entry must be a dict with at least method + path.
    for call_name, spec in list(backend.items()):
        if not isinstance(spec, dict):
            raise ValueError(f"template {name}: backend.{call_name} must be a mapping")
        if "method" not in spec or "path" not in spec:
            raise ValueError(f"template {name}: backend.{call_name} needs `method` and `path`")

    grid_meta = meta.get("grid")
    grid: dict[str, int] | None = None
    if isinstance(grid_meta, dict):
        try:
            grid = {
                "x": int(grid_meta["x"]),
                "y": int(grid_meta["y"]),
                "w": int(grid_meta["w"]),
                "h": int(grid_meta["h"]),
            }
        except (KeyError, TypeError, ValueError):
            raise ValueError(
                f"template {name}: `grid` must contain integer x/y/w/h"
            )

    manifest = TemplateManifest(
        keywords=_coerce_str_list(meta.get("keywords")),
        publishes=_coerce_str_list(meta.get("publishes")),
        subscribes=_coerce_str_list(meta.get("subscribes")),
        backend=backend,
        purpose=str(meta.get("purpose") or "").strip(),
        grid=grid,
    )

    return Template(
        name=name,
        id_default=_kebab_id_from_name(name),
        js=js,
        md=body,
        manifest=manifest,
    )


__all__ = [
    "Template",
    "TemplateManifest",
    "TEMPLATES_DIR",
    "list_templates",
    "load_template",
]
