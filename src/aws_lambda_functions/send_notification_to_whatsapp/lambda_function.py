import databases
import logging
import sys
import os
import json
import requests
from psycopg2.extras import RealDictCursor


"""
Define the connection to the database outside of the "lambda_handler" function.
The connection to the database will be created the first time the function is called.
Any subsequent function call will use the same database connections.
"""
postgresql_connection = None

# Define global variables.
POSTGRESQL_USERNAME = os.environ["POSTGRESQL_USERNAME"]
POSTGRESQL_PASSWORD = os.environ["POSTGRESQL_PASSWORD"]
POSTGRESQL_HOST = os.environ["POSTGRESQL_HOST"]
POSTGRESQL_PORT = int(os.environ["POSTGRESQL_PORT"])
POSTGRESQL_DB_NAME = os.environ["POSTGRESQL_DB_NAME"]
WHATSAPP_API_URL = os.environ["WHATSAPP_API_URL"]

logger = logging.getLogger(__name__)  # Create the logger with the specified name.
logger.setLevel(logging.WARNING)  # Set the logging level of the logger.


def lambda_handler(event, context):
    """
    :argument event: The AWS Lambda uses this parameter to pass in event data to the handler.
    :argument context: The AWS Lambda uses this parameter to provide runtime information to your handler.
    """
    # Since the connection with the database was defined outside of the function, we create the global variable.
    global postgresql_connection
    if not postgresql_connection:
        try:
            postgresql_connection = databases.create_postgresql_connection(
                POSTGRESQL_USERNAME,
                POSTGRESQL_PASSWORD,
                POSTGRESQL_HOST,
                POSTGRESQL_PORT,
                POSTGRESQL_DB_NAME
            )
        except Exception as error:
            logger.error(error)
            sys.exit(1)

    # Parse the JSON object.
    body = json.loads(event['body'])

    # Define the values of the data passed to the function.
    chat_room_id = body["arguments"]["input"]["chatRoomId"]
    notification_description = body["arguments"]["input"]["notificationDescription"]

    # With a dictionary cursor, the data is sent in a form of Python dictionaries.
    cursor = postgresql_connection.cursor(cursor_factory=RealDictCursor)

    # Prepare the SQL request that gives the minimal information about the specific chat room.
    statement = """
    select
        whatsapp_chat_rooms.whatsapp_chat_id,
        channels.channel_technical_id as whatsapp_bot_token
    from
        chat_rooms
    left join whatsapp_chat_rooms on
        chat_rooms.chat_room_id = whatsapp_chat_rooms.chat_room_id
    left join channels on
        chat_rooms.channel_id = channels.channel_id
    where
        chat_rooms.chat_room_id = '{0}'
    limit 1;
    """.format(chat_room_id)

    # Execute a previously prepared SQL query.
    try:
        cursor.execute(statement)
    except Exception as error:
        logger.error(error)
        sys.exit(1)

    # After the successful execution of the query commit your changes to the database.
    postgresql_connection.commit()

    # Fetch the next row of a query result set.
    aggregated_data = cursor.fetchone()
    whatsapp_chat_id = aggregated_data["whatsapp_chat_id"]
    whatsapp_bot_token = aggregated_data["whatsapp_bot_token"]

    # Send a message to the Whatsapp business account.
    request_url = "{0}/v1/messages".format(WHATSAPP_API_URL)
    payload = {
        "to": whatsapp_chat_id,
        "type": "text",
        "text": {
            "body": "🤖💬\n{0}".format(notification_description)
        }
    }
    headers = {
        'Content-Type': 'application/json',
        'D360-Api-Key': whatsapp_bot_token
    }
    try:
        response = requests.post(request_url, json=payload, headers=headers)
        response.raise_for_status()
    except Exception as error:
        logger.error(error)
        sys.exit(1)

    # Return nothing.
    return {
        "statusCode": 200
    }
