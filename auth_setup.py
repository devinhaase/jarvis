# SUPERSEDED (Phase 6 item 4) — use google_auth_setup.py instead. This Phase 1 script only
# ever requests gmail.readonly and stores the token as plain JSON at the project root;
# google_auth_setup.py requests the full current scope set (Gmail read+compose, Calendar,
# Drive) and stores it encrypted at data/google_token.enc. Left in place, unmodified,
# rather than deleted — it's not wired into anything else in this codebase anymore
# (tools.py's read_recent_emails and google_tools.py both use google_auth.py now), so
# nothing breaks by its continued existence, but there's no reason to run it going forward.

import os.path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Using read-only scope for Gmail as a safe default for our Tier 1 tools
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def authenticate():
    creds = None
    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first time.
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        print("Existing token.json found.")
        
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing expired token...")
            creds.refresh(Request())
        else:
            if not os.path.exists('credentials.json'):
                print("ERROR: 'credentials.json' not found in the current directory!")
                print("Please follow the instructions in the chat to download it from Google Cloud Console.")
                return None
            
            print("Starting Google Authentication flow. A browser window should open...")
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
            
        # Save the credentials for the next run
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
            print("Success! 'token.json' has been generated and saved.")
            
    else:
        print("Credentials are valid.")
        
    return creds

if __name__ == '__main__':
    authenticate()
