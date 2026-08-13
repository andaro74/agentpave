"""Every relative link in the committed docs resolves — checked against git, not the disk.

M07's hermetic gate asks for "no broken doc links". The interesting half is what
counts as *existing*. Resolving a link target against the working tree would pass
on a file the author has but has never added, and 404 for everyone who clones —
so the source of truth here is `git ls-files`.

That distinction is not hypothetical. The three demo GIFs sat untracked in
`docs/images/` while the README that references them was being written, and a
filesystem check would have called that green on the one machine where it could
not fail.
"""

import posixpath
import re
import subprocess
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

import pytest

REPO = Path(__file__).resolve().parents[1]

# Golden fixtures are test data, not documentation. `pr_comment_blocked.md` is
# the exact bytes the gate posts on a pull request, links and all, and they
# point at github.com rather than into this tree.
EXCLUDED_PREFIXES = ("platform/evalsvc/tests/golden/",)

# Jinja sources are excluded because their link targets contain `{{ }}` that has
# no meaning until rendered. The rendered form is not unchecked, though: the
# committed sample `services/catalog-agent/README.md` is the template's output
# and is checked like any other document, with the M04 drift test keeping the
# two in step.
EXCLUDED_SUFFIXES = (".md.j2",)

_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)")
_FENCE = re.compile(r"^\s*(```|~~~)")
# An inline code span renders as literal text, so `[x](y)` inside backticks is
# not a link and must not be resolved. Found the honest way: this file's own
# review log quotes the broken link used to demonstrate the checker's teeth,
# and the checker dutifully reported it as broken documentation.
_CODE_SPAN = re.compile(r"`[^`]*`")
_LINE_ANCHOR = re.compile(r"^L(\d+)(?:-L(\d+))?$")

_EXTERNAL_SCHEMES = ("http://", "https://", "mailto:")


def _tracked() -> set[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return {p for p in out.stdout.split("\0") if p}


def _strip_fences(text: str) -> list[tuple[int, str]]:
    """Numbered lines with fenced code blocks blanked out.

    A fenced block can legitimately contain `[x](y)` — the README's own
    quick-start does — and a checker that resolved those would fail on prose.
    Lines are blanked rather than dropped so reported line numbers stay true to
    the file a reader will open.
    """
    lines, inside = [], False
    for n, line in enumerate(text.splitlines(), start=1):
        if _FENCE.match(line):
            inside = not inside
            lines.append((n, ""))
            continue
        lines.append((n, "" if inside else _CODE_SPAN.sub("", line)))
    return lines


def broken_links(doc: str, text: str, tracked: set[str]) -> list[str]:
    """One message per link in `doc` that will not resolve for someone who clones.

    `doc` is repo-relative and posix-separated. Factored out of the test that
    walks the repository so the checker itself can be given a document with a
    known-bad link — a link checker whose only input is a tree where every link
    happens to work has demonstrated nothing.
    """
    failures: list[str] = []
    for lineno, line in _strip_fences(text):
        for raw in _LINK.findall(line):
            target = unquote(raw)
            if target.startswith(_EXTERNAL_SCHEMES) or target.startswith("#"):
                continue

            path, _, fragment = target.partition("#")
            if not path:
                continue

            # normpath collapses `..`; PurePosixPath alone does not.
            resolved = posixpath.normpath(str(PurePosixPath(doc).parent / path))

            is_file = resolved in tracked
            is_dir = any(t.startswith(resolved.rstrip("/") + "/") for t in tracked)
            if not (is_file or is_dir):
                failures.append(f"{doc}:{lineno} -> {target} (no such tracked path: {resolved})")
                continue

            # `file.py#L42` is GitHub's line anchor. A reference past the end of
            # the file is rot that renders as a link to nothing in particular.
            if is_file and (m := _LINE_ANCHOR.match(fragment)):
                last = int(m.group(2) or m.group(1))
                have = len((REPO / resolved).read_text(encoding="utf-8").splitlines())
                if last > have:
                    failures.append(
                        f"{doc}:{lineno} -> {target} (line {last} past end of file, {have} lines)"
                    )
    return failures


def _documents(tracked: set[str]) -> list[str]:
    return sorted(
        p
        for p in tracked
        if p.endswith(".md")
        and not p.startswith(EXCLUDED_PREFIXES)
        and not p.endswith(EXCLUDED_SUFFIXES)
    )


# ── the gate ──────────────────────────────────────────────────────────────


def test_every_relative_link_in_committed_markdown_resolves() -> None:
    tracked = _tracked()
    docs = _documents(tracked)
    assert docs, "no markdown found — the checker would pass by having nothing to check"

    failures: list[str] = []
    for doc in docs:
        failures += broken_links(doc, (REPO / doc).read_text(encoding="utf-8"), tracked)

    assert not failures, "broken documentation links:\n  " + "\n  ".join(failures)


# ── teeth ─────────────────────────────────────────────────────────────────


def test_the_checker_catches_a_link_to_a_path_that_does_not_exist() -> None:
    failures = broken_links("docs/X.md", "See [the spec](ARCHITECTURE.md).", tracked=set())
    assert len(failures) == 1
    assert "docs/ARCHITECTURE.md" in failures[0]


def test_the_checker_catches_a_link_that_resolves_only_on_the_authors_disk() -> None:
    """The property the filesystem cannot check: present locally, absent in the clone."""
    doc, text = "README.md", "![act 1](docs/images/act-1-paved-road.gif)"
    assert (REPO / "docs/images/act-1-paved-road.gif").exists(), (
        "this test asserts a tracked-vs-present distinction and needs the file present"
    )
    assert broken_links(doc, text, tracked=set()), "an untracked target must be reported"
    assert not broken_links(doc, text, tracked={"docs/images/act-1-paved-road.gif"})


def test_the_checker_ignores_links_inside_fenced_code() -> None:
    text = "```\n[not a link](nowhere.md)\n```\n"
    assert broken_links("README.md", text, tracked=set()) == []


def test_the_checker_ignores_a_link_quoted_in_an_inline_code_span() -> None:
    """Backticks render the link as literal text, so it is not a link.

    This is the case the review log needed: describing the mutation that proves
    this checker has teeth means quoting a deliberately broken link, and the
    first version of the checker reported the description as a defect.
    """
    text = "Appending `[a doc that does not exist](docs/NOPE.md)` turns the gate red."
    assert broken_links("docs/VALIDATION.md", text, tracked=set()) == []
    # …and the same link outside backticks is still caught.
    assert broken_links("docs/VALIDATION.md", text.replace("`", ""), tracked=set())


def test_the_checker_catches_a_line_anchor_past_the_end_of_the_file() -> None:
    target = "pyproject.toml"
    have = len((REPO / target).read_text(encoding="utf-8").splitlines())
    failures = broken_links("docs/X.md", f"[too far](../{target}#L{have + 500})", tracked={target})
    assert len(failures) == 1
    assert "past end of file" in failures[0]


def test_the_checker_accepts_a_line_anchor_within_the_file() -> None:
    assert (
        broken_links("docs/X.md", "[fine](../pyproject.toml#L1)", tracked={"pyproject.toml"}) == []
    )


# ── the diagrams guard ────────────────────────────────────────────────────


def test_every_diagram_source_has_a_committed_svg() -> None:
    """`make diagrams` renders Mermaid to SVG; this asserts the output was committed.

    It deliberately does **not** assert the SVG is *current*. Checking that would
    mean re-rendering, which needs Node and a headless browser — the toolchain
    `make check` does not require, for ADR-029's reason. So this catches a
    diagram whose render was never committed, and cannot catch one whose source
    moved on afterwards. Said plainly here because a guard that looks like more
    than it is, is this repository's recurring defect.
    """
    tracked = _tracked()
    sources = sorted(p for p in tracked if p.endswith(".mermaid"))
    assert sources, "no Mermaid sources found — `make diagrams` would render nothing"

    missing = [s for s in sources if s.removesuffix(".mermaid") + ".svg" not in tracked]
    assert not missing, (
        "diagram sources with no committed SVG (run `make diagrams`):\n  " + "\n  ".join(missing)
    )


def test_no_rendered_diagram_hides_its_labels_in_a_foreignobject() -> None:
    """The render must survive GitHub, which is the only place it is displayed.

    mermaid-cli's default puts every label inside a `<foreignObject>` of HTML.
    GitHub's markdown sanitiser strips those, so the SVG displays as
    correctly-shaped boxes containing **no text at all** — while rendering
    perfectly in every local viewer, in any PNG export, and in the browser the
    renderer itself used. The first committed render of this repository's
    diagram had 66 of them and zero `<text>` elements.

    `docs/diagrams/mermaid-config.json` sets `htmlLabels: false`, and this is
    what stops a future render silently dropping it: the config is one flag on
    one command line, and nothing else would notice.
    """
    svgs = sorted(p for p in _tracked() if p.endswith(".svg") and p.startswith("docs/diagrams/"))
    assert svgs, "no rendered diagrams found — this guard would pass by having nothing to check"

    for svg in svgs:
        markup = (REPO / svg).read_text(encoding="utf-8")
        assert "foreignObject" not in markup, (
            f"{svg} carries HTML labels GitHub will strip — re-run `make diagrams`, "
            "which passes docs/diagrams/mermaid-config.json"
        )
        assert "<text" in markup, f"{svg} contains no <text> elements — its labels are not there"


@pytest.mark.parametrize("doc", ["README.md", "docs/ARCHITECTURE.md", "docs/ROADMAP.md"])
def test_the_documents_the_roadmap_names_are_present(doc: str) -> None:
    """A link checker passes trivially on a tree that lost its documents."""
    assert doc in _tracked()
