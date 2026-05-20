"""CDK stack: API Gateway + Lambda credential-vending service for Bedrock.

Researchers send a GET to /credentials with an `x-api-key` header. A custom
authorizer Lambda validates the key against a secret in Secrets Manager (and
optionally restricts source IPs). A second Lambda then calls sts:AssumeRole on
a role scoped to bedrock invoke permissions and returns the short-lived creds
as JSON. The shape matches what AWS CLI / boto3 `credential_process` expects.
"""

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigateway as apigw,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct


class BedrockCredsStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # 1) API key stored in Secrets Manager (improvement over plain Lambda env).
        api_key_secret = secretsmanager.Secret(
            self,
            "ApiKeySecret",
            secret_name="bedrock-creds/api-key",
            description="x-api-key header value required by the credentials API.",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                exclude_punctuation=True,
                password_length=40,
            ),
        )

        # 2) Execution role for the credentials-vending Lambda.
        creds_exec_role = iam.Role(
            self,
            "CredsLambdaExecRole",
            role_name="bedrock-creds-credentials-lambda",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Execution role for the credentials-vending Lambda.",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )

        # 3) The role researchers ultimately get credentials for: Bedrock invoke only.
        #    Scoped to anthropic + meta foundation models in this region, plus any
        #    cross-region inference profile in this account.
        bedrock_role = iam.Role(
            self,
            "BedrockAccessRole",
            role_name="bedrock-creds-bedrock-access",
            assumed_by=iam.ArnPrincipal(creds_exec_role.role_arn),
            description="Short-lived role researchers receive; can invoke Bedrock.",
            max_session_duration=Duration.hours(1),
            inline_policies={
                "bedrock-invoke": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "bedrock:InvokeModel",
                                "bedrock:InvokeModelWithResponseStream",
                                "bedrock:Converse",
                                "bedrock:ConverseStream",
                            ],
                            resources=[
                                # Cross-region inference profiles route to underlying
                                # foundation models in any of the US regions, so we
                                # allow any region for the model ARN itself. Model
                                # family is still scoped (anthropic, meta).
                                "arn:aws:bedrock:*::foundation-model/anthropic.*",
                                "arn:aws:bedrock:*::foundation-model/meta.*",
                                f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/*",
                            ],
                        )
                    ]
                )
            },
        )
        creds_exec_role.add_to_policy(
            iam.PolicyStatement(
                actions=["sts:AssumeRole"],
                resources=[bedrock_role.role_arn],
            )
        )

        # 4) Credentials Lambda: calls sts:AssumeRole, returns the creds JSON.
        creds_fn = lambda_.Function(
            self,
            "CredentialsLambda",
            function_name="bedrock-creds-credentials",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.lambda_handler",
            code=lambda_.Code.from_asset("lambdas/credentials"),
            role=creds_exec_role,
            timeout=Duration.seconds(10),
            memory_size=128,
            log_retention=logs.RetentionDays.ONE_MONTH,
            environment={
                "BEDROCK_ROLE_ARN": bedrock_role.role_arn,
                "ROLE_SESSION_NAME": "bedrock-creds-session",
            },
        )

        # 5) Authorizer Lambda: validates x-api-key against Secrets Manager.
        authorizer_exec_role = iam.Role(
            self,
            "AuthorizerLambdaExecRole",
            role_name="bedrock-creds-authorizer-lambda",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )
        authorizer_fn = lambda_.Function(
            self,
            "AuthorizerLambda",
            function_name="bedrock-creds-authorizer",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.lambda_handler",
            code=lambda_.Code.from_asset("lambdas/authorizer"),
            role=authorizer_exec_role,
            timeout=Duration.seconds(5),
            memory_size=128,
            log_retention=logs.RetentionDays.ONE_MONTH,
            environment={
                "API_KEY_SECRET_ARN": api_key_secret.secret_arn,
                "EXPECTED_IP_RANGE": '["0.0.0.0/0"]',
            },
        )
        api_key_secret.grant_read(authorizer_fn)

        # 6) API Gateway REST API + /credentials [GET] with the custom authorizer.
        api = apigw.RestApi(
            self,
            "BedrockCredsApi",
            rest_api_name="bedrock-creds-api",
            description="Vends short-lived AWS credentials for Bedrock invoke.",
            deploy_options=apigw.StageOptions(
                stage_name="prod",
                throttling_rate_limit=10,
                throttling_burst_limit=20,
                logging_level=apigw.MethodLoggingLevel.INFO,
                metrics_enabled=True,
            ),
            cloud_watch_role=True,
        )

        authorizer = apigw.RequestAuthorizer(
            self,
            "ApiKeyAuthorizer",
            handler=authorizer_fn,
            identity_sources=[apigw.IdentitySource.header("x-api-key")],
            results_cache_ttl=Duration.minutes(5),
            authorizer_name="bedrock-creds-authorizer",
        )

        credentials_resource = api.root.add_resource("credentials")
        credentials_resource.add_method(
            "GET",
            integration=apigw.LambdaIntegration(creds_fn, proxy=True),
            authorizer=authorizer,
            request_parameters={
                "method.request.header.x-api-key": True,
            },
        )

        # 7) Usage plan with throttling + daily quota.
        usage_plan = api.add_usage_plan(
            "DefaultUsagePlan",
            name="bedrock-creds-default",
            throttle=apigw.ThrottleSettings(rate_limit=10, burst_limit=20),
            quota=apigw.QuotaSettings(limit=10_000, period=apigw.Period.DAY),
        )
        usage_plan.add_api_stage(stage=api.deployment_stage)

        # 8) Outputs for operators.
        CfnOutput(
            self,
            "ApiUrl",
            value=f"{api.url}credentials",
            description="Value for AWS_GET_TEMP_CREDS_API_URL.",
        )
        CfnOutput(
            self,
            "ApiKeySecretArn",
            value=api_key_secret.secret_arn,
            description="Read with: aws secretsmanager get-secret-value --secret-id <arn>",
        )
        CfnOutput(
            self,
            "BedrockRoleArn",
            value=bedrock_role.role_arn,
            description="Role researchers' temp credentials assume.",
        )
