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
WHATSAPP_API_URL = os.environ["WHATSAPP_API_URL"]

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

    # Check the format and values of required arguments.
    chat_room_id = input_arguments.get("chatRoomId", None)
    if chat_room_id is not None:
        try:
            uuid.UUID(chat_room_id)
        except ValueError:
            raise Exception("The 'chatRoomId' argument format is not UUID.")
    else:
        raise Exception("The 'chatRoomId' argument can't be None/Null/Undefined.")

    # Put the result of the function in the queue.
    queue.put({
        "input_arguments": {
            "chat_room_id": chat_room_id
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
def get_whatsapp_bot_token(**kwargs) -> AnyStr:
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

    # Prepare the SQL query that returns the whatsapp bot token of the specific chat room.
    sql_statement = """
    select
        channels.channel_technical_id::text as whatsapp_bot_token
    from
        chat_rooms
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

    # Return the whatsapp bot token value.
    return cursor.fetchone()["whatsapp_bot_token"]


def get_templates(**kwargs) -> json:
    # Check if the input dictionary has all the necessary keys.
    try:
        whatsapp_bot_token = kwargs["whatsapp_bot_token"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Create the request URL address.
    request_url = "{0}/v1/configs/templates".format(WHATSAPP_API_URL)

    # Define the header setting.
    headers = {
        "D360-Api-Key": whatsapp_bot_token
    }

    # Execute GET request.
    try:
        response = requests.get(request_url, headers=headers)
        response.raise_for_status()
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Return the JSON object of the response.
    return response.json()


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

    # Get the whatsapp bot token.
    whatsapp_bot_token = get_whatsapp_bot_token(
        postgresql_connection=postgresql_connection,
        sql_arguments={
            "chat_room_id": chat_room_id
        }
    )

    # Get the list of available templates.
    templates = get_templates(whatsapp_bot_token=whatsapp_bot_token)

    # Return the status code 200.
    return {
        "statusCode": 200,
        "body": json.dumps(templates)
    }
