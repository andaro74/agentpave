"""`pave` — the platform CLI.

Two rules from CLAUDE.md shape this file:

* A verb that is not implemented yet **fails loudly** with
  "arrives in M0x — see docs/ROADMAP.md". It never prints a placeholder and
  exits 0. A scaffolder that silently does nothing is worse than one that is
  missing, because the first time you find out is when the thing it was
  supposed to build is not there.
* The AWS-touching path is imported lazily, inside the function that needs it.
  `pave eval --dry-run` must work in the hermetic gate with no boto3 session
  and no credentials, and a module-level `import boto3` would break that on
  import rather than on use.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from agentpave_evalsvc.dataset import DatasetError, load_dataset
from agentpave_evalsvc.harness import plan

from .scaffold import CLASSIFICATIONS, ScaffoldError, render, validate

# Which milestone owns each verb, for the not-yet message. Kept as data so the
# message and the roadmap cannot disagree quietly.
NOT_YET = {
    "shadow-eval": "M06",
}

# The templates ship inside the repo, not inside the installed package: the
# scaffolder is run from a clone, and a template the operator cannot read and
# diff is a template nobody reviews.
REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = REPO_ROOT / "templates"
SERVICES_ROOT = REPO_ROOT / "services"


def _not_yet(verb: str) -> int:
    milestone = NOT_YET[verb]
    print(
        f"pave {verb} arrives in {milestone} — see docs/ROADMAP.md",
        file=sys.stderr,
    )
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pave", description="The AgentPave platform CLI")
    sub = parser.add_subparsers(dest="verb", required=True)

    evaluate = sub.add_parser("eval", help="run the golden-set evaluation")
    evaluate.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan without calling any model",
    )
    evaluate.add_argument(
        "--diff",
        action="store_true",
        help="compare the run against the last recorded baseline",
    )
    evaluate.add_argument(
        "--save-baseline",
        action="store_true",
        help="record this run as the new baseline",
    )
    evaluate.add_argument(
        "--adversarial-only",
        action="store_true",
        help="run only the adversarial mini-suite",
    )

    new = sub.add_parser("new", help="scaffold a governed service from a template")
    new.add_argument("name", help="kebab-case; becomes the directory, package and stack name")
    new.add_argument("--template", default="agent-tools")
    new.add_argument("--classification", default="internal", choices=CLASSIFICATIONS)
    new.add_argument(
        "--into",
        default=None,
        help="output directory (default: services/); used by the render gate",
    )

    sub.add_parser(
        "shadow-eval",
        help=f"candidate vs. incumbent on the golden set (arrives in {NOT_YET['shadow-eval']})",
    )

    return parser


def _run_eval(args: argparse.Namespace) -> int:
    try:
        dataset = load_dataset()
    except DatasetError as exc:
        print(f"dataset is not usable: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(plan(dataset))
        return 0

    # Imported here, not at module scope: everything below this line needs AWS.
    from agentpave_evalsvc.runner import run_deployed

    return run_deployed(
        dataset,
        stack_name=os.environ.get("AGENTPAVE_GATEWAY_STACK", "AgentPave-Gateway-dev"),
        eval_stack_name=os.environ.get("AGENTPAVE_EVAL_STACK", "AgentPave-Eval-dev"),
        show_diff=args.diff,
        save_baseline=args.save_baseline,
        adversarial_only=args.adversarial_only,
    )


def _run_new(args: argparse.Namespace) -> int:
    try:
        spec = validate(args.name, args.classification)
        written = render(
            spec,
            template_root=TEMPLATE_ROOT / args.template,
            output_root=Path(args.into) if args.into else SERVICES_ROOT,
        )
    except ScaffoldError as exc:
        print(f"pave new: {exc}", file=sys.stderr)
        return 1

    destination = (Path(args.into) if args.into else SERVICES_ROOT) / spec.name
    print(f"scaffolded {spec.name} ({args.template}, {spec.classification}) → {destination}")
    for path in written:
        print(f"  {path}")
    print(
        f"\n{len(written)} files. It reaches models only through the gateway and "
        "tools only through MCP; both are asserted at synth, not trusted."
    )
    return 0


def _force_utf8_output() -> None:
    """Make stdout/stderr UTF-8 regardless of the console's code page.

    The scorecard, the score diff, and the not-yet message all carry non-ASCII
    (`▲`, `▼`, `—`, `✋`). On a Windows console defaulting to cp1252 those raise
    `UnicodeEncodeError` mid-print, so `pave eval` dies partway through its own
    output — while every test still passes, because pytest's `capsys` captures
    text before it is ever encoded for a terminal.

    That gap is why this is a function with a test rather than a habit: the
    hermetic gate cannot see a console, so the encoding has to be pinned here
    instead of assumed.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _force_utf8_output()
    args = _build_parser().parse_args(argv)

    if args.verb in NOT_YET:
        return _not_yet(args.verb)
    if args.verb == "eval":
        return _run_eval(args)
    if args.verb == "new":
        return _run_new(args)

    # Unreachable while argparse validates the verb, but an unhandled verb
    # must not exit 0 — a CLI that silently succeeds at nothing is the failure
    # mode the not-yet rule exists to prevent.
    print(f"pave: unhandled verb {args.verb!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
