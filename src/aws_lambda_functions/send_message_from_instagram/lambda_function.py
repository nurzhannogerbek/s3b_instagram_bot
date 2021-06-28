import logging
import os
from typing import *
import json

# Configure the logging tool in the AWS Lambda function.
logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)

# Initialize constants with parameters to configure
INSTAGRAM_BOT_VERIFY_TOKEN = os.environ["INSTAGRAM_BOT_VERIFY_TOKEN"]


def set_webhook_for_instagram(query_string_parameters: Dict[AnyStr, Any]) -> Dict[AnyStr, Any]:
    # Define all necessary variables.
    hub_mode = query_string_parameters.get("hub.mode", None)
    hub_challenge = query_string_parameters.get("hub.challenge", None)
    hub_verify_token = query_string_parameters.get("hub.verify_token", None)

    # Check the values of query parameters.
    if hub_mode == "subscribe" and hub_challenge:
        # Check the verify token value.
        if hub_verify_token != INSTAGRAM_BOT_VERIFY_TOKEN:
            return {
                "statusCode": 403,
                "body": "Verification token mismatch. Check your 'VERIFY_TOKEN'."
            }

        # You must echo back the "hub.challenge" value, when the endpoint is registered as a webhook.
        return {
            "statusCode": 200,
            "body": hub_challenge
        }

    # Return the status code 200.
    return {
        "statusCode": 200
    }


def lambda_handler(event, context):
    """
    :param event: The AWS Lambda function uses this parameter to pass in event data to the handler.
    :param context: The AWS Lambda function uses this parameter to provide runtime information to your handler.
    """
    # Define the http method of the request.
    try:
        http_method = event["requestContext"]["http"]["method"]
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Check the http method of the request.
    if http_method == "GET":
        # Define query parameters.
        try:
            query_string_parameters = event["queryStringParameters"]
        except Exception as error:
            logger.error(error)
            raise Exception(error)

        # Set the webhook for facebook messenger.
        response = set_webhook_for_instagram(query_string_parameters)
    elif http_method == "POST":
        # Define the JSON object body.
        try:
            body = json.loads(event["body"])
        except Exception as error:
            logger.error(error)
            raise Exception(error)

        print(body)

        # Return the status code 200.
        response = {
            "statusCode": 200
        }
    else:
        # Return the status code 500.
        response = {
            "statusCode": 500,
            "body": "Unexpected HTTP method."
        }

    # Return response.
    return response
