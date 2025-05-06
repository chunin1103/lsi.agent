import google.auth
from google_auth_oauthlib.flow import InstalledAppFlow

CLIENT_SECRETS_FILE = "credentials.json"
SCOPES = ["https://www.googleapis.com/auth/adwords"]

def generate_refresh_token():
    flow = InstalledAppFlow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES
    )

    credentials = flow.run_local_server(port=8080, prompt='consent')

    print("Refresh token:", credentials.refresh_token)

if __name__ == "__main__":
    generate_refresh_token()
