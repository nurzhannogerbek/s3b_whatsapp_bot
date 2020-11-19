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
Any subsequent function call will use the same database connection.
"""
postgresql_connection = None

# Define global variables.
POSTGRESQL_USERNAME = os.environ["POSTGRESQL_USERNAME"]
POSTGRESQL_PASSWORD = os.environ["POSTGRESQL_PASSWORD"]
POSTGRESQL_HOST = os.environ["POSTGRESQL_HOST"]
POSTGRESQL_PORT = int(os.environ["POSTGRESQL_PORT"])
POSTGRESQL_DB_NAME = os.environ["POSTGRESQL_DB_NAME"]
WHATSAPP_BOT_TOKEN = os.environ["WHATSAPP_BOT_TOKEN"]
WHATSAPP_API_URL = os.environ["WHATSAPP_API_URL"]
APPSYNC_CORE_API_URL = os.environ["APPSYNC_CORE_API_URL"]
APPSYNC_CORE_API_KEY = os.environ["APPSYNC_CORE_API_KEY"]

logger = logging.getLogger(__name__)  # Create the logger with the specified name.
logger.setLevel(logging.WARNING)  # Set the logging level of the logger.


def lambda_handler(event, context):
    """
    :argument event: The AWS Lambda uses this parameter to pass in event data to the handler.
    :argument context: The AWS Lambda uses this parameter to provide runtime information to your handler.
    """
    # Parse the JSON object.
    body = json.loads(event['body'])
    contacts = body["contacts"]
    whatsapp_profile = contacts["profile"]["name"]
    whatsapp_username = contacts["wa_id"]
    messages = contacts["messages"]
    message_type = messages["type"]

    # Check the message type value.
    if message_type == "text":
        message_text = messages["text"]["body"]
        send_message_to_whatsapp(whatsapp_username, message_text)
    else:
        message_text = """🤖💬\nОбработка данного формата сообщения в данный момент невозможна.\nПросим прощения за 
        доставленные временные неудобства!"""
        send_message_to_whatsapp(whatsapp_username, message_text)

    # Return the status code value of the request.
    return {
        "statusCode": 200
    }


def send_message_to_whatsapp(whatsapp_username, message_text):
    """
    Function name:
    send_message_to_whatsapp

    Function description:
    The main task of this function is to send the specific message to the Whatsapp.
    """
    # Send a message to the Whatsapp business account.
    request_url = "{0}v1/messages".format(WHATSAPP_API_URL)
    payload = {
        "to": whatsapp_username,
        "type": "text",
        "text": {
            "body": message_text
        }
    }
    headers = {
        'Content-Type': 'application/json',
        'D360-Api-Key': WHATSAPP_BOT_TOKEN
    }
    try:
        response = requests.post(request_url, json=payload, headers=headers)
        response.raise_for_status()
    except Exception as error:
        logger.error(error)
        sys.exit(1)

    # Return nothing.
    return None
