"""The eval service stack.

Deliberately small: one DynamoDB table holding baseline score summaries.

There is no Lambda here, and that is a decision rather than an omission. The
eval harness runs from CI and from a developer's laptop, both of which already
hold credentials and both of which need the scorecard in front of a human. A
Lambda would add a deployment artifact, a log group, and an execution role in
exchange for moving the same code somewhere nobody watches it. ARCHITECTURE.md
§3 describes the eval service as running "in CI + nightly Lambda"; the nightly
Lambda is M05's, arriving with `nightly-eval.yml` and the dashboard that reads
it. ADR-012 records the split.

What this stack does *not* grant is the load-bearing part: no role here carries
`bedrock:*`. The eval service reaches models only through the gateway
(ARCHITECTURE.md invariant 1), and the negative assertion in
`tests/test_eval_stack.py` is what keeps that true.
"""

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from constructs import Construct


class EvalStack(Stack):
    """Baseline score store for the eval service."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
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

        CfnOutput(self, "BaselineTableName", value=self.baseline_table.table_name)
