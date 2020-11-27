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
    keys = body.keys()

    # Check for specific keys.
    if "messages" in keys and "contacts" in keys:
        # Define the general information.
        metadata = body["contacts"][0]
        whatsapp_profile = metadata["profile"]["name"]
        whatsapp_username = metadata["wa_id"]
        whatsapp_chat_id = whatsapp_username
        message_information = body["messages"][0]
        message_type = message_information["type"]

        # Determine the business account from which clients write.
        business_account = event['rawPath'].rsplit('/', 1)[1]

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

        # Get whatsapp bot token from the database.
        whatsapp_bot_token = get_whatsapp_bot_token(postgresql_connection, business_account)

        # Check the message type value.
        if message_type == "text":
            # Define the client's message text.
            message_text = message_information["text"]["body"]

            # Get aggregated data from the database associated with the specific chat room.
            aggregated_data = get_chat_room_information(postgresql_connection, whatsapp_chat_id)

            # Define several variables that will be used in the future.
            if aggregated_data is not None:
                chat_room_id = aggregated_data["chat_room_id"]
                channel_id = aggregated_data["channel_id"]
                chat_room_status = aggregated_data["chat_room_status"]
                client_id = aggregated_data["client_id"]
            else:
                chat_room_id, channel_id, chat_room_status, client_id = None, None, None, None

            # Check the status of the chat room.
            if chat_room_status is None:
                # Create new identified user in the PostgreSQL database.
                client_id = create_identified_user(postgresql_connection, metadata, whatsapp_profile, whatsapp_username)

                # Call a mutation called "createChatRoom" from AppSync.
                chat_room_entry = create_chat_room(whatsapp_bot_token, "whatsapp", client_id, whatsapp_chat_id)

                # Define several variables that will be used in the future.
                chat_room_id = chat_room_entry["data"]["createChatRoom"]["chatRoomId"]
                channel_id = chat_room_entry["data"]["createChatRoom"]["channelId"]
            elif chat_room_status == "completed":
                activate_closed_chat_room(chat_room_id, client_id)

            # Add a new message from the client to the database.
            create_chat_room_message(chat_room_id, client_id, channel_id, message_type, message_text)
        else:
            message_text = "🤖💬\nОбработка данного формата сообщения в данный момент невозможна.\nПросим прощения за " \
                            "доставленные временные неудобства!"
            send_message_to_whatsapp(whatsapp_bot_token, whatsapp_username, message_text)

    # Return the status code value of the request.
    return {
        "statusCode": 200
    }


def get_whatsapp_bot_token(postgresql_db_connection, business_account):
    """
    Function name:
    get_whatsapp_bot_token

    Function description:
    The main task of this function is to get the value of the chat bot token.
    """
    # With a dictionary cursor, the data is sent in a form of Python dictionaries.
    cursor = postgresql_db_connection.cursor(cursor_factory=RealDictCursor)

    # Prepare the SQL request that creates the new identified user.
    statement = """
    select
        channels.channel_technical_id as whatsapp_bot_token
    from
        whatsapp_business_accounts
    left join channels on
        whatsapp_business_accounts.channel_id = channels.channel_id
    where
        whatsapp_business_accounts.business_account = '{0}'
    limit 1;
    """.format(business_account)

    # Execute a previously prepared SQL query.
    try:
        cursor.execute(statement)
    except Exception as error:
        logger.error(error)
        sys.exit(1)

    # After the successful execution of the query commit your changes to the database.
    postgresql_db_connection.commit()

    # Define the id of the created identified user.
    whatsapp_bot_token = cursor.fetchone()["whatsapp_bot_token"]

    # Return chat bot token.
    return whatsapp_bot_token


def send_message_to_whatsapp(whatsapp_bot_token, whatsapp_username, message_text):
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
        'D360-Api-Key': whatsapp_bot_token
    }
    try:
        response = requests.post(request_url, json=payload, headers=headers)
        response.raise_for_status()
    except Exception as error:
        logger.error(error)
        sys.exit(1)

    # Return nothing.
    return None


def create_chat_room(channel_technical_id, channel_type_name, client_id, whatsapp_chat_id):
    """
    Function name:
    create_chat_room

    Function description:
    The main task of this function is to create a chat room.
    """
    # Define the GraphQL mutation query to the AppSync.
    query = """
    mutation CreateChatRoom (
        $channelTechnicalId: String!,
        $channelTypeName: String!,
        $clientId: String!,
        $whatsappChatId: String
    ) {
        createChatRoom(
            input: {
                channelTechnicalId: $channelTechnicalId,
                channelTypeName: $channelTypeName,
                clientId: $clientId,
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
                userType
                userSecondaryPhoneNumber
                userSecondaryEmail
                userProfilePhotoUrl
                userPrimaryPhoneNumber
                userPrimaryEmail
                userMiddleName
                userLastName
                userId
                userFirstName
                metadata
                gender {
                    genderId
                    genderPublicName
                    genderTechnicalName
                }
                country {
                    countryAlpha2Code
                    countryAlpha3Code
                    countryCodeTopLevelDomain
                    countryNumericCode
                    countryId
                    countryOfficialName
                    countryShortName
                }
            }
            organizationsIds
        }
    }
    """
    variables = {
        "channelTechnicalId": channel_technical_id,
        "channelTypeName": channel_type_name,
        "clientId": client_id,
        "whatsappChatId": whatsapp_chat_id
    }

    # Define the header setting.
    headers = {
        "x-api-key": APPSYNC_CORE_API_KEY,
        "Content-Type": "application/json"
    }

    try:
        # Make the POST request to the AppSync.
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
        sys.exit(1)

    # Return the information about the specific chat room.
    return response.json()


def create_chat_room_message(chat_room_id, message_author_id, message_channel_id, message_type, message_text):
    """
    Function name:
    create_chat_room_message

    Function description:
    The main task of this function is to create the message in the specific chat room.
    """
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
                messageAuthorId: $messageAuthorId,
                messageChannelId: $messageChannelId,
                messageType: $messageType,
                messageText: $messageText,
                messageContentUrl: null,
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

    try:
        # Make the POST request to the AppSync.
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
        sys.exit(1)

    # Return nothing.
    return None


def activate_closed_chat_room(chat_room_id, client_id):
    """
    Function name:
    activate_closed_chat_room

    Function description:
    The main task of this function is to activate a closed chat room when the client writes to it.
    """
    query = """
    mutation ActivateClosedChatRoom (
        $chatRoomId: String!,
        $clientId: String!
    ) {
        activateClosedChatRoom(
            input: {
                chatRoomId: $chatRoomId,
                clientId: $clientId
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
            organizationsIds
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
                metadata
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
                gender {
                    genderId
                    genderPublicName
                    genderTechnicalName
                }
            }
        }
    }
    """
    variables = {
        "chatRoomId": chat_room_id,
        "clientId": client_id
    }

    # Define the header setting.
    headers = {
        "x-api-key": APPSYNC_CORE_API_KEY,
        "Content-Type": "application/json"
    }

    try:
        # Make the POST request to the AppSync.
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
        sys.exit(1)

    # Return nothing.
    return None


def get_chat_room_information(postgresql_db_connection, whatsapp_chat_id):
    """
    Function name:
    get_chat_room_information

    Function description:
    The main task of this function is to give aggregated data about the specific chat room.
    """
    # With a dictionary cursor, the data is sent in a form of Python dictionaries.
    cursor = postgresql_db_connection.cursor(cursor_factory=RealDictCursor)

    # Check if the database has chat room information for the specific whatsapp conversation.
    statement = """
    select
        chat_rooms.chat_room_id,
        chat_rooms.channel_id,
        chat_rooms.chat_room_status,
        (
            select
                users.user_id
            from
                chat_rooms_users_relationship
            left join users on
                chat_rooms_users_relationship.user_id = users.user_id
            where
                chat_rooms_users_relationship.chat_room_id = chat_rooms.chat_room_id
            and
                (
                    users.internal_user_id is null and users.identified_user_id is not null
                    or
                    users.internal_user_id is null and users.unidentified_user_id is not null
                )
            limit 1
        ) as client_id
    from
        chat_rooms
    left join whatsapp_chat_rooms on
        chat_rooms.chat_room_id = whatsapp_chat_rooms.chat_room_id
    where
        whatsapp_chat_rooms.whatsapp_chat_id = '{0}'
    limit 1;
    """.format(whatsapp_chat_id)

    # Execute a previously prepared SQL query.
    try:
        cursor.execute(statement)
    except Exception as error:
        logger.error(error)
        sys.exit(1)

    # After the successful execution of the query commit your changes to the database.
    postgresql_db_connection.commit()

    # Determine aggregated data about the specific chat room from the database.
    aggregated_data = cursor.fetchone()

    # The cursor will be unusable from this point forward.
    cursor.close()

    # Return the information about the specific chat room.
    return aggregated_data


def create_identified_user(postgresql_db_connection, metadata, whatsapp_profile, whatsapp_username):
    """
    Function name:
    create_identified_user

    Function description:
    The main task of this function is to create a identified user in the database.
    """
    # With a dictionary cursor, the data is sent in a form of Python dictionaries.
    cursor = postgresql_db_connection.cursor(cursor_factory=RealDictCursor)

    # Prepare the SQL request that creates the new identified user.
    statement = """
    insert into identified_users(
        identified_user_primary_phone_number,
        metadata,
        whatsapp_profile,
        whatsapp_username
    ) values(
        '{0}',
        '{1}',
        '{2}',
        '{3}'
    )
    on conflict on constraint identified_users_whatsapp_username_key 
    do nothing
    returning
        identified_user_id;
    """.format(
        "+{0}".format(whatsapp_username),
        json.dumps(metadata),
        whatsapp_profile,
        whatsapp_username
    )

    # Execute a previously prepared SQL query.
    try:
        cursor.execute(statement)
    except Exception as error:
        logger.error(error)
        sys.exit(1)

    # After the successful execution of the query commit your changes to the database.
    postgresql_db_connection.commit()

    # Define the id of the created identified user.
    identified_user_id = cursor.fetchone()["identified_user_id"]

    # Prepare the SQL request that creates the new user.
    statement = """
    insert into users(identified_user_id)
    values('{0}')
    returning
        user_id;
    """.format(identified_user_id)

    # Execute a previously prepared SQL query.
    try:
        cursor.execute(statement)
    except Exception as error:
        logger.error(error)
        sys.exit(1)

    # After the successful execution of the query commit your changes to the database.
    postgresql_db_connection.commit()

    # Define the id of the created user.
    user_id = cursor.fetchone()["user_id"]

    # Return the id of the created user.
    return user_id
