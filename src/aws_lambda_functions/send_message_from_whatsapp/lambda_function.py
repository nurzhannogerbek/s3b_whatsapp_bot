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
from PIL import Image
import io


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
FILE_STORAGE_SERVICE_URL = os.environ["FILE_STORAGE_SERVICE_URL"]

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


def create_chat_room(**kwargs) -> json:
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
                gender {
                    genderId
                    genderPublicName
                    genderTechnicalName
                }
                metadata
                telegramUsername
                userFirstName
                userId
                userNickname
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
            lastMessageFromClientDateTime
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

    # Return the JSON object of the response.
    return response.json()


@postgresql_wrapper
def get_identified_user_data(**kwargs) -> AnyStr:
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

    # Prepare an SQL query that returns the data of the identified user.
    sql_statement = """
    select
        users.user_id::text
    from
        identified_users
    left join users on
        identified_users.identified_user_id = users.identified_user_id
    where
        identified_users.whatsapp_username = %(whatsapp_username)s
    and
        users.internal_user_id is null
    and
        users.unidentified_user_id is null
    limit 1;
    """

    # Execute the SQL query dynamically, in a convenient and safe way.
    try:
        cursor.execute(sql_statement, sql_arguments)
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Return the id of the user.
    result = cursor.fetchone()
    if result is None:
        user_id = None
    else:
        user_id = result["user_id"]
    return user_id


@postgresql_wrapper
def create_identified_user(**kwargs) -> AnyStr:
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
        identified_user_id::text;
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
    sql_statement = "insert into users(identified_user_id) values(%(identified_user_id)s) returning user_id::text;"

    # Execute the SQL query dynamically, in a convenient and safe way.
    try:
        cursor.execute(sql_statement, sql_arguments)
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Return the id of the new created user.
    return cursor.fetchone()["user_id"]


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
                gender {
                    genderId
                    genderPublicName
                    genderTechnicalName
                }
                metadata
                telegramUsername
                userFirstName
                userId
                userNickname
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
            lastMessageFromClientDateTime
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
        message_text = kwargs["message_text"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        message_content = kwargs["message_content"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Define the GraphQL mutation.
    query = """
    mutation CreateChatRoomMessage (
        $chatRoomId: String!,
        $messageAuthorId: String!,
        $messageChannelId: String!,
        $messageText: String,
        $messageContent: String
    ) {
        createChatRoomMessage(
            input: {
                chatRoomId: $chatRoomId,
                localMessageId: null,
                isClient: true,
                messageAuthorId: $messageAuthorId,
                messageChannelId: $messageChannelId,
                messageContent: $messageContent,
                messageText: $messageText,
                quotedMessage: {
                    messageAuthorId: null,
                    messageChannelId: null,
                    messageContent: null,
                    messageId: null,
                    messageText: null
                }
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
        "messageText": message_text,
        "messageContent": message_content
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
            chatRoomStatus
            unreadMessagesNumber,
            channelTypeName,
            isClient
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


def send_message_text_to_whatsapp(**kwargs) -> None:
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
        "Content-Type": "application/json",
        "D360-Api-Key": whatsapp_bot_token
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


def upload_file_to_s3_bucket(**kwargs) -> AnyStr:
    try:
        binary_data = kwargs["binary_data"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        chat_room_id = kwargs["chat_room_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        file_name = kwargs["file_name"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Define a dictionary of files to send to the s3 bucket url address.
    files = {
        "file": binary_data
    }

    # Execute GET request.
    try:
        response = requests.get(
            "{0}/get_presigned_url_to_upload_file".format(FILE_STORAGE_SERVICE_URL),
            params={
                "key": "chat_rooms/{0}/{1}".format(chat_room_id, file_name)
            }
        )
        response.raise_for_status()
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Define a few necessary variables.
    try:
        request_url = response.json()["data"]["url"]
        original_file_url = response.json()["url"]
        key = response.json()["data"]["fields"]["key"]
        x_amz_algorithm = response.json()["data"]["fields"]["x-amz-algorithm"]
        x_amz_credential = response.json()["data"]["fields"]["x-amz-credential"]
        x_amz_date = response.json()["data"]["fields"]["x-amz-date"]
        policy = response.json()["data"]["fields"]["policy"]
        x_amz_signature = response.json()["data"]["fields"]["x-amz-signature"]
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Define the JSON object body of the POST request.
    data = {
        "key": key,
        "x-amz-algorithm": x_amz_algorithm,
        "x-amz-credential": x_amz_credential,
        "x-amz-date": x_amz_date,
        "policy": policy,
        "x-amz-signature": x_amz_signature
    }

    # Execute POST request.
    try:
        response = requests.post(request_url, data=data, files=files)
        response.raise_for_status()
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Return the original url address of the file.
    return original_file_url


def get_the_binary_data(**kwargs):
    # Check if the input dictionary has all the necessary keys.
    try:
        whatsapp_bot_token = kwargs["whatsapp_bot_token"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        file_id = kwargs["file_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Execute GET request.
    try:
        response = requests.get(
            "{0}/v1/media/{1}".format(WHATSAPP_API_URL, file_id),
            headers={
                "D360-API-Key": whatsapp_bot_token
            }
        )
        response.raise_for_status()
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Return the binary data.
    return response.content, response.headers["Content-Type"]


def form_message_format(**kwargs):
    # Check if the input dictionary has all the necessary keys.
    try:
        message = kwargs["message"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        chat_room_id = kwargs["chat_room_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        whatsapp_bot_token = kwargs["whatsapp_bot_token"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Define a few necessary variables.
    text = message.get("text", None)
    location = message.get("location", None)
    image = message.get("image", None)
    video = message.get("video", None)
    document = message.get("document", None)
    voice = message.get("voice", None)

    # Define the value of the message text.
    if text is not None:
        message_text = text.get("body", None)
    elif image is not None:
        message_text = image.get("caption", None)
    elif video is not None:
        message_text = video.get("caption", None)
    else:
        message_text = None

    # Define the value of the message content.
    if location is not None:
        message_content = [
            {
                "category": "location",
                "details": {
                    "latitude": location.get("latitude", None),
                    "longitude": location.get("longitude", None)
                }
            }
        ]
    elif image is not None:
        binary_data, file_size = get_the_binary_data(
            whatsapp_bot_token=whatsapp_bot_token,
            file_id=image["id"],
        )

        image_file = Image.open(io.BytesIO(binary_data))

        image_width, image_height = image_file.size

        message_content = [
            {
                "category": "image",
                "fileName": "{0}.jpeg".format(image["id"]),
                "fileExtension": ".jpeg",
                "fileSize": file_size,
                "mimeType": image["mime_type"],
                "url": upload_file_to_s3_bucket(
                    binary_data=binary_data,
                    chat_room_id=chat_room_id,
                    file_name="{0}.jpeg".format(image["id"])
                ),
                "dimensions": {
                    "width": image_width,
                    "height": image_height
                }
            }
        ]
    elif video is not None:
        binary_data, file_size = get_the_binary_data(
            whatsapp_bot_token=whatsapp_bot_token,
            file_id=video["id"],
        )

        message_content = [
            {
                "category": "document",
                "fileName": "{0}.mp4".format(video["id"]),
                "fileExtension": ".mp4",
                "fileSize": file_size,
                "mimeType": video["mime_type"],
                "url": upload_file_to_s3_bucket(
                    binary_data=binary_data,
                    chat_room_id=chat_room_id,
                    file_name="{0}.mp4".format(video["id"])
                )
            }
        ]
    elif document is not None:
        binary_data, file_size = get_the_binary_data(
            whatsapp_bot_token=whatsapp_bot_token,
            file_id=document["id"],
        )

        message_content = [
            {
                "category": "document",
                "fileName": document["filename"],
                "fileExtension": ".{0}".format(document["filename"].rsplit('.', 1)[1]).lower(),
                "fileSize": file_size,
                "mimeType": document["mime_type"],
                "url": upload_file_to_s3_bucket(
                    binary_data=binary_data,
                    chat_room_id=chat_room_id,
                    file_name=document["filename"]
                )
            }
        ]
    elif voice is not None:
        binary_data, file_size = get_the_binary_data(
            whatsapp_bot_token=whatsapp_bot_token,
            file_id=voice["id"],
        )

        message_content = [
            {
                "category": "audio",
                "fileName": "{0}.ogg".format(voice["id"]),
                "fileExtension": ".ogg",
                "fileSize": file_size,
                "mimeType": voice["mime_type"],
                "url": upload_file_to_s3_bucket(
                    binary_data=binary_data,
                    chat_room_id=chat_room_id,
                    file_name="{0}.ogg".format(voice["id"])
                )
            }
        ]
    else:
        message_content = None

    # Return the content of the message.
    return message_text, message_content


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

    # Check the availability of certain keys.
    if all(key in ["messages", "contacts"] for key in body.keys()):
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
            whatsapp_username = whatsapp_chat_id = metadata["wa_id"]
        except Exception as error:
            logger.error(error)
            raise Exception(error)
        try:
            message = body["messages"][0]
        except Exception as error:
            logger.error(error)
            raise Exception(error)
        try:
            message_type = message["type"]
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

        # Get the aggregated data.
        aggregated_data = get_aggregated_data(
            postgresql_connection=postgresql_connection,
            sql_arguments={
                "whatsapp_chat_id": "{0}:{1}".format(business_account, whatsapp_chat_id)
            }
        )

        # Determine whether this is a new chat room or not.
        chat_room_id = aggregated_data["chat_room_id"] if aggregated_data is not None else None
        channel_id = aggregated_data["channel_id"] if aggregated_data is not None else None
        chat_room_status = aggregated_data["chat_room_status"] if aggregated_data is not None else None
        client_id = aggregated_data["client_id"] if aggregated_data is not None else None

        # Get whatsapp bot token from the database.
        whatsapp_bot_token = get_whatsapp_bot_token(
            postgresql_connection=postgresql_connection,
            sql_arguments={
                "business_account": business_account
            }
        )

        # Define the list of available message types.
        available_types = ["text", "location", "image", "video", "document", "voice"]

        # Check the message type.
        if chat_room_id is None and any(message_type == available_type for available_type in available_types[1:]):
            send_message_text_to_whatsapp(
                whatsapp_bot_token=whatsapp_bot_token,
                whatsapp_chat_id=whatsapp_chat_id,
                message_text="🤖💬\nОпишите пожалуйста сперва вашу проблему в текстовом формате."
            )
        elif message_type not in available_types:
            send_message_text_to_whatsapp(
                whatsapp_bot_token=whatsapp_bot_token,
                whatsapp_chat_id=whatsapp_chat_id,
                message_text="🤖💬\nОбработка данного формата сообщения недоступна."
            )
        else:
            # Form the format of the message (text and content) depending on the message category.
            message_text, message_content = form_message_format(
                message=message,
                chat_room_id=chat_room_id,
                whatsapp_bot_token=whatsapp_bot_token
            )

            # Form the message content values.
            last_message_content = json.dumps({"messageText": message_text, "messageContent": message_content})
            message_content = json.dumps(message_content) if message_content is not None else None

            # Check the chat room status.
            if chat_room_status is None:
                # Check whether the user was registered in the system earlier.
                client_id = get_identified_user_data(
                    postgresql_connection=postgresql_connection,
                    sql_arguments={
                        "whatsapp_username": whatsapp_username
                    }
                )

                # Create the new user.
                if client_id is None:
                    client_id = create_identified_user(
                        postgresql_connection=postgresql_connection,
                        sql_arguments={
                            "identified_user_primary_phone_number": "+{0}".format(whatsapp_username),
                            "metadata": json.dumps(metadata),
                            "whatsapp_profile": whatsapp_profile,
                            "whatsapp_username": whatsapp_username
                        }
                    )

                # Create the new chat room.
                chat_room = create_chat_room(
                    channel_technical_id=whatsapp_bot_token,
                    client_id=client_id,
                    last_message_content=last_message_content,
                    whatsapp_chat_id="{0}:{1}".format(business_account, whatsapp_chat_id)
                )

                # Define a few necessary variables that will be used in the future.
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
                    last_message_content=last_message_content
                )

            # Send the message to the operator and save it in the database.
            chat_room_message = create_chat_room_message(
                chat_room_id=chat_room_id,
                message_author_id=client_id,
                message_channel_id=channel_id,
                message_text=message_text,
                message_content=message_content
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

    # Return the status code 200.
    return {
        "statusCode": 200
    }
