import logging
import os
import uuid
from psycopg2.extras import RealDictCursor
from functools import wraps
from typing import *
import json
from threading import Thread
from queue import Queue
import requests
import databases

# Configure the logging tool in the AWS Lambda function.
logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)

# Initialize constants with parameters to configure.
POSTGRESQL_USERNAME = os.environ["POSTGRESQL_USERNAME"]
POSTGRESQL_PASSWORD = os.environ["POSTGRESQL_PASSWORD"]
POSTGRESQL_HOST = os.environ["POSTGRESQL_HOST"]
POSTGRESQL_PORT = int(os.environ["POSTGRESQL_PORT"])
POSTGRESQL_DB_NAME = os.environ["POSTGRESQL_DB_NAME"]
FACEBOOK_API_URL = "https://graph.facebook.com/v11.0"
APPSYNC_CORE_API_URL = os.environ["APPSYNC_CORE_API_URL"]
APPSYNC_CORE_API_KEY = os.environ["APPSYNC_CORE_API_KEY"]

# The connection to the database will be created the first time the AWS Lambda function is called.
# Any subsequent call to the function will use the same database connection until the container stops.
POSTGRESQL_CONNECTION = None


def run_multithreading_tasks(functions: List[Dict[AnyStr, Union[Callable, Dict[AnyStr, Any]]]]) -> Dict[AnyStr, Any]:
    # Create the empty list to save all parallel threads.
    threads = []

    # Create the queue to store all results of functions.
    queue = Queue()

    # Create the thread for each function.
    for function in functions:
        # Check whether the input arguments have keys in their dictionaries.
        try:
            function_object = function["function_object"]
        except KeyError as error:
            logger.error(error)
            raise Exception(error)
        try:
            function_arguments = function["function_arguments"]
        except KeyError as error:
            logger.error(error)
            raise Exception(error)

        # Add the instance of the queue to the list of function arguments.
        function_arguments["queue"] = queue

        # Create the thread.
        thread = Thread(target=function_object, kwargs=function_arguments)
        threads.append(thread)

    # Start all parallel threads.
    for thread in threads:
        thread.start()

    # Wait until all parallel threads are finished.
    for thread in threads:
        thread.join()

    # Get the results of all threads.
    results = {}
    while not queue.empty():
        results = {**results, **queue.get()}

    # Return the results of all threads.
    return results


def check_input_arguments(**kwargs) -> None:
    # Make sure that all the necessary arguments for the AWS Lambda function are present.
    try:
        queue = kwargs["queue"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        input_arguments = kwargs["body"]["arguments"]["input"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Check the format and values of required arguments in the list of input arguments.
    required_arguments = ["chatRoomId", "messageAuthorId", "messageChannelId"]
    for argument_name, argument_value in input_arguments.items():
        if argument_name not in required_arguments:
            raise Exception("The '{0}' argument doesn't exist.".format(argument_name))
        if argument_value is None:
            raise Exception("The '{0}' argument can't be None/Null/Undefined.".format(argument_name))
        if argument_name.endswith("Id"):
            try:
                uuid.UUID(argument_value)
            except ValueError:
                raise Exception("The '{0}' argument format is not UUID.".format(argument_name))

    # Put the result of the function in the queue.
    queue.put({
        "input_arguments": {
            "chat_room_id": input_arguments.get("chatRoomId", None),
            "message_author_id": input_arguments.get("messageAuthorId", None),
            "message_channel_id": input_arguments.get("messageChannelId", None)
        }
    })

    # Return nothing.
    return None


def reuse_or_recreate_postgresql_connection(queue: Queue) -> None:
    global POSTGRESQL_CONNECTION
    if not POSTGRESQL_CONNECTION:
        try:
            POSTGRESQL_CONNECTION = databases.create_postgresql_connection(
                POSTGRESQL_USERNAME,
                POSTGRESQL_PASSWORD,
                POSTGRESQL_HOST,
                POSTGRESQL_PORT,
                POSTGRESQL_DB_NAME
            )
        except Exception as error:
            logger.error(error)
            raise Exception("Unable to connect to the PostgreSQL database.")
    queue.put({"postgresql_connection": POSTGRESQL_CONNECTION})
    return None


def postgresql_wrapper(function):
    @wraps(function)
    def wrapper(**kwargs):
        try:
            postgresql_connection = kwargs["postgresql_connection"]
        except KeyError as error:
            logger.error(error)
            raise Exception(error)
        cursor = postgresql_connection.cursor(cursor_factory=RealDictCursor)
        kwargs["cursor"] = cursor
        result = function(**kwargs)
        cursor.close()
        return result

    return wrapper


@postgresql_wrapper
def get_aggregated_data(**kwargs) -> Dict:
    # Check if the input dictionary has all the necessary keys.
    try:
        cursor = kwargs["cursor"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        sql_arguments = kwargs["sql_arguments"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Prepare the SQL query that gives the minimal information about the chat room.
    sql_statement = """
    select
        split_part(instagram_chat_rooms.instagram_chat_id, ':', 2) as instagram_chat_id,
        channels.channel_technical_id as instagram_bot_token
    from
        chat_rooms
    left join instagram_chat_rooms on
        chat_rooms.chat_room_id = instagram_chat_rooms.chat_room_id
    left join channels on
        chat_rooms.channel_id = channels.channel_id
    where
        chat_rooms.chat_room_id = %(chat_room_id)s
    limit 1;
    """

    # Execute the SQL query dynamically, in a convenient and safe way.
    try:
        cursor.execute(sql_statement, sql_arguments)
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Return the aggregated data.
    return cursor.fetchone()


def create_chat_room_message(**kwargs) -> Dict[AnyStr, Any]:
    # Check if the input dictionary has all the necessary keys.
    try:
        input_arguments = kwargs["input_arguments"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    chat_room_id = input_arguments.get("chat_room_id", None)
    message_author_id = input_arguments.get("message_author_id", None)
    message_channel_id = input_arguments.get("message_channel_id", None)
    message_text = input_arguments.get("message_text", None)

    # Define the GraphQL mutation.
    query = """
    mutation CreateChatRoomMessage (
        $chatRoomId: String!,
        $messageAuthorId: String!,
        $messageChannelId: String!,
        $messageText: String
    ) {
        createChatRoomMessage(
            input: {
                isClient: false,
                chatRoomId: $chatRoomId,
                messageAuthorId: $messageAuthorId,
                messageChannelId: $messageChannelId,
                messageText: $messageText
            }
        ) {
            channelId
            channelTypeName
            chatRoomId
            chatRoomStatus
            localMessageId
            messageAuthorId
            messageChannelId
            messageContent
            messageCreatedDateTime
            messageDeletedDateTime
            messageId
            messageIsDelivered
            messageIsRead
            messageIsSent
            messageText
            messageUpdatedDateTime
            quotedMessage {
                messageAuthorId
                messageChannelId
                messageContent
                messageId
                messageText
            }
        }
    }
    """

    # Define the GraphQL variables.
    variables = {
        "chatRoomId": chat_room_id,
        "messageAuthorId": message_author_id,
        "messageChannelId": message_channel_id,
        "messageText": message_text
    }

    # Define the header setting.
    headers = {
        "x-api-key": APPSYNC_CORE_API_KEY,
        "Content-Type": "application/json"
    }

    # Execute POST request.
    try:
        response = requests.post(
            APPSYNC_CORE_API_URL,
            json={
                "query": query,
                "variables": variables
            },
            headers=headers
        )
        response.raise_for_status()
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Return the JSON object of the response.
    return response.json()


def send_template_to_instagram(**kwargs) -> None:
    # Check if the input dictionary has all the necessary keys.
    try:
        instagram_bot_token = kwargs["instagram_bot_token"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        instagram_chat_id = kwargs["instagram_chat_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        message_text = kwargs["message_text"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Create the request URL address.
    request_url = "{0}/me/messages".format(FACEBOOK_API_URL)

    # Define the header setting.
    headers = {
        "Content-Type": "application/json"
    }

    # Create the parameters.
    parameters = {
        "access_token": instagram_bot_token
    }

    # Define the JSON object body of the POST request.
    data = {
        "recipient": {
            "id": instagram_chat_id
        },
        "message": {
            "text": message_text
        },
        "messaging_type": "MESSAGE_TAG",
        "tag": "CONFIRMED_EVENT_UPDATE"
    }

    # Execute POST request.
    try:
        response = requests.post(request_url, headers=headers, params=parameters, data=json.dumps(data))
        response.raise_for_status()
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Return nothing.
    return None


def lambda_handler(event, context):
    """
    :param event: The AWS Lambda function uses this parameter to pass in event data to the handler.
    :param context: The AWS Lambda function uses this parameter to provide runtime information to your handler.
    """
    # Parse the JSON object.
    try:
        body = json.loads(event["body"])
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Run several initialization functions in parallel.
    results_of_tasks = run_multithreading_tasks([
        {
            "function_object": check_input_arguments,
            "function_arguments": {
                "body": body
            }
        },
        {
            "function_object": reuse_or_recreate_postgresql_connection,
            "function_arguments": {}
        }
    ])

    # Define the instances of the database connections.
    postgresql_connection = results_of_tasks["postgresql_connection"]

    # Define the input arguments of the AWS Lambda function.
    input_arguments = results_of_tasks["input_arguments"]
    chat_room_id = input_arguments.get("chat_room_id", None)

    # Get the aggregated data.
    aggregated_data = get_aggregated_data(
        postgresql_connection=postgresql_connection,
        sql_arguments={
            "chat_room_id": chat_room_id
        }
    )

    # Define a few necessary variables that will be used in the future.
    try:
        instagram_chat_id = aggregated_data["instagram_chat_id"]
    except Exception as error:
        logger.error(error)
        raise Exception(error)
    try:
        instagram_bot_token = aggregated_data["instagram_bot_token"]
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Define the message text.
    message_text = "Здравствуйте! Ваше сообщение было получено нами, пока мы были недоступны. Можем ли мы связаться " \
                   "с вами по поводу вашего вопроса еще раз?\nЕсли вы согласны, пожалуйста, отправьте нам ДА."
    input_arguments["message_text"] = message_text

    # Send the message to the operator and save it in the database.
    chat_room_message = create_chat_room_message(input_arguments=input_arguments)

    # Send the prepared template to the instagram.
    send_template_to_instagram(
        instagram_bot_token=instagram_bot_token,
        instagram_chat_id=instagram_chat_id,
        message_text=message_text
    )

    # Return the status code 200.
    return {
        "statusCode": 200,
        "body": json.dumps(chat_room_message)
    }
