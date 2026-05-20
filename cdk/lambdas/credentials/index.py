"""Credentials-vending Lambda.

Returns the JSON shape expected by AWS CLI / boto3 `credential_process`:
    {"Version": 1, "AccessKeyId": ..., "SecretAccessKey": ...,
     "SessionToken": ..., "Expiration": "<ISO-8601>"}

Invoked via API Gateway with Lambda proxy integration, so the return value
must include statusCode/body/headers.
"""

import json
import logging
import os
import sys

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ROLE_ARN = os.environ["BEDROCK_ROLE_ARN"]
ROLE_SESSION_NAME = os.environ.get("ROLE_SESSION_NAME", "bedrock-creds-session")

_sts = boto3.client("sts")


def lambda_handler(event, context):
    try:
        response = _sts.assume_role(
            RoleArn=ROLE_ARN,
            RoleSessionName=ROLE_SESSION_NAME,
        )
    except ClientError as e:
        logger.exception("AssumeRole failed")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "assume_role_failed", "detail": str(e)}),
            "headers": {"Content-Type": "application/json"},
        }

    creds = response["Credentials"]
    body = {
        "Version": 1,
        "AccessKeyId": creds["AccessKeyId"],
        "SecretAccessKey": creds["SecretAccessKey"],
        "SessionToken": creds["SessionToken"],
        "Expiration": creds["Expiration"].isoformat(),
    }
    return {
        "statusCode": 200,
        "body": json.dumps(body),
        "headers": {"Content-Type": "application/json"},
    }
