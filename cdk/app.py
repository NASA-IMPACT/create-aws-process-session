#!/usr/bin/env python3
"""CDK entry point.

Account and region are resolved from the caller's AWS credentials via
CDK_DEFAULT_ACCOUNT / CDK_DEFAULT_REGION (set automatically by the CDK CLI
when you run `cdk deploy --profile <name>` or rely on the default chain).
You can also override explicitly:

    cdk deploy -c account=123456789012 -c region=us-east-1
"""
import os

import aws_cdk as cdk

from stacks.bedrock_creds_stack import BedrockCredsStack

app = cdk.App()

account = app.node.try_get_context("account") or os.environ.get("CDK_DEFAULT_ACCOUNT")
region = (
    app.node.try_get_context("region")
    or os.environ.get("CDK_DEFAULT_REGION")
    or "us-west-2"
)

BedrockCredsStack(
    app,
    "BedrockCredsStack",
    env=cdk.Environment(account=account, region=region),
    description="Bedrock credential-vending API (API Gateway + Lambda + AssumeRole).",
)

app.synth()
