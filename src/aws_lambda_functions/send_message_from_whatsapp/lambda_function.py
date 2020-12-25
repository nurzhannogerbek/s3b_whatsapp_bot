import logging
import os
from psycopg2 import connect
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


def reuse_or_recreate_postgresql_connection() -> connect:
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
    return POSTGRESQL_CONNECTION


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

    # Prepare the SQL query that returns the whatsapp's chat bot token.
    sql_statement = """
    select
        channels.channel_technical_id as whatsapp_bot_token
    from
        whatsapp_business_accounts
    left join channels on
        whatsapp_business_accounts.channel_id = channels.channel_id
    where
        whatsapp_business_accounts.business_account = %(business_account)s
    limit 1;
    """

    # Execute the SQL query dynamically, in a convenient and safe way.
    try:
        cursor.execute(sql_statement, sql_arguments)
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Return whatsapp's chat bot token.
    return cursor.fetchone()["whatsapp_bot_token"]


def send_message_to_whatsapp(**kwargs) -> None:
    # Check if the input dictionary has all the necessary keys.
    try:
        whatsapp_bot_token = kwargs["whatsapp_bot_token"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        whatsapp_chat_id = kwargs["whatsapp_chat_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        message_text = kwargs["message_text"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Create the request URL address.
    request_url = "{0}/v1/messages".format(WHATSAPP_API_URL)

    # Create the parameters.
    parameters = {
        "to": whatsapp_chat_id,
        "type": "text",
        "text": {
            "body": message_text
        }
    }

    # Define the header setting.
    headers = {
        'Content-Type': 'application/json',
        'D360-Api-Key': whatsapp_bot_token
    }

    # Execute POST request.
    try:
        response = requests.post(request_url, json=parameters, headers=headers)
        response.raise_for_status()
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Return nothing.
    return None


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

    # Prepare the SQL query that returns the aggregated data.
    sql_statement = """
    select
        chat_rooms.chat_room_id,
        chat_rooms.channel_id,
        chat_rooms.chat_room_status,
        users.user_id as client_id
    from
        whatsapp_chat_rooms
    left join chat_rooms on
        whatsapp_chat_rooms.chat_room_id = chat_rooms.chat_room_id
    left join chat_rooms_users_relationship on
        chat_rooms.chat_room_id = chat_rooms_users_relationship.chat_room_id
    left join users on
        chat_rooms_users_relationship.user_id = users.user_id
    where
        whatsapp_chat_rooms.whatsapp_chat_id = %(whatsapp_chat_id)s
    and
        (
            users.internal_user_id is null and users.identified_user_id is not null
            or
            users.internal_user_id is null and users.unidentified_user_id is not null
        )
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


def create_chat_room(**kwargs) -> None:
    # Check if the input dictionary has all the necessary keys.
    try:
        channel_technical_id = kwargs["channel_technical_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        client_id = kwargs["client_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        last_message_content = kwargs["last_message_content"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        whatsapp_chat_id = kwargs["whatsapp_chat_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        queue = kwargs["queue"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Define the GraphQL mutation.
    query = """
    mutation CreateChatRoom (
        $channelTechnicalId: String!,
        $clientId: String!,
        $lastMessageContent: String!,
        $whatsappChatId: String
    ) {
        createChatRoom(
            input: {
                channelTechnicalId: $channelTechnicalId,
                channelTypeName: "whatsapp",
                clientId: $clientId,
                lastMessageContent: $lastMessageContent,
                whatsappChatId: $whatsappChatId
            }
        ) {
            channel {
                channelDescription
                channelId
                channelName
                channelTechnicalId
                channelType {
                    channelTypeDescription
                    channelTypeId
                    channelTypeName
                }
            }
            channelId
            chatRoomId
            chatRoomStatus
            client {
                country {
                    countryAlpha2Code
                    countryAlpha3Code
                    countryCodeTopLevelDomain
                    countryId
                    countryNumericCode
                    countryOfficialName
                    countryShortName
                }
                gender {
                    genderId
                    genderPublicName
                    genderTechnicalName
                }
                metadata
                telegramUsername
                userFirstName
                userId
                userLastName
                userMiddleName
                userPrimaryEmail
                userPrimaryPhoneNumber
                userProfilePhotoUrl
                userSecondaryEmail
                userSecondaryPhoneNumber
                userType
                whatsappProfile
                whatsappUsername
            }
            lastMessageContent
            lastMessageDateTime
            organizationsIds
            unreadMessagesNumber
        }
    }
    """

    # Define the GraphQL variables.
    variables = {
        "channelTechnicalId": channel_technical_id,
        "clientId": client_id,
        "lastMessageContent": last_message_content,
        "whatsappChatId": whatsapp_chat_id
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

    # Put the result of the function in the queue.
    queue.put({"chat_room": response.json()})

    # Return nothing.
    return None


@postgresql_wrapper
def create_identified_user(**kwargs) -> None:
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
    try:
        queue = kwargs["queue"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Prepare the SQL query that creates identified user.
    sql_statement = """
    insert into identified_users(
        identified_user_primary_phone_number,
        metadata,
        whatsapp_profile,
        whatsapp_username
    ) values(
        %(identified_user_primary_phone_number)s,
        %(metadata)s,
        %(whatsapp_profile)s,
        %(whatsapp_username)s
    )
    on conflict on constraint identified_users_whatsapp_username_key 
    do nothing
    returning
        identified_user_id;
    """

    # Execute the SQL query dynamically, in a convenient and safe way.
    try:
        cursor.execute(sql_statement, sql_arguments)
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Define the id of the created identified user.
    sql_arguments["identified_user_id"] = cursor.fetchone()["identified_user_id"]

    # Prepare the SQL query that creates the user.
    sql_statement = "insert into users(identified_user_id) values(%(identified_user_id)s) returning user_id;"

    # Execute the SQL query dynamically, in a convenient and safe way.
    try:
        cursor.execute(sql_statement, sql_arguments)
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Put the result of the function in the queue.
    queue.put({"user_id": cursor.fetchone()["user_id"]})

    # Return nothing.
    return None


def activate_closed_chat_room(**kwargs):
    # Check if the input dictionary has all the necessary keys.
    try:
        chat_room_id = kwargs["chat_room_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        client_id = kwargs["client_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        last_message_content = kwargs["last_message_content"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Define the GraphQL mutation.
    query = """
    mutation ActivateClosedChatRoom (
        $chatRoomId: String!,
        $clientId: String!,
        $lastMessageContent: String!
    ) {
        activateClosedChatRoom(
            input: {
                chatRoomId: $chatRoomId,
                clientId: $clientId,
                lastMessageContent: $lastMessageContent
            }
        ) {
            channel {
                channelDescription
                channelId
                channelName
                channelTechnicalId
                channelType {
                    channelTypeDescription
                    channelTypeId
                    channelTypeName
                }
            }
            channelId
            chatRoomId
            chatRoomStatus
            client {
                country {
                    countryAlpha2Code
                    countryAlpha3Code
                    countryCodeTopLevelDomain
                    countryId
                    countryNumericCode
                    countryOfficialName
                    countryShortName
                }
                gender {
                    genderId
                    genderPublicName
                    genderTechnicalName
                }
                metadata
                telegramUsername
                userFirstName
                userId
                userLastName
                userMiddleName
                userPrimaryEmail
                userPrimaryPhoneNumber
                userProfilePhotoUrl
                userSecondaryEmail
                userSecondaryPhoneNumber
                userType
                whatsappProfile
                whatsappUsername
            }
            lastMessageContent
            lastMessageDateTime
            organizationsIds
            unreadMessagesNumber
        }
    }
    """

    # Define the GraphQL variables.
    variables = {
        "chatRoomId": chat_room_id,
        "clientId": client_id,
        "lastMessageContent": last_message_content
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

    # Return nothing.
    return None


def create_chat_room_message(**kwargs):
    # Check if the input dictionary has all the necessary keys.
    try:
        chat_room_id = kwargs["chat_room_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        message_author_id = kwargs["message_author_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        message_channel_id = kwargs["message_channel_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        message_type = kwargs["message_type"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        message_text = kwargs["message_text"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Define the GraphQL mutation.
    query = """
    mutation CreateChatRoomMessage (
        $chatRoomId: String!,
        $messageAuthorId: String!,
        $messageChannelId: String!,
        $messageType: String!,
        $messageText: String
    ) {
        createChatRoomMessage(
            input: {
                chatRoomId: $chatRoomId,
                localMessageId: null,
                messageAuthorId: $messageAuthorId,
                messageChannelId: $messageChannelId,
                messageContentUrl: null,
                messageText: $messageText,
                messageType: $messageType,
                quotedMessage: {
                    messageAuthorId: null,
                    messageChannelId: null,
                    messageContentUrl: null,
                    messageId: null,
                    messageText: null,
                    messageType: null
                }
            }
        ) {
            channelId
            chatRoomId
            chatRoomStatus
            localMessageId
            messageAuthorId
            messageChannelId
            messageContentUrl
            messageCreatedDateTime
            messageDeletedDateTime
            messageId
            messageIsDelivered
            messageIsRead
            messageIsSent
            messageText
            messageType
            messageUpdatedDateTime
            quotedMessage {
                messageAuthorId
                messageChannelId
                messageContentUrl
                messageId
                messageText
                messageType
            }
        }
    }
    """

    # Define the GraphQL variables.
    variables = {
        "chatRoomId": chat_room_id,
        "messageAuthorId": message_author_id,
        "messageChannelId": message_channel_id,
        "messageType": message_type,
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

    # Return JSON object of the response.
    return response.json()


def update_message_data(**kwargs):
    # Check if the input dictionary has all the necessary keys.
    try:
        chat_room_id = kwargs["chat_room_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        messages_ids = kwargs["messages_ids"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Define the GraphQL mutation.
    query = """
    mutation UpdateMessageData (
        $chatRoomId: String!,
        $messagesIds: [String!]!
    ) {
        updateMessageData(
            input: {
                chatRoomId: $chatRoomId,
                isClient: true,
                messageStatus: MESSAGE_IS_SENT,
                messagesIds: $messagesIds
            }
        ) {
            chatRoomId
            channelId
            chatRoomMessages {
                messageAuthorId
                messageChannelId
                messageContentUrl
                messageCreatedDateTime
                messageDeletedDateTime
                messageId
                messageIsDelivered
                messageIsRead
                messageIsSent
                messageText
                messageType
                messageUpdatedDateTime
                quotedMessage {
                    messageAuthorId
                    messageChannelId
                    messageContentUrl
                    messageId
                    messageText
                    messageType
                }
            }
            chatRoomStatus
            unreadMessagesNumber
        }
    }
    """

    # Define the GraphQL variables.
    variables = {
        "chatRoomId": chat_room_id,
        "messagesIds": messages_ids
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
    keys = body.keys()

    # Check the availability of certain keys.
    if all(key in ["messages", "contacts"] for key in keys):
        # Define a few necessary variables that will be used in the future.
        try:
            metadata = body["contacts"][0]
        except Exception as error:
            logger.error(error)
            raise Exception(error)
        try:
            whatsapp_profile = metadata["profile"]["name"]
        except Exception as error:
            logger.error(error)
            raise Exception(error)
        try:
            whatsapp_username = metadata["wa_id"]
        except Exception as error:
            logger.error(error)
            raise Exception(error)
        whatsapp_chat_id = whatsapp_username
        try:
            message_information = body["messages"][0]
        except Exception as error:
            logger.error(error)
            raise Exception(error)
        try:
            message_type = message_information["type"]
        except Exception as error:
            logger.error(error)
            raise Exception(error)

        # Define the business account from which clients write.
        try:
            business_account = event['rawPath'].rsplit('/', 1)[1]
        except Exception as error:
            logger.error(error)
            raise Exception(error)

        # Define the instances of the database connections.
        postgresql_connection = reuse_or_recreate_postgresql_connection()

        # Get whatsapp bot token from the database.
        whatsapp_bot_token = get_whatsapp_bot_token(
            postgresql_connection=postgresql_connection,
            sql_arguments={
                "business_account": business_account
            }
        )

        # Check the message type.
        if message_type == "text":
            # Get the aggregated data.
            aggregated_data = get_aggregated_data(
                postgresql_connection=postgresql_connection,
                sql_arguments={
                    "whatsapp_chat_id": whatsapp_chat_id
                }
            )

            # Define a few necessary variables that will be used in the future.
            chat_room_id = aggregated_data.get("chat_room_id", None)
            channel_id = aggregated_data.get("channel_id", None)
            chat_room_status = aggregated_data.get("chat_room_status", None)
            client_id = aggregated_data.get("client_id", None)

            # Define the message text.
            try:
                message_text = message_information["text"]["body"]
            except Exception as error:
                logger.error(error)
                raise Exception(error)

            # Check the chat room status.
            if not chat_room_status:
                # Run several functions in parallel.
                results_of_tasks = run_multithreading_tasks([
                    {
                        "function_object": create_identified_user,
                        "function_arguments": {
                            "postgresql_connection": postgresql_connection,
                            "sql_arguments": {
                                "identified_user_primary_phone_number": "+{0}".format(whatsapp_username),
                                "metadata": json.dumps(metadata),
                                "whatsapp_profile": whatsapp_profile,
                                "whatsapp_username": whatsapp_username
                            }
                        }
                    },
                    {
                        "function_object": create_chat_room,
                        "function_arguments": {
                            "channel_technical_id": whatsapp_bot_token,
                            "client_id": client_id,
                            "last_message_content": message_text,
                            "whatsapp_chat_id": whatsapp_chat_id
                        }
                    }
                ])

                # Define a few necessary variables that will be used in the future.
                try:
                    chat_room = results_of_tasks["chat_room"]
                except Exception as error:
                    logger.error(error)
                    raise Exception(error)
                try:
                    client_id = results_of_tasks["client_id"]
                except Exception as error:
                    logger.error(error)
                    raise Exception(error)
                try:
                    chat_room_id = chat_room["data"]["createChatRoom"]["chatRoomId"]
                except Exception as error:
                    logger.error(error)
                    raise Exception(error)
                try:
                    channel_id = chat_room["data"]["createChatRoom"]["channelId"]
                except Exception as error:
                    logger.error(error)
                    raise Exception(error)
            elif chat_room_status == "completed":
                # Activate closed chat room before sending a message to the operator.
                activate_closed_chat_room(
                    chat_room_id=chat_room_id,
                    client_id=client_id,
                    last_message_content=message_text
                )

            # Send the message to the operator and save it in the database.
            chat_room_message = create_chat_room_message(
                chat_room_id=chat_room_id,
                message_author_id=client_id,
                message_channel_id=channel_id,
                message_type="text",
                message_text=message_text
            )

            # Define the id of the created message.
            try:
                message_id = chat_room_message["data"]["createChatRoomMessage"]["messageId"]
            except Exception as error:
                logger.error(error)
                raise Exception(error)

            # Update the data (unread message number / message status) of the created message.
            update_message_data(
                chat_room_id=chat_room_id,
                messages_ids=[message_id]
            )
        else:
            # Define the message text.
            message_text = "🤖💬\nОбработка данного формата в данный момент невозможна."

            # Send the prepared text to the whatsapp client.
            send_message_to_whatsapp(
                whatsapp_bot_token=whatsapp_bot_token,
                message_text=message_text,
                whatsapp_chat_id=whatsapp_chat_id
            )

    # Return the status code 200.
    return {
        "statusCode": 200
    }
