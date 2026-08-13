"""Dashboard-as-code: the four panels ROADMAP M05 asks for.

Every panel is a **Logs Insights query**, not a CloudWatch metric. A custom
metric costs about $0.30 a month each, forever, whether or not anything runs;
the six this dashboard would need are a standing charge on an idle platform,
which is the one thing ADR-002 forbids. Logs Insights is billed per gigabyte
scanned when a query runs, and a query runs when somebody opens the dashboard —
so an unwatched dashboard costs storage measured in kilobytes (ADR-030).

One dashboard, not several. CloudWatch gives three per account free and charges
$3 a month for the fourth, so the count is an invariant with a test on it rather
than a preference.

**The queries below were written against rows that exist.** M05's first attempt
wrote them against the schema `telemetry.py` was intended to produce, which is
not the same document: `blocked_by` is a JSON *array* in the real line, so Logs
Insights addresses its first element as `blocked_by.0` and a query naming
`blocked_by` returns a column of blanks. The rows this was checked against are
quoted in `docs/VALIDATION.md` under "M05 status".

Two rules every query here follows:

* **Filter on the event marker.** Lambda's own START/END/REPORT lines share these
  groups, as does anything anyone ever prints. A query that matched "has a
  `service_id`" would silently widen the day something else grew one.
* **No panel invents a number.** The defect-leakage counter is maintained by
  hand, and its panel says so on its face — see `_defect_leakage` below.
"""

from aws_cdk import Duration, Stack
from aws_cdk import aws_cloudwatch as cloudwatch
from constructs import Construct

# ── the hand-maintained counter ───────────────────────────────────────────
#
# ARCHITECTURE.md §7 Q2 asks what the honest automated trigger for this counter
# would be. The answer recorded in ADR-032 is that there is not one: this
# platform has no production, so nothing can detect a defect in production. The
# number is therefore incremented by a person, and it lives here — in the stack —
# so that moving it is a reviewed commit with an author and a date rather than an
# edit in the console that leaves no trace.
#
# Deriving it from gate failures is forbidden and not merely discouraged: a gate
# that fails is a defect *caught*, and charting that as leakage would make the
# platform's working controls look like escapes (ADR-032).
#
# It reads 0 because nothing has leaked, not because nothing is counted. The
# panel prints that distinction, because a hand-cranked number that looks
# measured is worse than an empty panel.
DEFECTS_LEAKED = 0
DEFECTS_LEAKED_LAST_REVIEWED = "2026-08-12"

# The window every panel opens on. A fortnight, set by the trend: it is the
# shortest span in which the word means anything, since a nightly suite produces
# one point a day. The three gateway panels inherit it and lose nothing — that
# group keeps a week, so there is nothing behind day seven for them to scan.
#
# Not three months, even though the scorecard group retains that long. Logs
# Insights charges per gigabyte scanned on every page load, so the default window
# is the one recurring cost this dashboard has (ADR-030). A reader who wants the
# full quarter widens it in the console, deliberately, once.
DEFAULT_WINDOW = Duration.days(14)

# Full width, in CloudWatch's 24-column grid.
FULL = 24
HALF = 12


class DashboardStack(Stack):
    """The eval trend, spend per service, guardrail interventions, and leakage."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        stage: str,
        gateway_log_group: str,
        eval_log_group: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Held as attributes so the drift test can assert the dashboard queries
        # the groups the other stacks actually create (ADR-031).
        self.gateway_log_group = gateway_log_group
        self.eval_log_group = eval_log_group

        self.dashboard = cloudwatch.Dashboard(
            self,
            "Dashboard",
            dashboard_name=f"AgentPave-{stage}",
            default_interval=DEFAULT_WINDOW,
            widgets=[
                [self._heading(stage)],
                [self._eval_trend()],
                [self._spend_per_service(), self._guardrail_interventions()],
                [self._defect_leakage()],
            ],
        )

    # ── panels ────────────────────────────────────────────────────────────

    def _heading(self, stage: str) -> cloudwatch.TextWidget:
        """What this dashboard reads, named on the page.

        A reader who finds a panel empty needs to know whether the platform was
        quiet or the query is pointed at the wrong group, and that question is
        unanswerable from a chart alone.
        """
        return cloudwatch.TextWidget(
            markdown="\n".join(
                [
                    f"# AgentPave — {stage}",
                    "",
                    "> The paved road provides. The quality gate decides.",
                    "",
                    "Every panel below is a Logs Insights query over one of two log "
                    "groups — there are no custom metrics in this platform, because "
                    "six of them would bill while idle (ADR-030, ADR-002).",
                    "",
                    f"* gateway requests — `{self.gateway_log_group}`",
                    f"* eval scorecards — `{self.eval_log_group}`",
                    "",
                    "An empty panel means nothing ran in the window. It does not mean "
                    "nothing was measured.",
                ]
            ),
            width=FULL,
            height=6,
        )

    def _eval_trend(self) -> cloudwatch.LogQueryWidget:
        """Pass rate per day, from the lines `pave eval` writes — best and worst.

        `avg` is rejected: several runs land on one day — a pull request's gate,
        a re-run after the fix, the nightly — and averaging them charts a day's
        worst moment into the trend forever.

        `max` alone was the original answer, and it was wrong in a way only the
        data showed. On 2026-08-13 the gate blocked a pull request at 29/31 and
        the panel rendered 100 → 93.5, the first regression it had ever drawn.
        Three passing runs later the same UTC day, the bucket became
        `max(0.935, 1, 1, 1)` and the dip vanished. The fix-and-re-run cycle
        guarantees that: `max` by day deletes precisely the regressions that
        were *repaired*, and keeps only the ones nobody got to before midnight.

        That inverts ADR-030's own rule — a line is written per graded run,
        passing or failing, because "a trend that dropped its failures would
        chart a platform that never regressed". The write side kept them; this
        query threw them away.

        So both, over the same bin (ADR-038): `best_pct` answers "does the suite
        still pass", `worst_pct` makes a regression permanent on the day it
        happened. The cost is that a flake now leaves an identical mark, which
        is the correct direction to be wrong in — a false dip sends someone to
        the run history, a hidden one sends nobody anywhere.
        """
        return cloudwatch.LogQueryWidget(
            title="Eval trend — golden-set pass rate (%) by day, best and worst run",
            log_group_names=[self.eval_log_group],
            view=cloudwatch.LogQueryVisualizationType.LINE,
            query_lines=[
                'filter event = "agentpave.eval.scorecard"',
                "stats max(pass_rate) * 100 as best_pct,",
                "      min(pass_rate) * 100 as worst_pct by bin(1d)",
            ],
            width=FULL,
            height=6,
        )

    def _spend_per_service(self) -> cloudwatch.LogQueryWidget:
        """Tokens and cost, grouped by the service that spent them.

        A table rather than a chart. At this scale there are two callers — the
        agent and the eval harness — and the useful question is "which of them is
        the money", which a two-row table answers and a stacked area chart
        decorates.

        **Every alias differs from the field it aggregates**, and that is not
        style. `sum(input_tokens) as input_tokens` aliases an aggregate to the
        name of the field it reads, and Logs Insights does not resolve the
        self-reference: the column renders **empty**. The first deployed run of
        this panel showed exactly that — `requests` populated from
        `count() as requests`, which introduces a new name, and all three
        `sum()` columns blank. `sort` on a blank column then ordered the table
        arbitrarily, which was the tell.

        Nothing hermetic could see it. The synth assertions checked that the
        query says `sum(cost_usd)` and groups by `service_id`, and it did both.
        A test now forbids the self-aliasing pattern itself.
        """
        return cloudwatch.LogQueryWidget(
            title="Tokens and cost per service",
            log_group_names=[self.gateway_log_group],
            view=cloudwatch.LogQueryVisualizationType.TABLE,
            query_lines=[
                'filter event = "agentpave.gateway.request"',
                "stats count() as requests,"
                " sum(input_tokens) as tokens_in,"
                " sum(output_tokens) as tokens_out,"
                " sum(cost_usd) as spend_usd"
                " by service_id, feature_id",
                "sort spend_usd desc",
            ],
            width=HALF,
            height=6,
        )

    def _guardrail_interventions(self) -> cloudwatch.LogQueryWidget:
        """What was refused, by stage and by filter type.

        `blocked_by.0` because the field is an array — see the module docstring.
        Only the first filter is charted; a request blocked by two filters at
        once is counted under the first Bedrock named, which understates the
        long tail and is the reason the raw rows stay queryable rather than being
        summarised into a metric.

        `stage` is grouped as well as the filter, because "refused at the
        classification gate" and "refused by a content filter" are different
        events with different meanings — one is policy, the other is the model's
        input. A count that merged them would report a guardrail intervention
        rate that includes refusals no guardrail was involved in.
        """
        return cloudwatch.LogQueryWidget(
            title="Guardrail interventions — refusals by stage and filter",
            log_group_names=[self.gateway_log_group],
            view=cloudwatch.LogQueryVisualizationType.TABLE,
            query_lines=[
                'filter event = "agentpave.gateway.request" and outcome = "refused"',
                "stats count() as refusals by stage, blocked_by.0",
                "sort refusals desc",
            ],
            width=HALF,
            height=6,
        )

    def _defect_leakage(self) -> cloudwatch.TextWidget:
        """The counter, and the admission that it is hand-cranked.

        This panel is text and not a query because there is nothing to query.
        Writing it as a metric at zero would render an identical-looking number
        with an implied provenance it does not have, and the first person to read
        it would reasonably believe something was watching.
        """
        return cloudwatch.TextWidget(
            markdown="\n".join(
                [
                    "## Defect leakage",
                    "",
                    f"# {DEFECTS_LEAKED}",
                    "",
                    "**Maintained by hand.** This number is a constant in "
                    "`platform/infra/agentpave_infra/stacks/dashboard_stack.py`, "
                    "incremented by a person in a reviewed commit. Nothing detects "
                    "it automatically, and nothing can: this platform has no "
                    "production, so there is no such thing here as a defect found "
                    "in production. ARCHITECTURE.md §7 Q2 asked what the honest "
                    "automated trigger would be — ADR-032 answers that there is "
                    "not one at this scale, and forbids deriving one from the "
                    "gate's own failures — a gate that fails is a defect caught.",
                    "",
                    f"Last reviewed by a human: **{DEFECTS_LEAKED_LAST_REVIEWED}**. "
                    "A stale review date is the failure mode to watch for; the "
                    "number staying at zero is not evidence on its own.",
                ]
            ),
            width=FULL,
            height=6,
        )
