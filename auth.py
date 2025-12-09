"""
Google OAuth Authentication Service
Handles OAuth flow for Google Drive access
"""

import os
import json
from flask import session, redirect, url_for, request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request

# OAuth 2.0 scopes - all scopes configured in Google Cloud Console
SCOPES = [
    'openid',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile',
    'https://www.googleapis.com/auth/drive',  # Full Drive access (read/write)
    'https://www.googleapis.com/auth/documents',  # Google Docs API for editing
    'https://www.googleapis.com/auth/spreadsheets',  # Google Sheets API for editing
    'https://www.googleapis.com/auth/presentations',  # Google Slides API for editing
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/calendar.events',
]


def get_google_client_config():
    """Get Google OAuth client configuration from environment variables"""
    return {
        "web": {
            "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
            "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [get_redirect_uri()],
        }
    }


def get_redirect_uri():
    """Get the OAuth redirect URI based on environment"""
    base_url = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:5000")
    return f"{base_url}/oauth2callback"


def create_oauth_flow():
    """Create OAuth flow for Google authentication"""
    client_config = get_google_client_config()

    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=get_redirect_uri()
    )

    return flow


def get_authorization_url():
    """Generate authorization URL for user to grant access"""
    flow = create_oauth_flow()

    authorization_url, state = flow.authorization_url(
        access_type='offline',
        prompt='consent'
    )

    return authorization_url, state


def exchange_code_for_credentials(authorization_response, state):
    """Exchange authorization code for credentials"""
    flow = create_oauth_flow()
    flow.state = state

    flow.fetch_token(authorization_response=authorization_response)

    return flow.credentials


def credentials_to_dict(credentials):
    """Convert credentials object to dictionary for session storage"""
    return {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }


def dict_to_credentials(credentials_dict):
    """Convert dictionary back to credentials object"""
    return Credentials(
        token=credentials_dict['token'],
        refresh_token=credentials_dict.get('refresh_token'),
        token_uri=credentials_dict['token_uri'],
        client_id=credentials_dict['client_id'],
        client_secret=credentials_dict['client_secret'],
        scopes=credentials_dict['scopes']
    )


def get_valid_credentials():
    """Get valid credentials from session, refreshing if necessary"""
    if 'credentials' not in session:
        return None

    credentials = dict_to_credentials(session['credentials'])

    # Check if credentials are valid
    if not credentials.valid:
        if credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
                session['credentials'] = credentials_to_dict(credentials)
            except Exception as e:
                print(f"Error refreshing credentials: {e}")
                return None
        else:
            return None

    return credentials


def is_authenticated():
    """Check if user is authenticated with valid credentials"""
    return get_valid_credentials() is not None


def logout():
    """Clear user session and credentials"""
    if 'credentials' in session:
        del session['credentials']
    if 'state' in session:
        del session['state']
    session.clear()
