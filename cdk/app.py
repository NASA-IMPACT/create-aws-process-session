#!/usr/bin/env python3
"""CDK entry point.

Account and region are resolved from the caller's AWS credentials via
CDK_DEFAULT_ACCOUNT / CDK_DEFAULT_REGION (set automatically by the CDK CLI
when you run `cdk deploy --profile <name>` or rely on the default chain).
You can also override explicitly:

    cdk deploy -c account=123456789012 -c region=us-east-1

To restrict the API to a network allow-list, pass one or more CIDRs as a
comma-separated context value (defaults to 0.0.0.0/0 = open):

    cdk deploy -c ip_allow_list=156.68.128.0/22
    cdk deploy -c ip_allow_list=156.68.128.0/22,10.0.0.0/8
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
ip_allow_list_raw = app.node.try_get_context("ip_allow_list") or "0.0.0.0/0"
ip_allow_list = [c.strip() for c in ip_allow_list_raw.split(",") if c.strip()]

BedrockCredsStack(
    app,
    "BedrockCredsStack",
    env=cdk.Environment(account=account, region=region),
    description="Bedrock credential-vending API (API Gateway + Lambda + AssumeRole).",
    ip_allow_list=ip_allow_list,
)

app.synth()
