# # setup_gmail.py
# import os
# import logging
# from google.auth.transport.requests import Request
# from google.oauth2.credentials import Credentials
# from google_auth_oauthlib.flow import InstalledAppFlow
# from googleapiclient.discovery import build, Resource
# from googleapiclient.errors import HttpError

# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
# logger = logging.getLogger(__name__)

# # Define the scopes required by the application
# _SCOPES = [
#     "https://mail.google.com/",   # Read, compose, send, modify Gmail
#     "https://www.googleapis.com/auth/calendar",       # Read/write access to Calendars
#     "https://www.googleapis.com/auth/tasks" ,         # Read/write access to Tasks
# ]

# # Define file paths for credentials and token
# TOKEN_PATH = 'token.json'
# CREDENTIALS_PATH = 'credentials.json'  # Expects Google Cloud OAuth 2.0 Client ID JSON

# def get_credentials() -> Credentials | None:
#     """
#     Authenticates the user via OAuth 2.0, handling token refresh and storage.

#     Returns:
#         Credentials: The authenticated credentials object, or None if authentication fails.
#     """
#     creds = None
#     try:
#         # Load existing token if available
#         if os.path.exists(TOKEN_PATH):
#             creds = Credentials.from_authorized_user_file(TOKEN_PATH, _SCOPES)
#             logger.info("Loaded credentials from token.json")

#         # If there are no (valid) credentials available, let the user log in.
#         if not creds or not creds.valid:
#             if creds and creds.expired and creds.refresh_token:
#                 logger.info("Refreshing expired credentials...")
#                 try:
#                     creds.refresh(Request())
#                     logger.info("Credentials refreshed successfully.")
#                 except Exception as e:
#                     logger.error(f"Failed to refresh token: {e}. Need re-authentication.")
#                     creds = None
#                     if os.path.exists(TOKEN_PATH):
#                         os.remove(TOKEN_PATH)  # Remove invalid token
#             else:
#                 logger.info("No valid credentials found, initiating OAuth flow...")
#                 if not os.path.exists(CREDENTIALS_PATH):
#                     logger.error(
#                         f"'{CREDENTIALS_PATH}' not found. Please download your OAuth 2.0 Client ID JSON "
#                         f"from Google Cloud Console and save it as '{CREDENTIALS_PATH}'."
#                     )
#                     return None
#                 flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, _SCOPES)
#                 creds = flow.run_local_server(port=0)
#                 logger.info("OAuth flow completed successfully.")

#             # Save the credentials for the next run
#             if creds:
#                 with open(TOKEN_PATH, 'w') as token_file:
#                     token_file.write(creds.to_json())
#                 logger.info(f"Credentials saved to {TOKEN_PATH}")

#     except FileNotFoundError:
#         logger.error(f"'{CREDENTIALS_PATH}' not found. Cannot initiate authentication.")
#         return None
#     except Exception as e:
#         logger.exception(f"An unexpected error occurred during authentication: {e}")
#         return None

#     # Final check: if credentials exist but might lack required scopes.
#     if creds and not creds.has_scopes(_SCOPES):
#         logger.warning("Credentials exist but lack required scopes. Re-authentication might be needed.")

#     return creds

# def get_gmail_service(credentials: Credentials) -> Resource | None:
#     """Builds and returns a Gmail API service object."""
#     if not credentials or not credentials.valid:
#         logger.error("Cannot build Gmail service: Invalid credentials provided.")
#         return None
#     try:
#         service = build('gmail', 'v1', credentials=credentials)
#         logger.info("Gmail service built successfully.")
#         return service
#     except Exception as e:
#         logger.exception(f"Failed to build Gmail service: {e}")
#         return None

# def get_calendar_service(credentials: Credentials) -> Resource | None:
#     """Builds and returns a Google Calendar API service object."""
#     if not credentials or not credentials.valid:
#         logger.error("Cannot build Calendar service: Invalid credentials provided.")
#         return None
#     try:
#         service = build('calendar', 'v3', credentials=credentials)
#         logger.info("Calendar service built successfully.")
#         return service
#     except Exception as e:
#         logger.exception(f"Failed to build Calendar service: {e}")
#         return None

# def get_tasks_service(credentials: Credentials) -> Resource | None:
#     """Builds and returns a Google Tasks API service object."""
#     if not credentials or not credentials.valid:
#         logger.error("Cannot build Tasks service: Invalid credentials provided.")
#         return None
#     try:
#         service = build('tasks', 'v1', credentials=credentials)
#         logger.info("Tasks service built successfully.")
#         return service
#     except Exception as e:
#         logger.exception(f"Failed to build Tasks service: {e}")
#         return None

# def get_user_email(gmail_service: Resource) -> str | None:
#     """Retrieves the primary email address of the authenticated user."""
#     if not gmail_service:
#         logger.error("Gmail service object is required to get user email.")
#         return None
#     try:
#         profile = gmail_service.users().getProfile(userId='me').execute()
#         email = profile.get('emailAddress')
#         if email:
#             logger.info(f"Retrieved user email: {email}")
#             return email
#         else:
#             logger.error("Could not retrieve email address from profile.")
#             return None
#     except HttpError as error:
#         logger.error(f"An API error occurred while getting user profile: {error}")
#         return None
#     except Exception as e:
#         logger.exception(f"An unexpected error occurred while getting user email: {e}")
#         return None
