"""The eval service stack.

Deliberately small: one DynamoDB table holding baseline score summaries, and
one log group holding a line per run for the dashboard to chart.

There is no Lambda here, and that is a decision rather than an omission. The
eval harness runs from CI and from a developer's laptop, both of which already
hold credentials and both of which need the scorecard in front of a human. A
Lambda would add a deployment artifact and an execution role in exchange for
moving the same code somewhere nobody watches it. ARCHITECTURE.md §3 describes
the eval service as running "in CI + nightly Lambda"; the nightly is a scheduled
GitHub workflow instead, and ADR-012 records the split.

Which is exactly why the log group is here. Because the harness runs on a GitHub
runner rather than inside AWS, nothing writes its scores to CloudWatch as a side
effect of running — no Lambda, no execution role, no automatic log group. The
eval trend the dashboard charts exists only because `pave eval` puts a line here
on purpose (ADR-030).

The table and the group are asymmetric on purpose. **CI may write the log group
and may not write the table**: a scorecard line is a measurement, and a baseline
row is the bar that measurement is judged against. A run that scored badly must
be able to say so and must not be able to lower the bar to match (ADR-027).

What this stack does *not* grant is the load-bearing part: no role here carries
`bedrock:*`. The eval service reaches models only through the gateway
(ARCHITECTURE.md invariant 1), and the negative assertion in
`tests/test_eval_stack.py` is what keeps that true.
"""

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_logs as logs
from constructs import Construct

from agentpave_infra.log_groups import EVAL_LOG_STREAM


class EvalStack(Stack):
    """Baseline score store and scorecard log group for the eval service."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        log_group_name: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # On-demand only. A provisioned floor bills while idle, which ADR-002
        # forbids; the synth assertion pins the billing mode so a later edit
        # cannot quietly reintroduce one.
        self.baseline_table = dynamodb.Table(
            self,
            "BaselineTable",
            partition_key=dynamodb.Attribute(
                name="pk", type=dynamodb.AttributeType.STRING
            ),  # constant "baseline" — see baseline.PARTITION
            sort_key=dynamodb.Attribute(
                name="sk", type=dynamodb.AttributeType.STRING
            ),  # <iso8601>#<run_id>, so the newest row is a single reverse query
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ── Scorecard log group ───────────────────────────────────────────
        # Three months, not the one week the Lambda groups keep. Those hold
        # request lines, where a week is plenty to debug from; this one is the
        # only history behind the dashboard's eval trend, and a trend over seven
        # days of a suite that runs nightly is seven points. Storage is a few
        # kilobytes a run, so the retention costs approximately nothing —
        # ADR-002 is about standing charges, not about bytes.
        self.scorecard_log_group = logs.LogGroup(
            self,
            "ScorecardLogGroup",
            # Named, not generated: the dashboard has to name this group in a
            # Logs Insights query at synth time (ADR-031).
            log_group_name=log_group_name,
            retention=logs.RetentionDays.THREE_MONTHS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # The stream is created here rather than by the writer, so the CI role
        # needs `logs:PutLogEvents` and nothing else. Granting
        # `logs:CreateLogStream` instead would let a run open a stream of its own
        # naming anywhere in the group, and the point of this grant is that it is
        # the narrowest write in the platform (ADR-027).
        self.scorecard_log_stream = logs.LogStream(
            self,
            "ScorecardLogStream",
            log_group=self.scorecard_log_group,
            log_stream_name=EVAL_LOG_STREAM,
            removal_policy=RemovalPolicy.DESTROY,
        )

        CfnOutput(self, "BaselineTableName", value=self.baseline_table.table_name)
        # `pave eval` resolves the group from this output rather than from an
        # environment variable, for the same reason it resolves the baseline
        # table that way: the run writes to whatever was last deployed, and a
        # stale variable cannot point it at a group that no longer exists.
        CfnOutput(self, "ScorecardLogGroupName", value=self.scorecard_log_group.log_group_name)
        CfnOutput(self, "ScorecardLogStreamName", value=self.scorecard_log_stream.log_stream_name)
