"""The tvmaze-catalog MCP stack.

One stack per component (ARCHITECTURE.md §4). The interesting property here is
what the role does *not* hold: this component reaches no model, so it carries
no Bedrock permission at all. That is ADR-003 migration constraint (a) — "the
agent holds zero direct Bedrock permissions; the gateway is its only model
path" — enforced on the second component rather than assumed.
"""

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

# Where the ASGI app serves MCP, without the leading slash: the function URL
# already ends in one. Kept in step with the handler's STREAMABLE_HTTP_PATH by
# a test rather than by an import, so the deployed asset does not have to
# depend on the infra package to stay honest.
MCP_PATH = "mcp"


class McpTvmazeStack(Stack):
    """Lambda + Function URL serving MCP over streamable HTTP."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        asset_path: str,
        agent_id: str = "catalog-agent",
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        log_group = logs.LogGroup(
            self,
            "McpLogGroup",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # No managed policies, no grants beyond its own log group. A tool
        # server that reads a public API needs nothing else, and saying so in
        # IAM is what makes the claim checkable.
        self.mcp_role = iam.Role(
            self,
            "McpRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            # ASCII only: CloudFormation rejects characters outside its
            # allowed set in a role description, and an em-dash here would
            # fail the deploy rather than the synth.
            description="tvmaze-catalog MCP server: holds no Bedrock access by design",
        )
        log_group.grant_write(self.mcp_role)

        self.mcp_function = lambda_.Function(
            self,
            "McpFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="agentpave_mcp_tvmaze.lambda_handler.handler",
            code=lambda_.Code.from_asset(asset_path),
            role=self.mcp_role,
            log_group=log_group,
            memory_size=512,
            timeout=Duration.seconds(30),
            environment={
                # The service identity Cedar authorizes. Not caller-supplied:
                # a header anyone could set is not an identity (ADR-008).
                "AGENTPAVE_AGENT_ID": agent_id,
                # Fixtures, not live TVMaze — `make conformance` asserts on
                # specific shows, and a live catalogue would make the deployed
                # gate go red overnight for reasons unrelated to the platform.
                "AGENTPAVE_TVMAZE_MODE": "fixtures",
            },
        )

        self.function_url = self.mcp_function.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.AWS_IAM,
        )

        # The MCP endpoint, not the bare function URL. The ASGI app serves MCP
        # at /mcp, so publishing the root would hand every consumer — including
        # `make conformance` — a URL that 404s. `function_url.url` ends in "/".
        CfnOutput(self, "McpUrl", value=f"{self.function_url.url}{MCP_PATH}")
