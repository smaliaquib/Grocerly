from aws_cdk import Stack, Duration, CfnOutput
from aws_cdk.aws_dynamodb import Table
from aws_cdk.aws_lambda import Runtime, Tracing
from aws_cdk.aws_lambda_python_alpha import PythonFunction
from aws_cdk.aws_secretsmanager import Secret
from constructs import Construct
from cdklabs.generative_ai_cdk_constructs.bedrock import (
    Agent,
    BedrockFoundationModel,
    AgentActionGroup,
    ActionGroupExecutor,
    Guardrail,
    Topic,
    ApiSchema,
    AgentAlias,
)


class AiAgentStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        secret: Secret,
        invoke_agent_lambda: PythonFunction,
        ecommerce_table: Table,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        agent_lambda_function = PythonFunction(
            self,
            "AgentLambdaFunction",
            runtime=Runtime.PYTHON_3_11,
            tracing=Tracing.ACTIVE,
            entry="./agent",
            index="app.py",
            handler="lambda_handler",
            timeout=Duration.minutes(2),
            memory_size=512,
        )
        secret.grant_read(agent_lambda_function)
        agent_lambda_function.add_environment(
            "ECOMMERCE_TABLE_NAME", ecommerce_table.table_name
        )
        # Bedrock AI Agent
        agent = Agent(
            self,
            "Agent",
            foundation_model=BedrockFoundationModel.ANTHROPIC_CLAUDE_3_5_SONNET_V1_0,
            instruction="You are a helpful and friendly AI assistant.",
            should_prepare_agent=True,
        )

        executor_group = ActionGroupExecutor.fromlambda_function(
            lambda_function=agent_lambda_function
        )

        # agent action group

        action_group = AgentActionGroup(
            name="GreatCustomerSupport",
            description="Use these functions for customer support",
            executor=executor_group,
            enabled=True,
            api_schema=ApiSchema.from_local_asset("./agent/openapi.json"),
        )

        agent_alias = AgentAlias(
            self,
            "AgentAlias",
            agent=agent,
            description="Alias for description",
            alias_name="grocery_agent_alias",
        )

        ecommerce_table.grant_full_access(agent_lambda_function)

        agent.add_action_group(action_group)

        # Guardrails
        agent_guardrail = Guardrail(
            self,
            id="grocery-agent-guardrail-001",
            name="GroceryAgentManagementGuardrail",
            description="Guardrails for secure interactions.",
            denied_topics=[
                Topic(
                    name="FinancialFraud",
                    examples=[
                        "Requests to create fake products, or generate unauthorized payment links."
                    ],
                    definition="Prevent any actions that could lead to fraud or misuse of Stripe's payment system.",
                ),
                Topic(
                    name="DataLeakage",
                    examples=[
                        "Requests to expose sensitive customer data, such as credit card numbers or PII."
                    ],
                    definition="Block any actions that could result in the leakage of sensitive or (PII).",
                ),
                Topic(
                    name="UnauthorizedAccess",
                    examples=[
                        "Requests to access or modify products or payment links without proper authentication."
                    ],
                    definition="Prevent unauthorized access to Stripe or DynamoDB resources.",
                ),
                Topic(
                    name="MaliciousCodeExecution",
                    examples=[
                        "Requests to execute code, scripts, or commands on the server or database."
                    ],
                    definition="Block any attempts to execute malicious code or scripts.",
                ),
            ],
            blocked_outputs_messaging="Your request cannot be processed due to security restrictions.",
            blocked_input_messaging="Your input contains restricted content. Please revise your request.",
        )
        agent.add_guardrail(agent_guardrail)

        invoke_agent_lambda.add_environment("AGENT_ID", agent.agent_id)
        invoke_agent_lambda.add_environment("AGENT_ALIAS", agent_alias.alias_id)

        (CfnOutput(self, "AGENT_ALIAS", value=agent_alias.alias_id),)
