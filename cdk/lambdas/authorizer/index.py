"""Custom API Gateway REQUEST authorizer for the credentials API.

Validates the `x-api-key` header against a value stored in Secrets Manager,
and optionally restricts the source IP to a configured CIDR allow-list.

Returns an IAM policy as required by REQUEST-type authorizers on REST APIs.
"""

import ipaddress
import json
import logging
import os
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SECRET_ARN = os.environ["API_KEY_SECRET_ARN"]
IP_RANGES = [
    ipaddress.ip_network(c)
    for c in json.loads(os.environ.get("EXPECTED_IP_RANGE", '["0.0.0.0/0"]'))
]

_secrets = boto3.client("secretsmanager")


@lru_cache(maxsize=1)
def _api_key() -> str:
    """Fetch the API key from Secrets Manager. Cached per container lifetime."""
    return _secrets.get_secret_value(SecretId=SECRET_ARN)["SecretString"]


def _policy(effect: str, resource: str, principal: str = "client") -> dict:
    return {
        "principalId": principal,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": effect,
                    "Resource": resource,
                }
            ],
        },
    }


def _source_ip_allowed(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in IP_RANGES)


def lambda_handler(event, context):
    headers = event.get("headers") or {}
    # API Gateway lower-cases header names in REQUEST authorizer events, but be defensive.
    submitted = headers.get("x-api-key") or headers.get("X-Api-Key")
    method_arn = event.get("methodArn", "*")

    try:
        expected = _api_key()
    except ClientError:
        logger.exception("Failed to read API key secret")
        return _policy("Deny", method_arn)

    if not submitted or submitted != expected:
        return _policy("Deny", method_arn)

    source_ip = (
        event.get("requestContext", {}).get("identity", {}).get("sourceIp")
        or ""
    )
    if source_ip and not _source_ip_allowed(source_ip):
        logger.info("Rejecting request from disallowed source IP: %s", source_ip)
        return _policy("Deny", method_arn)

    return _policy("Allow", method_arn)
