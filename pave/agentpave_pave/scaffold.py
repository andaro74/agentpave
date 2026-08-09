"""Rendering a template into a service directory.

Pure over a template root and an output root, so the hermetic gate can render
into a temporary directory and then run ruff and pytest against the result —
which is the M04 hermetic gate, and the only thing that proves the golden path
actually works. A scaffolder tested only by its unit tests is a scaffolder that
renders confidently broken code.

Two constraints come straight from earlier milestones' scars:

* The rendered Python package is `agentpave_<name>`, never `<name>`. A
  directory whose name matches a module shadows it as a namespace package, and
  `sys.path[0]` is the repo root. That cost M03 an afternoon (ADR-004), and a
  scaffolder that renders the same shape would inflict it on every service.
* Rendered test basenames are prefixed with the service name. pytest's prepend
  import mode names test modules by basename, so two services each rendering
  `tests/test_agent.py` collide the moment both exist (ADR-004).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, StrictUndefined

TEMPLATE_SUFFIX = ".j2"

# Kebab-case, starting with a letter. This becomes a Python package name, a
# CloudFormation stack name, and a directory, so the intersection of what all
# three accept is narrower than any one of them.
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

# Mirrors the gateway's own vocabulary. `sensitive` is accepted here and
# refused at the gateway by design (ADR-001) — the scaffolder does not get to
# decide that, but it does have to render a service that asks correctly.
CLASSIFICATIONS = ("public", "internal", "sensitive")


class ScaffoldError(Exception):
    """The render could not proceed. Always raised before anything is written."""


@dataclass(frozen=True)
class ServiceSpec:
    """Everything the template is allowed to know about the service."""

    name: str
    classification: str

    @property
    def package(self) -> str:
        """The Python package name — never equal to the service directory."""
        return "agentpave_" + self.name.replace("-", "_")

    @property
    def stack_name(self) -> str:
        return "AgentPave-Service-" + "".join(p.title() for p in self.name.split("-"))

    @property
    def test_prefix(self) -> str:
        """Prefix for rendered test basenames, unique across the monorepo."""
        return "test_" + self.name.replace("-", "_")

    def as_context(self) -> dict[str, str]:
        return {
            "name": self.name,
            "package": self.package,
            "classification": self.classification,
            "stack_name": self.stack_name,
            "test_prefix": self.test_prefix,
        }


def validate(name: str, classification: str) -> ServiceSpec:
    """Check the inputs, or raise before touching the filesystem."""
    if not NAME_PATTERN.match(name):
        raise ScaffoldError(
            f"service name {name!r} must be kebab-case starting with a letter "
            "(it becomes a package name, a stack name, and a directory)"
        )
    if classification not in CLASSIFICATIONS:
        raise ScaffoldError(
            f"classification {classification!r} must be one of {', '.join(CLASSIFICATIONS)}"
        )
    return ServiceSpec(name=name, classification=classification)


def _environment() -> Environment:
    # StrictUndefined so a template referencing a variable nobody supplies
    # fails the render instead of quietly emitting an empty string. A service
    # scaffolded with a blank package name would fail far from the cause.
    return Environment(
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,  # rendering Python and YAML, never HTML
    )


def _render_path(relative: Path, context: dict[str, str]) -> Path:
    """Substitute variables in a path, and strip the template suffix.

    Directory names carry variables too — `{{package}}/agent.py.j2` has to
    become `agentpave_catalog_agent/agent.py`.
    """
    parts = []
    for part in relative.parts:
        for key, value in context.items():
            part = part.replace("{{" + key + "}}", value)
        parts.append(part)

    rendered = Path(*parts)
    if rendered.suffix == TEMPLATE_SUFFIX:
        rendered = rendered.with_suffix("")
    return rendered


def render(
    spec: ServiceSpec,
    *,
    template_root: Path,
    output_root: Path,
) -> tuple[Path, ...]:
    """Render `template_root` into `output_root / spec.name`.

    Returns the files written, relative to the output root, sorted — so a test
    can assert the shape of a render without reading its contents.

    Refuses to write into an existing directory. Scaffolding is not a merge:
    a half-overwritten service is harder to reason about than either version.
    """
    if not template_root.is_dir():
        raise ScaffoldError(f"template not found: {template_root}")

    destination = output_root / spec.name
    if destination.exists():
        raise ScaffoldError(
            f"{destination} already exists — refusing to overwrite a service; "
            "remove it or choose another name"
        )

    context = spec.as_context()
    environment = _environment()

    # Rendered fully in memory before anything is written, so a template error
    # leaves no half-scaffolded directory behind.
    rendered: dict[Path, str] = {}
    for source in sorted(template_root.rglob("*")):
        if not source.is_file():
            continue
        relative = source.relative_to(template_root)
        text = source.read_text(encoding="utf-8")
        if source.suffix == TEMPLATE_SUFFIX:
            text = environment.from_string(text).render(**context)
        rendered[_render_path(relative, context)] = text

    if not rendered:
        raise ScaffoldError(f"template {template_root} contains no files")

    for relative, text in rendered.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # Written with an explicit encoding and newline: the rendered tree is
        # committed, and a scaffolder that emits the host's code page produces
        # a service whose diff depends on who ran it (M03's cp1252 lesson, one
        # layer up).
        target.write_text(text, encoding="utf-8", newline="\n")

    return tuple(sorted(rendered))
