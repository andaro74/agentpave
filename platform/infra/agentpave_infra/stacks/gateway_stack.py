"""The LLM Gateway stack.

This stack is where ARCHITECTURE.md invariant 1 stops being a promise and
becomes a constraint: the gateway's execution role is the only role in the
platform carrying `bedrock:InvokeModel`, and it is written out explicitly here
rather than via a `grant_*` helper so that the permission set is small enough
to assert on character by character (see tests/test_gateway_stack.py).
"""

from agentpave_gateway.guardrail import load_policy
from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_bedrock as bedrock
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

from agentpave_infra.guardrail_render import (
    content_policy_config,
    sensitive_information_policy_config,
)


class GatewayStack(Stack):
    """Lambda + Function URL + metering table for the LLM gateway."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        asset_path: str,
        model_serve: str,
        model_judge: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── Metering table ────────────────────────────────────────────────
        # On-demand only. A provisioned floor would bill while idle, which
        # ADR-002 forbids; the assertion test pins the billing mode so a future
        # edit cannot quietly reintroduce one.
        self.metering_table = dynamodb.Table(
            self,
            "MeteringTable",
            partition_key=dynamodb.Attribute(
                name="pk", type=dynamodb.AttributeType.STRING
            ),  # service_id#feature_id
            sort_key=dynamodb.Attribute(
                name="sk", type=dynamodb.AttributeType.STRING
            ),  # <iso8601>#<request_id>
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # An explicit log group, not CDK's `log_retention` prop: that prop
        # synthesises a helper Lambda holding `logs:PutRetentionPolicy` on `*`,
        # which would put a second, broader role in a stack whose whole point
        # is a narrow one.
        log_group = logs.LogGroup(
            self,
            "GatewayLogGroup",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ── Guardrail ─────────────────────────────────────────────────────
        # Rendered from the authored YAML at synth time. The policy file is the
        # source of truth; this construct is its deployed form, and the gateway
        # only ever learns the resulting identifier. Enforcement is Bedrock's.
        policy = load_policy()
        self.guardrail = bedrock.CfnGuardrail(
            self,
            "GatewayGuardrail",
            name=f"{policy.name}-{construct_id.split('-')[-1]}",
            description=policy.description,
            blocked_input_messaging=policy.blocked_input_message,
            blocked_outputs_messaging=policy.blocked_output_message,
            content_policy_config=content_policy_config(policy),
            sensitive_information_policy_config=sensitive_information_policy_config(policy),
        )

        # DRAFT tracks the policy file: editing the YAML and redeploying moves
        # the live guardrail with it. Pinned numbered versions are the right
        # answer once more than one stage shares a guardrail, which is out of
        # scope for a single-account demo (ADR-001).
        guardrail_version = "DRAFT"

        # ── Execution role ────────────────────────────────────────────────
        self.gateway_role = iam.Role(
            self,
            "GatewayRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Sole holder of bedrock:InvokeModel in AgentPave",
        )
        log_group.grant_write(self.gateway_role)

        # Cross-region inference profiles (`us.anthropic.*`) resolve to
        # foundation models in several regions, so the model ARN is region-wild
        # by necessity. It stays scoped to Anthropic models — never `*`.
        self.gateway_role.add_to_policy(
            iam.PolicyStatement(
                sid="InvokeAnthropicModels",
                actions=["bedrock:InvokeModel"],
                resources=[
                    "arn:aws:bedrock:*::foundation-model/anthropic.*",
                    f"arn:aws:bedrock:*:{self.account}:inference-profile/*.anthropic.*",
                ],
            )
        )

        # Applying a guardrail on InvokeModel needs its own permission, scoped
        # to this guardrail. Without it the call fails closed — Bedrock refuses
        # the request rather than quietly serving it unguarded.
        self.gateway_role.add_to_policy(
            iam.PolicyStatement(
                sid="ApplyGatewayGuardrail",
                actions=["bedrock:ApplyGuardrail"],
                resources=[self.guardrail.attr_guardrail_arn],
            )
        )

        # PutItem only. `table.grant_write_data()` would also hand over
        # DeleteItem and UpdateItem; a metering ledger is append-only, and a
        # role that cannot delete rows cannot be used to erase its own trail.
        self.gateway_role.add_to_policy(
            iam.PolicyStatement(
                sid="WriteMeteringRows",
                actions=["dynamodb:PutItem"],
                resources=[self.metering_table.table_arn],
            )
        )

        # ── Function ──────────────────────────────────────────────────────
        self.gateway_function = lambda_.Function(
            self,
            "GatewayFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="agentpave_gateway.lambda_handler.handler",
            code=lambda_.Code.from_asset(asset_path),
            role=self.gateway_role,
            log_group=log_group,
            memory_size=512,
            timeout=Duration.seconds(30),
            environment={
                "AGENTPAVE_METERING_TABLE": self.metering_table.table_name,
                "AGENTPAVE_MODEL_SERVE": model_serve,
                "AGENTPAVE_MODEL_JUDGE": model_judge,
                # The handler refuses to invoke a model when these are unset:
                # an unguarded call is worse than no call.
                "AGENTPAVE_GUARDRAIL_ID": self.guardrail.attr_guardrail_id,
                "AGENTPAVE_GUARDRAIL_VERSION": guardrail_version,
            },
        )

        # IAM auth, not public. ROADMAP describes the smoke gate as "a curl";
        # an unauthenticated URL in front of Bedrock is not a thing to ship
        # even in a demo, so the smoke script signs its request instead.
        self.function_url = self.gateway_function.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.AWS_IAM,
        )

        # ── Outputs ───────────────────────────────────────────────────────
        # The smoke gate discovers the deployment through these rather than
        # taking hardcoded names on the command line, so `make smoke-gateway`
        # tests whatever was last deployed instead of what someone remembered.
        CfnOutput(self, "FunctionUrl", value=self.function_url.url)
        CfnOutput(self, "MeteringTableName", value=self.metering_table.table_name)
        CfnOutput(self, "GuardrailId", value=self.guardrail.attr_guardrail_id)
