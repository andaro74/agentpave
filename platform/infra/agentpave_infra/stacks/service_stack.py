"""A scaffolded service's stack — the shape every `pave new` service deploys as.

One stack per component (ARCHITECTURE.md §4). This one is the golden path's
infrastructure half: everything the template promises about governance has to be
true in IAM, or the promise is a comment.

Three properties, each asserted in `platform/infra/tests/test_service_stack.py`
rather than trusted:

1. **Zero Bedrock.** The service reaches models only through the gateway
   (invariant 1), so its role holds no `bedrock:*` at all. Not scoped-down —
   absent. A scaffolded service that could invoke a model directly would route
   around the guardrail, the classification check, and the metering ledger in
   one step, and every quality claim this platform makes assumes it cannot.

2. **A permission boundary.** The role carries one, so a future edit — or a
   template someone forks — cannot widen it past what the boundary allows even
   if the inline policy says otherwise. This is the difference between "we did
   not grant Bedrock" and "this role cannot hold Bedrock."

3. **A budget alarm.** An agent loop that retries in a tight circle is a
   plausible defect and an expensive one. ADR-002 says nothing bills while
   idle; this is the other half — a ceiling on what a failure can cost while
   busy.
"""

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

# What a scaffolded service's role may ever hold, whatever its inline policy
# says. Written as an explicit allow-list rather than a Bedrock deny, because a
# deny-list is a promise to remember every future service someone might reach
# for; this is a promise to remember one thing.
BOUNDARY_ACTIONS = (
    "logs:CreateLogStream",
    "logs:PutLogEvents",
    "lambda:InvokeFunctionUrl",
    "execute-api:Invoke",
    "xray:PutTraceSegments",
    "xray:PutTelemetryRecords",
)

# Invocations in five minutes before the alarm fires. Sized for a demo: a
# healthy walkthrough makes a handful, and a runaway loop makes thousands.
INVOCATION_ALARM_THRESHOLD = 200

# The host a service points at before anything has wired it to the platform.
# Synth has to succeed with no AWS account (standing rule 4), so the gateway and
# MCP URLs cannot be cross-stack references — they are resolved from the other
# stacks' outputs by `make deploy-dev` and passed in. Until then the service
# points here, and `.invalid` is reserved by RFC 2606 precisely so that it can
# never accidentally resolve to somebody's server.
#
# It is named rather than inlined because the walkthrough checks the deployed
# function's environment for it: a service that deployed unwired must fail the
# gate as a configuration error, not as a DNS error three layers down inside a
# 502. That is what M04's second deployed walkthrough did.
UNWIRED_HOST = "unset.invalid"


class ServiceStack(Stack):
    """Lambda + Function URL for one scaffolded agent service."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        asset_path: str,
        service_name: str,
        package: str,
        gateway_url: str,
        mcp_url: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        log_group = logs.LogGroup(
            self,
            "ServiceLogGroup",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # The ceiling. Attached to the role below, so it constrains every
        # policy that role will ever carry — including ones added later by
        # someone who has not read this file.
        self.boundary = iam.ManagedPolicy(
            self,
            "ServiceBoundary",
            description=f"Permission boundary for {service_name}: no Bedrock, ever",
            statements=[
                iam.PolicyStatement(
                    actions=list(BOUNDARY_ACTIONS),
                    resources=["*"],
                ),
            ],
        )

        self.service_role = iam.Role(
            self,
            "ServiceRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            permissions_boundary=self.boundary,
            # ASCII only: CloudFormation rejects characters outside its allowed
            # set in a role description, so an em-dash here fails the deploy
            # rather than the synth.
            description=f"{service_name}: reaches models only through the gateway",
        )
        log_group.grant_write(self.service_role)

        # Calling the gateway and the MCP server is the whole permission set.
        # Both are IAM-authenticated Function URLs, so this is what "signed
        # requests" costs in policy terms (ADR-010).
        self.service_role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunctionUrl"],
                resources=[
                    f"arn:aws:lambda:{self.region}:{self.account}:function:*",
                ],
                conditions={"StringEquals": {"lambda:FunctionUrlAuthType": "AWS_IAM"}},
            )
        )

        self.service_function = lambda_.Function(
            self,
            "ServiceFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler=f"{package}.lambda_handler.handler",
            code=lambda_.Code.from_asset(asset_path),
            role=self.service_role,
            log_group=log_group,
            memory_size=512,
            timeout=Duration.seconds(60),
            # Tracing on: ADR-019 exports spans from the function itself, and
            # X-Ray gives the three hops somewhere to correlate.
            tracing=lambda_.Tracing.ACTIVE,
            environment={
                "AGENTPAVE_GATEWAY_URL": gateway_url,
                "AGENTPAVE_MCP_URL": mcp_url,
                # No model id, no guardrail id, no Bedrock region. The service
                # is not told how to reach a model because it must not be able
                # to.
                "AGENTPAVE_SERVICE_ID": service_name,
            },
        )

        self.function_url = self.service_function.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.AWS_IAM,
        )

        # A runaway agent loop is a plausible defect and an expensive one.
        # Alarms on invocation count rather than on cost, because a cost metric
        # lags by hours and this needs to fire while the loop is still running.
        self.runaway_alarm = cloudwatch.Alarm(
            self,
            "RunawayInvocationAlarm",
            metric=self.service_function.metric_invocations(period=Duration.minutes(5)),
            threshold=INVOCATION_ALARM_THRESHOLD,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description=(
                f"{service_name} invoked more than {INVOCATION_ALARM_THRESHOLD} "
                "times in five minutes: likely a retry loop"
            ),
        )

        CfnOutput(self, "ServiceUrl", value=self.function_url.url)
        CfnOutput(self, "ServiceRoleArn", value=self.service_role.role_arn)
        # Published so `make walkthrough` can ask X-Ray for this function's
        # traces by name. The generated name is not derivable from the stack
        # name, and a deployed gate that has to guess at an identifier is a
        # gate that silently checks the wrong resource.
        CfnOutput(self, "ServiceFunctionName", value=self.service_function.function_name)
        # Spans are written to stdout and land here (ADR-024). The walkthrough
        # reads this group to prove the service's *own* telemetry arrived —
        # X-Ray summaries could not, because Lambda emits a segment for every
        # invocation including one that died at import.
        CfnOutput(self, "ServiceLogGroupName", value=log_group.log_group_name)
