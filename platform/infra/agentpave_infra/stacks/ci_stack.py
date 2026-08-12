"""The identity GitHub Actions assumes. No long-lived keys anywhere.

M05 puts the quality gate in CI, and L2 and L5 need AWS. The obvious way to
give a workflow AWS access is an access key in a repository secret, and it is
the wrong way: a key in a secret is a credential that exists whether or not a
workflow is running, cannot be scoped to a branch, and survives every rotation
policy nobody remembers. OIDC issues a token per run, scoped to this repository
and to the refs named below, and there is nothing to leak between runs.

Its own stack, per CLAUDE.md's one-stack-per-component rule. CI identity is not
part of the gateway, the registry, or the eval service — folding it into any of
them would tie the credential CI depends on to the deploy lifecycle of a
component it has no relationship with (ADR-027).

**The role cannot write a baseline, and that is the point.** It can read the bar
and be blocked by it; it cannot move it. `dynamodb:PutItem` is absent, so a run
that scored badly has no way to record itself as the new standard — the failure
mode `is_recordable` guards against inside the process, closed here in IAM as
well. Setting the bar stays a deliberate act: a human running
`make seed-baseline` (ADR-027).
"""

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_iam as iam
from constructs import Construct

# GitHub's OIDC issuer. The audience is fixed by the official
# `aws-actions/configure-aws-credentials` action.
GITHUB_ISSUER = "token.actions.githubusercontent.com"
GITHUB_AUDIENCE = "sts.amazonaws.com"

# Certificate thumbprints for the issuer. AWS no longer validates these for
# providers backed by a well-known CA, but CloudFormation still takes the
# property, and an empty list is a deploy-time failure rather than a synth one.
GITHUB_THUMBPRINTS = (
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
)


class CiStack(Stack):
    """An OIDC provider and one role, scoped to this repository."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        repository: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # `CfnOIDCProvider`, not the `OpenIdConnectProvider` construct: the
        # latter is backed by a custom resource, which means a Lambda function
        # and its role exist in this account forever to manage a thing
        # CloudFormation supports natively.
        self.provider = iam.CfnOIDCProvider(
            self,
            "GitHubOidcProvider",
            url=f"https://{GITHUB_ISSUER}",
            client_id_list=[GITHUB_AUDIENCE],
            thumbprint_list=list(GITHUB_THUMBPRINTS),
        )

        # Which workflows may assume this role. Two subjects rather than
        # `repo:owner/name:*`, because the wildcard also matches
        # `environment:` and `ref:refs/tags/*` subjects — anyone who can push a
        # tag could then assume the role. Pull requests and main are what the
        # gate and the nightly run on.
        self.subjects = (
            f"repo:{repository}:pull_request",
            f"repo:{repository}:ref:refs/heads/main",
        )

        self.ci_role = iam.Role(
            self,
            "CiRole",
            assumed_by=iam.WebIdentityPrincipal(
                self.provider.attr_arn,
                conditions={
                    "StringEquals": {f"{GITHUB_ISSUER}:aud": GITHUB_AUDIENCE},
                    # `StringLike` on `sub` rather than `StringEquals`, because
                    # the two subjects are a list. The values themselves carry
                    # no wildcard.
                    "StringLike": {f"{GITHUB_ISSUER}:sub": list(self.subjects)},
                },
            ),
            # ASCII only: CloudFormation rejects characters outside its allowed
            # set in a role description.
            description="AgentPave CI: runs the quality gate, cannot move the bar",
        )

        # Reading the deployment. `pave eval` discovers the gateway URL and the
        # baseline table from stack outputs rather than from hardcoded names,
        # so the gate tests whatever was last deployed.
        self.ci_role.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudformation:DescribeStacks"],
                resources=[
                    f"arn:aws:cloudformation:{self.region}:{self.account}:stack/AgentPave-*/*"
                ],
            )
        )

        # Calling the gateway. Both actions, because an IAM-authed function URL
        # takes both since October 2025 — the defect that cost M04 an afternoon
        # (ADR-025), and the reason this is written as two statements here
        # rather than rediscovered as a 403 in a CI run nobody can debug.
        gateway_functions = f"arn:aws:lambda:{self.region}:{self.account}:function:AgentPave-*"
        self.ci_role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunctionUrl"],
                resources=[gateway_functions],
                conditions={"StringEquals": {"lambda:FunctionUrlAuthType": "AWS_IAM"}},
            )
        )
        self.ci_role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[gateway_functions],
                conditions={"Bool": {"lambda:InvokedViaFunctionUrl": "true"}},
            )
        )

        # Reading the bar. Query only — see the class docstring and ADR-027.
        self.ci_role.add_to_policy(
            iam.PolicyStatement(
                actions=["dynamodb:Query"],
                resources=[f"arn:aws:dynamodb:{self.region}:{self.account}:table/AgentPave-Eval-*"],
            )
        )

        CfnOutput(
            self,
            "CiRoleArn",
            value=self.ci_role.role_arn,
            description="Set as the GitHub repository variable AWS_CI_ROLE_ARN",
        )
