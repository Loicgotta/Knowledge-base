# Guide d'Intégration - Agent IA Google Docs/Sheets

Ce guide explique comment intégrer l'agent IA de modification de documents Google (Docs, Sheets, Slides) à votre plateforme existante.

---

## Table des matières

1. [Prérequis](#1-prérequis)
2. [Configuration Google Cloud Console](#2-configuration-google-cloud-console)
3. [Installation des dépendances](#3-installation-des-dépendances)
4. [Architecture de l'intégration](#4-architecture-de-lintégration)
5. [Code d'authentification OAuth](#5-code-dauthentification-oauth)
6. [Service de modification des documents](#6-service-de-modification-des-documents)
7. [Fonctions d'exécution des commandes IA](#7-fonctions-dexécution-des-commandes-ia)
8. [Prompts système pour l'IA](#8-prompts-système-pour-lia)
9. [Intégration dans votre endpoint](#9-intégration-dans-votre-endpoint)
10. [Exemple complet](#10-exemple-complet)
11. [Format des commandes IA](#11-format-des-commandes-ia)
12. [Dépannage](#12-dépannage)

---

## 1. Prérequis

- Python 3.8+
- Un compte Google Cloud Platform
- Une plateforme existante avec un système de sessions (Flask, FastAPI, Django, etc.)
- Une clé API OpenAI (ou autre LLM)

---

## 2. Configuration Google Cloud Console

### 2.1 Créer un projet

1. Accédez à [Google Cloud Console](https://console.cloud.google.com/)
2. Créez un nouveau projet ou sélectionnez un projet existant

### 2.2 Activer les APIs

Activez les APIs suivantes dans **APIs & Services > Library** :

- Google Drive API
- Google Docs API
- Google Sheets API
- Google Slides API

### 2.3 Configurer l'écran de consentement OAuth

1. Allez dans **APIs & Services > OAuth consent screen**
2. Choisissez **External** (ou Internal si G Suite)
3. Remplissez les informations :
   - Nom de l'application
   - Email de support
   - Logo (optionnel)
4. Ajoutez les **Scopes** suivants :
   ```
   openid
   https://www.googleapis.com/auth/userinfo.email
   https://www.googleapis.com/auth/userinfo.profile
   https://www.googleapis.com/auth/drive
   https://www.googleapis.com/auth/documents
   https://www.googleapis.com/auth/spreadsheets
   https://www.googleapis.com/auth/presentations
   ```
5. Ajoutez des utilisateurs de test si en mode "Testing"

### 2.4 Créer les identifiants OAuth

1. Allez dans **APIs & Services > Credentials**
2. Cliquez sur **Create Credentials > OAuth client ID**
3. Type d'application : **Web application**
4. Ajoutez les **Authorized redirect URIs** :
   - `http://localhost:5000/oauth2callback` (développement)
   - `https://votre-domaine.com/oauth2callback` (production)
5. Notez le **Client ID** et **Client Secret**

### 2.5 Variables d'environnement

Créez un fichier `.env` :

```bash
GOOGLE_CLIENT_ID=votre-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=votre-client-secret
BASE_URL=http://localhost:5000
OPENAI_API_KEY=votre-cle-openai
FLASK_SECRET_KEY=une-cle-secrete-aleatoire
```

---

## 3. Installation des dépendances

```bash
pip install google-auth google-auth-oauthlib google-api-python-client flask python-dotenv openai
```

Ou ajoutez à votre `requirements.txt` :

```
google-auth>=2.0.0
google-auth-oauthlib>=1.0.0
google-api-python-client>=2.0.0
flask>=2.0.0
python-dotenv>=1.0.0
openai>=1.0.0
```

---

## 4. Architecture de l'intégration

```
┌─────────────────────────────────────────────────────────────────┐
│                        VOTRE PLATEFORME                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │   Frontend   │───▶│   Endpoint   │───▶│   OpenAI/LLM     │  │
│  │  (Sélection  │    │  /chat-edit  │    │   (Génère les    │  │
│  │   document)  │    │              │    │   commandes)     │  │
│  └──────────────┘    └──────┬───────┘    └──────────────────┘  │
│                             │                                   │
│                             ▼                                   │
│                    ┌──────────────────┐                        │
│                    │ execute_modif()  │                        │
│                    │ (Parse & Execute)│                        │
│                    └────────┬─────────┘                        │
│                             │                                   │
│                             ▼                                   │
│                    ┌──────────────────┐                        │
│                    │  DriveService    │                        │
│                    │  (API Google)    │                        │
│                    └────────┬─────────┘                        │
│                             │                                   │
└─────────────────────────────┼───────────────────────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │   Google APIs    │
                    │ Drive/Docs/Sheets│
                    └──────────────────┘
```

**Flux de données :**
1. L'utilisateur sélectionne un document et envoie un message
2. Votre endpoint construit le prompt avec le contenu du document
3. L'IA génère une réponse avec une commande de modification
4. La fonction `execute_modification()` parse et exécute la commande
5. Le document Google est modifié via l'API

---

## 5. Code d'authentification OAuth

Créez le fichier `google_auth.py` :

```python
"""
Module d'authentification Google OAuth 2.0
Gère le flow OAuth pour accéder à Drive, Docs, Sheets, Slides
"""

import os
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request


# === SCOPES - Permissions demandées à l'utilisateur ===
SCOPES = [
    'openid',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile',
    'https://www.googleapis.com/auth/drive',              # Drive (lecture/écriture)
    'https://www.googleapis.com/auth/documents',          # Google Docs
    'https://www.googleapis.com/auth/spreadsheets',       # Google Sheets
    'https://www.googleapis.com/auth/presentations',      # Google Slides
]


def get_google_client_config():
    """Configuration OAuth depuis variables d'environnement"""
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
    """URI de redirection après authentification"""
    base_url = os.environ.get("BASE_URL", "http://localhost:5000")
    return f"{base_url}/oauth2callback"


def create_oauth_flow():
    """Créer le flow OAuth"""
    return Flow.from_client_config(
        get_google_client_config(),
        scopes=SCOPES,
        redirect_uri=get_redirect_uri()
    )


def get_authorization_url():
    """
    Générer l'URL d'autorisation Google

    Returns:
        tuple: (authorization_url, state)
        - authorization_url: URL vers laquelle rediriger l'utilisateur
        - state: Token anti-CSRF à stocker en session
    """
    flow = create_oauth_flow()
    authorization_url, state = flow.authorization_url(
        access_type='offline',      # Pour obtenir un refresh_token
        prompt='consent'            # Force l'affichage du consentement
    )
    return authorization_url, state


def exchange_code_for_credentials(authorization_response, state):
    """
    Échanger le code d'autorisation contre des credentials

    Args:
        authorization_response: URL complète du callback (avec le code)
        state: Token state stocké en session

    Returns:
        Credentials: Objet credentials Google
    """
    flow = create_oauth_flow()
    flow.state = state
    flow.fetch_token(authorization_response=authorization_response)
    return flow.credentials


def credentials_to_dict(credentials):
    """Convertir credentials en dict pour stockage (session, DB, etc.)"""
    return {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': list(credentials.scopes) if credentials.scopes else []
    }


def dict_to_credentials(credentials_dict):
    """Convertir dict en objet Credentials"""
    return Credentials(
        token=credentials_dict['token'],
        refresh_token=credentials_dict.get('refresh_token'),
        token_uri=credentials_dict['token_uri'],
        client_id=credentials_dict['client_id'],
        client_secret=credentials_dict['client_secret'],
        scopes=credentials_dict.get('scopes', [])
    )


def refresh_credentials_if_needed(credentials_dict):
    """
    Rafraîchir les credentials si expirés

    Args:
        credentials_dict: Dict des credentials stockés

    Returns:
        tuple: (credentials, updated_dict) ou (None, None) si échec
    """
    credentials = dict_to_credentials(credentials_dict)

    if not credentials.valid:
        if credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
                return credentials, credentials_to_dict(credentials)
            except Exception as e:
                print(f"Erreur refresh credentials: {e}")
                return None, None
        else:
            return None, None

    return credentials, credentials_dict
```

### Routes Flask pour OAuth

Ajoutez ces routes à votre application Flask :

```python
from flask import Flask, redirect, request, session, url_for, jsonify
from google_auth import (
    get_authorization_url,
    exchange_code_for_credentials,
    credentials_to_dict,
    refresh_credentials_if_needed
)
import os

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key')

# Autoriser OAuth en HTTP (développement local uniquement!)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'


@app.route('/auth/google/login')
def google_login():
    """
    Démarrer le flow OAuth Google
    Redirige l'utilisateur vers la page de consentement Google
    """
    authorization_url, state = get_authorization_url()
    session['oauth_state'] = state
    return redirect(authorization_url)


@app.route('/oauth2callback')
def oauth2callback():
    """
    Callback OAuth - Google redirige ici après consentement
    """
    # Vérifier le state anti-CSRF
    state = session.get('oauth_state')
    if not state:
        return jsonify({'error': 'State manquant'}), 400

    try:
        # Échanger le code contre des credentials
        credentials = exchange_code_for_credentials(
            authorization_response=request.url,
            state=state
        )

        # Stocker les credentials en session (ou en DB)
        session['google_credentials'] = credentials_to_dict(credentials)

        # Nettoyer le state
        del session['oauth_state']

        # Rediriger vers votre page principale
        return redirect(url_for('index'))

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/auth/google/logout')
def google_logout():
    """Déconnexion Google"""
    if 'google_credentials' in session:
        del session['google_credentials']
    return redirect(url_for('index'))


def get_valid_credentials():
    """
    Helper pour récupérer des credentials valides
    À appeler avant chaque opération Google
    """
    if 'google_credentials' not in session:
        return None

    credentials, updated_dict = refresh_credentials_if_needed(session['google_credentials'])

    if credentials and updated_dict:
        session['google_credentials'] = updated_dict
        return credentials

    return None


def require_google_auth(f):
    """Décorateur pour protéger les routes nécessitant l'auth Google"""
    from functools import wraps

    @wraps(f)
    def decorated_function(*args, **kwargs):
        credentials = get_valid_credentials()
        if not credentials:
            return jsonify({'error': 'Non authentifié Google', 'redirect': '/auth/google/login'}), 401
        return f(*args, **kwargs)

    return decorated_function
```

---

## 6. Service de modification des documents

Créez le fichier `drive_service.py` :

```python
"""
Service Google Drive/Docs/Sheets/Slides
Gère toutes les opérations de lecture et modification des documents
"""

from typing import Dict, List, Optional
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


class DriveService:
    """Service pour interagir avec les APIs Google"""

    def __init__(self, credentials):
        """
        Initialiser les services Google avec les credentials OAuth

        Args:
            credentials: Objet Credentials Google valide
        """
        self.credentials = credentials
        self.drive = build('drive', 'v3', credentials=credentials)
        self.docs = build('docs', 'v1', credentials=credentials)
        self.sheets = build('sheets', 'v4', credentials=credentials)
        self.slides = build('slides', 'v1', credentials=credentials)

    # ==================== LECTURE ====================

    def list_files(self, mime_types: List[str] = None, folder_id: str = None) -> List[Dict]:
        """
        Lister les fichiers du Drive

        Args:
            mime_types: Liste des types MIME à filtrer (optionnel)
            folder_id: ID du dossier parent (optionnel)

        Returns:
            Liste des fichiers [{id, name, mimeType}, ...]
        """
        query_parts = ["trashed=false"]

        if mime_types:
            mime_query = " or ".join([f"mimeType='{mt}'" for mt in mime_types])
            query_parts.append(f"({mime_query})")

        if folder_id:
            query_parts.append(f"'{folder_id}' in parents")

        query = " and ".join(query_parts)

        try:
            results = self.drive.files().list(
                q=query,
                pageSize=100,
                fields="files(id, name, mimeType, modifiedTime)"
            ).execute()
            return results.get('files', [])
        except HttpError as e:
            print(f"Erreur list_files: {e}")
            return []

    def list_editable_documents(self) -> List[Dict]:
        """Lister tous les documents éditables (Docs, Sheets, Slides)"""
        return self.list_files(mime_types=[
            'application/vnd.google-apps.document',
            'application/vnd.google-apps.spreadsheet',
            'application/vnd.google-apps.presentation'
        ])

    def get_document_content(self, file_id: str) -> str:
        """
        Lire le contenu textuel d'un Google Doc

        Args:
            file_id: ID du document

        Returns:
            Contenu texte du document
        """
        try:
            doc = self.docs.documents().get(documentId=file_id).execute()

            text_parts = []
            for element in doc.get('body', {}).get('content', []):
                if 'paragraph' in element:
                    for elem in element['paragraph'].get('elements', []):
                        if 'textRun' in elem:
                            text_parts.append(elem['textRun'].get('content', ''))

            return ''.join(text_parts)
        except HttpError as e:
            print(f"Erreur get_document_content: {e}")
            return ""

    def get_sheet_content(self, file_id: str, range_name: str = 'A1:Z1000') -> Dict:
        """
        Lire le contenu d'un Google Sheet

        Args:
            file_id: ID du spreadsheet
            range_name: Plage de cellules à lire

        Returns:
            Dict avec 'values': [[row1], [row2], ...]
        """
        try:
            result = self.sheets.spreadsheets().values().get(
                spreadsheetId=file_id,
                range=range_name
            ).execute()
            return result
        except HttpError as e:
            print(f"Erreur get_sheet_content: {e}")
            return {'values': []}

    # ==================== MODIFICATION GOOGLE DOCS ====================

    def replace_text_in_doc(self, file_id: str, find_text: str, replace_text: str) -> Dict:
        """
        Chercher et remplacer du texte dans un Google Doc

        Args:
            file_id: ID du document
            find_text: Texte à trouver
            replace_text: Texte de remplacement

        Returns:
            Dict avec status et message
        """
        try:
            requests = [{
                'replaceAllText': {
                    'containsText': {'text': find_text, 'matchCase': False},
                    'replaceText': replace_text
                }
            }]

            result = self.docs.documents().batchUpdate(
                documentId=file_id,
                body={'requests': requests}
            ).execute()

            occurrences = 0
            replies = result.get('replies', [])
            if replies and 'replaceAllText' in replies[0]:
                occurrences = replies[0]['replaceAllText'].get('occurrencesChanged', 0)

            return {
                'status': 'success',
                'message': f'{occurrences} occurrence(s) remplacée(s)',
                'occurrences': occurrences
            }
        except HttpError as e:
            return {'status': 'error', 'message': str(e)}

    def insert_text_after(self, file_id: str, after_text: str, new_text: str) -> Dict:
        """
        Insérer du texte après un texte spécifique

        Args:
            file_id: ID du document
            after_text: Texte après lequel insérer
            new_text: Texte à insérer

        Returns:
            Dict avec status et message
        """
        try:
            # Récupérer le document pour trouver la position
            doc = self.docs.documents().get(documentId=file_id).execute()

            full_text = ""
            for element in doc.get('body', {}).get('content', []):
                if 'paragraph' in element:
                    for elem in element['paragraph'].get('elements', []):
                        if 'textRun' in elem:
                            full_text += elem['textRun'].get('content', '')

            position = full_text.find(after_text)
            if position == -1:
                return {'status': 'error', 'message': f'Texte "{after_text}" non trouvé'}

            # +1 pour l'offset du document Google
            insert_index = position + len(after_text) + 1

            requests = [{
                'insertText': {
                    'location': {'index': insert_index},
                    'text': new_text
                }
            }]

            self.docs.documents().batchUpdate(
                documentId=file_id,
                body={'requests': requests}
            ).execute()

            return {'status': 'success', 'message': 'Texte inséré avec succès'}
        except HttpError as e:
            return {'status': 'error', 'message': str(e)}

    def delete_text_in_doc(self, file_id: str, text_to_delete: str) -> Dict:
        """
        Supprimer un texte spécifique du document

        Args:
            file_id: ID du document
            text_to_delete: Texte à supprimer

        Returns:
            Dict avec status et message
        """
        return self.replace_text_in_doc(file_id, text_to_delete, '')

    def append_to_doc(self, file_id: str, content: str) -> Dict:
        """
        Ajouter du contenu à la fin d'un Google Doc

        Args:
            file_id: ID du document
            content: Contenu à ajouter

        Returns:
            Dict avec status et message
        """
        try:
            doc = self.docs.documents().get(documentId=file_id).execute()

            end_index = 1
            for element in doc.get('body', {}).get('content', []):
                if 'endIndex' in element:
                    end_index = max(end_index, element['endIndex'])

            insert_index = max(1, end_index - 1)

            requests = [{
                'insertText': {
                    'location': {'index': insert_index},
                    'text': '\n' + content
                }
            }]

            self.docs.documents().batchUpdate(
                documentId=file_id,
                body={'requests': requests}
            ).execute()

            return {'status': 'success', 'message': 'Contenu ajouté à la fin'}
        except HttpError as e:
            return {'status': 'error', 'message': str(e)}

    def replace_doc_content(self, file_id: str, new_content: str) -> Dict:
        """
        Remplacer tout le contenu d'un Google Doc

        Args:
            file_id: ID du document
            new_content: Nouveau contenu complet

        Returns:
            Dict avec status et message
        """
        try:
            doc = self.docs.documents().get(documentId=file_id).execute()

            end_index = 1
            for element in doc.get('body', {}).get('content', []):
                if 'endIndex' in element:
                    end_index = max(end_index, element['endIndex'])

            requests = []

            # Supprimer le contenu existant
            if end_index > 2:
                requests.append({
                    'deleteContentRange': {
                        'range': {'startIndex': 1, 'endIndex': end_index - 1}
                    }
                })

            # Insérer le nouveau contenu
            if new_content:
                requests.append({
                    'insertText': {
                        'location': {'index': 1},
                        'text': new_content
                    }
                })

            if requests:
                self.docs.documents().batchUpdate(
                    documentId=file_id,
                    body={'requests': requests}
                ).execute()

            return {'status': 'success', 'message': 'Document remplacé'}
        except HttpError as e:
            return {'status': 'error', 'message': str(e)}

    # ==================== MODIFICATION GOOGLE SHEETS ====================

    def update_sheet_cells(self, file_id: str, updates: List[Dict]) -> Dict:
        """
        Modifier des cellules spécifiques dans un Google Sheet

        Args:
            file_id: ID du spreadsheet
            updates: Liste de mises à jour [{"cell": "A1", "value": "xxx"}, ...]

        Returns:
            Dict avec status et message
        """
        try:
            data = []
            for update in updates:
                cell = update.get('cell', '')
                value = update.get('value', '')
                if cell:
                    data.append({
                        'range': cell,
                        'values': [[value]]
                    })

            if not data:
                return {'status': 'error', 'message': 'Aucune mise à jour fournie'}

            result = self.sheets.spreadsheets().values().batchUpdate(
                spreadsheetId=file_id,
                body={
                    'valueInputOption': 'USER_ENTERED',
                    'data': data
                }
            ).execute()

            updated = result.get('totalUpdatedCells', 0)
            return {
                'status': 'success',
                'message': f'{updated} cellule(s) mise(s) à jour',
                'updated_cells': updated
            }
        except HttpError as e:
            return {'status': 'error', 'message': str(e)}
```

---

## 7. Fonctions d'exécution des commandes IA

Créez le fichier `command_executor.py` :

```python
"""
Exécuteur de commandes IA
Parse les réponses de l'IA et exécute les modifications sur les documents
"""

import json
from typing import Dict, Tuple, Optional
from drive_service import DriveService


def extract_json_from_command(answer: str, marker: str) -> Optional[Dict]:
    """
    Extraire le JSON d'une commande IA avec gestion des accolades imbriquées

    Args:
        answer: Réponse complète de l'IA
        marker: Marqueur de début (ex: '[MODIFY_CELLS:')

    Returns:
        Dict parsé ou None si non trouvé
    """
    start_idx = answer.find(marker)
    if start_idx == -1:
        return None

    json_start = start_idx + len(marker)
    brace_count = 0
    json_end = json_start
    in_string = False
    escape_next = False

    for i, char in enumerate(answer[json_start:], start=json_start):
        if escape_next:
            escape_next = False
            continue
        if char == '\\':
            escape_next = True
            continue
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == '{':
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0:
                json_end = i + 1
                break

    try:
        return json.loads(answer[json_start:json_end])
    except json.JSONDecodeError as e:
        print(f"Erreur parsing JSON: {e}")
        return None


def remove_command_from_answer(answer: str, marker: str) -> str:
    """
    Retirer la commande de la réponse pour affichage utilisateur

    Args:
        answer: Réponse complète de l'IA
        marker: Marqueur de début de la commande

    Returns:
        Réponse nettoyée sans la commande
    """
    start_idx = answer.find(marker)
    if start_idx == -1:
        return answer

    end_idx = start_idx
    brace_count = 0
    in_string = False

    for i, char in enumerate(answer[start_idx:], start=start_idx):
        if char == '"' and (i == 0 or answer[i-1] != '\\'):
            in_string = not in_string
        if not in_string:
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
            elif char == ']' and brace_count == 0:
                end_idx = i + 1
                break

    return (answer[:start_idx] + answer[end_idx:]).strip()


def execute_modification(answer: str, drive_service: DriveService) -> Tuple[Optional[Dict], str]:
    """
    Parser la réponse de l'IA et exécuter la modification appropriée

    Args:
        answer: Réponse de l'IA contenant potentiellement une commande
        drive_service: Instance de DriveService avec credentials valides

    Returns:
        Tuple (result, cleaned_answer):
        - result: Résultat de la modification ou None
        - cleaned_answer: Réponse sans la commande pour affichage
    """
    result = None

    # === GOOGLE SHEETS ===
    if '[MODIFY_CELLS:' in answer:
        try:
            cmd = extract_json_from_command(answer, '[MODIFY_CELLS:')
            if cmd:
                result = drive_service.update_sheet_cells(
                    cmd['file_id'],
                    cmd['updates']
                )
        except Exception as e:
            result = {'status': 'error', 'message': str(e)}
        answer = remove_command_from_answer(answer, '[MODIFY_CELLS:')

    # === GOOGLE DOCS - Remplacer texte ===
    elif '[REPLACE_TEXT:' in answer:
        try:
            cmd = extract_json_from_command(answer, '[REPLACE_TEXT:')
            if cmd:
                result = drive_service.replace_text_in_doc(
                    cmd['file_id'],
                    cmd['find'],
                    cmd['replace']
                )
        except Exception as e:
            result = {'status': 'error', 'message': str(e)}
        answer = remove_command_from_answer(answer, '[REPLACE_TEXT:')

    # === GOOGLE DOCS - Insérer après ===
    elif '[INSERT_AFTER:' in answer:
        try:
            cmd = extract_json_from_command(answer, '[INSERT_AFTER:')
            if cmd:
                result = drive_service.insert_text_after(
                    cmd['file_id'],
                    cmd['after'],
                    cmd['content']
                )
        except Exception as e:
            result = {'status': 'error', 'message': str(e)}
        answer = remove_command_from_answer(answer, '[INSERT_AFTER:')

    # === GOOGLE DOCS - Supprimer texte ===
    elif '[DELETE_TEXT:' in answer:
        try:
            cmd = extract_json_from_command(answer, '[DELETE_TEXT:')
            if cmd:
                result = drive_service.delete_text_in_doc(
                    cmd['file_id'],
                    cmd['text']
                )
        except Exception as e:
            result = {'status': 'error', 'message': str(e)}
        answer = remove_command_from_answer(answer, '[DELETE_TEXT:')

    # === GOOGLE DOCS - Append/Replace ===
    elif '[MODIFY_DOC:' in answer:
        try:
            cmd = extract_json_from_command(answer, '[MODIFY_DOC:')
            if cmd:
                if cmd.get('action') == 'append':
                    result = drive_service.append_to_doc(
                        cmd['file_id'],
                        cmd['content']
                    )
                elif cmd.get('action') == 'replace':
                    result = drive_service.replace_doc_content(
                        cmd['file_id'],
                        cmd['content']
                    )
        except Exception as e:
            result = {'status': 'error', 'message': str(e)}
        answer = remove_command_from_answer(answer, '[MODIFY_DOC:')

    return result, answer
```

---

## 8. Prompts système pour l'IA

Créez le fichier `prompts.py` :

```python
"""
Prompts système pour guider l'IA dans la modification des documents
"""


def build_docs_prompt(document: dict, doc_content: str) -> str:
    """
    Construire le prompt pour un Google Doc

    Args:
        document: Dict avec {id, name, mimeType}
        doc_content: Contenu textuel du document

    Returns:
        Prompt système complet
    """
    # Tronquer si trop long
    if len(doc_content) > 3000:
        doc_content = doc_content[:3000] + "\n... (contenu tronqué)"

    return f"""Tu es un assistant qui modifie le document "{document['name']}".

=== METHODOLOGIE DE MODIFICATION ===

***** REGLE FONDAMENTALE *****
NE REMPLACE PAS TOUT LE DOCUMENT si seule une partie doit être modifiée!
Analyse d'abord le contenu, puis choisis la commande APPROPRIEE.
*****************************

CONTENU ACTUEL DU DOCUMENT:
--- DEBUT ---
{doc_content}
--- FIN ---

=== COMMANDES DISPONIBLES ===

1. REMPLACER un texte spécifique par un autre:
[REPLACE_TEXT:{{"file_id":"{document['id']}", "find":"texte à trouver", "replace":"nouveau texte"}}]
→ Utilise pour: modifier un titre, corriger une faute, changer un mot/phrase

2. INSERER du texte après un texte existant:
[INSERT_AFTER:{{"file_id":"{document['id']}", "after":"texte existant", "content":"texte à insérer"}}]
→ Utilise pour: ajouter après une section, insérer un paragraphe

3. SUPPRIMER un texte:
[DELETE_TEXT:{{"file_id":"{document['id']}", "text":"texte à supprimer"}}]
→ Utilise pour: enlever un paragraphe, supprimer une phrase

4. AJOUTER à la fin du document:
[MODIFY_DOC:{{"file_id":"{document['id']}", "action":"append", "content":"texte à ajouter"}}]
→ Utilise pour: ajouter une conclusion, ajouter une section à la fin

5. REMPLACER TOUT le document (utiliser RAREMENT!):
[MODIFY_DOC:{{"file_id":"{document['id']}", "action":"replace", "content":"nouveau contenu complet"}}]
→ Utilise UNIQUEMENT si l'utilisateur demande explicitement de tout réécrire

=== EXEMPLES ===

Demande: "Change le titre en 'Nouveau Titre'"
→ [REPLACE_TEXT:{{"file_id":"...", "find":"Ancien Titre", "replace":"Nouveau Titre"}}]

Demande: "Ajoute une conclusion"
→ [MODIFY_DOC:{{"file_id":"...", "action":"append", "content":"\\n\\nConclusion\\n\\nEn conclusion..."}}]

Demande: "Supprime le paragraphe sur les risques"
→ [DELETE_TEXT:{{"file_id":"...", "text":"Le paragraphe complet sur les risques..."}}]

=== REGLES ===
1. Fais la modification IMMEDIATEMENT en ajoutant la commande
2. Après la commande, confirme brièvement ce qui a été fait
3. NE DIS JAMAIS "je vais faire" - FAIS-LE d'abord!
"""


def build_sheets_prompt(document: dict, values: list) -> str:
    """
    Construire le prompt pour un Google Sheet

    Args:
        document: Dict avec {id, name, mimeType}
        values: Liste des lignes [[row1], [row2], ...]

    Returns:
        Prompt système complet
    """
    # Formater les données avec références de cellules
    sheet_display = ""
    for row_idx, row in enumerate(values[:50], start=1):
        row_str = f"Ligne {row_idx}: "
        for col_idx, cell in enumerate(row):
            col_letter = chr(65 + col_idx) if col_idx < 26 else f"A{chr(65 + col_idx - 26)}"
            row_str += f"[{col_letter}{row_idx}={cell}] "
        sheet_display += row_str.strip() + "\n"

    if len(values) > 50:
        sheet_display += f"... et {len(values) - 50} lignes supplémentaires\n"

    return f"""Tu es un assistant qui modifie le tableur "{document['name']}".

=== METHODOLOGIE GOOGLE SHEETS ===

***** REGLE FONDAMENTALE *****
CHAQUE DONNEE = UNE CELLULE SEPAREE
JAMAIS plusieurs informations dans une seule cellule!
*****************************

STRUCTURE:
- COLONNES = LETTRES (A, B, C, D...)
  → A1, A2, A3 = même colonne A
- LIGNES = CHIFFRES (1, 2, 3...)
  → A1, B1, C1 = même ligne 1

DONNEES ACTUELLES:
```
{sheet_display}
```

=== COMMANDE DISPONIBLE ===

[MODIFY_CELLS:{{"file_id":"{document['id']}", "updates":[{{"cell":"A1", "value":"xxx"}}, {{"cell":"B1", "value":"yyy"}}]}}]

=== METHODOLOGIE ===

1. IDENTIFIER les en-têtes (ligne 1 généralement):
   - Quelle colonne pour quel type de donnée?
   - Ex: A=Date, B=Utilisateurs, C=Agent1, D=Agent2

2. TROUVER la dernière ligne de données

3. MAPPER chaque info vers sa colonne:
   - "lundi 8 décembre" → Date → colonne A
   - "12 utilisateurs" → Utilisateurs → colonne B
   - "Agent1 a eu 3" → Agent1 → colonne C

4. CREER une cellule pour CHAQUE donnée:
   CORRECT: A6="Lundi 8 dec", B6="12", C6="3"
   INCORRECT: A6="Lundi 8 dec, 12 utilisateurs, Agent1: 3" ← INTERDIT!

=== EXEMPLE ===

Structure: A=Date, B=Total, C=Friday, D=Campagne
Dernière ligne: 5

Demande: "Ajoute lundi 8 déc, 12 users, Friday 1, Campagne 1"

Analyse:
- Nouvelle ligne = 6
- Date → A6
- 12 → B6
- Friday 1 → C6
- Campagne 1 → D6

Commande:
[MODIFY_CELLS:{{"file_id":"...", "updates":[{{"cell":"A6", "value":"Lundi 8 décembre"}}, {{"cell":"B6", "value":"12"}}, {{"cell":"C6", "value":"1"}}, {{"cell":"D6", "value":"1"}}]}}]

=== REGLES ===
1. Fais la modification IMMEDIATEMENT
2. Confirme brièvement après
3. Respecte TOUJOURS la structure existante
"""


def build_slides_prompt(document: dict) -> str:
    """
    Construire le prompt pour Google Slides

    Args:
        document: Dict avec {id, name, mimeType}

    Returns:
        Prompt système complet
    """
    return f"""Tu es un assistant qui modifie la présentation "{document['name']}".

=== COMMANDE DISPONIBLE ===

Pour ajouter une diapositive à la fin:
[MODIFY_DOC:{{"file_id":"{document['id']}", "action":"append", "content":"Titre de la slide\\n\\nContenu de la slide"}}]

=== REGLES ===
1. Fais la modification IMMEDIATEMENT
2. Confirme brièvement après
"""
```

---

## 9. Intégration dans votre endpoint

Créez l'endpoint principal dans votre application :

```python
from flask import Flask, request, jsonify, session
from openai import OpenAI
from google_auth import refresh_credentials_if_needed, dict_to_credentials
from drive_service import DriveService
from command_executor import execute_modification
from prompts import build_docs_prompt, build_sheets_prompt, build_slides_prompt
import os

app = Flask(__name__)
openai_client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))


@app.route('/api/chat-edit', methods=['POST'])
def chat_edit():
    """
    Endpoint principal pour modifier un document via chat

    Body JSON attendu:
    {
        "message": "Le message de l'utilisateur",
        "document": {
            "id": "google-file-id",
            "name": "Nom du document",
            "mimeType": "application/vnd.google-apps.document"
        },
        "history": [
            {"role": "user", "content": "..."},
            {"role": "assistant", "content": "..."}
        ]
    }
    """

    # 1. Vérifier l'authentification
    if 'google_credentials' not in session:
        return jsonify({'error': 'Non authentifié', 'redirect': '/auth/google/login'}), 401

    # 2. Rafraîchir les credentials si nécessaire
    credentials, updated = refresh_credentials_if_needed(session['google_credentials'])
    if not credentials:
        return jsonify({'error': 'Session expirée', 'redirect': '/auth/google/login'}), 401

    if updated:
        session['google_credentials'] = updated

    # 3. Parser la requête
    data = request.json
    if not data or 'message' not in data or 'document' not in data:
        return jsonify({'error': 'Paramètres manquants'}), 400

    message = data['message']
    document = data['document']
    history = data.get('history', [])

    try:
        # 4. Initialiser le service Drive
        drive_service = DriveService(credentials)

        # 5. Construire le prompt selon le type de document
        mime_type = document.get('mimeType', '')

        if 'spreadsheet' in mime_type:
            # Google Sheets
            content = drive_service.get_sheet_content(document['id'])
            system_prompt = build_sheets_prompt(document, content.get('values', []))

        elif 'presentation' in mime_type:
            # Google Slides
            system_prompt = build_slides_prompt(document)

        else:
            # Google Docs (par défaut)
            content = drive_service.get_document_content(document['id'])
            system_prompt = build_docs_prompt(document, content)

        # 6. Construire les messages pour l'IA
        messages = [{"role": "system", "content": system_prompt}]

        # Ajouter l'historique (limité aux 6 derniers messages)
        for msg in history[-6:]:
            messages.append(msg)

        messages.append({"role": "user", "content": message})

        # 7. Appeler l'IA
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",  # ou gpt-4, gpt-3.5-turbo, etc.
            messages=messages,
            temperature=0.7,
            max_tokens=2000
        )

        ai_answer = response.choices[0].message.content

        # 8. Exécuter les modifications si présentes
        modification_result, clean_answer = execute_modification(ai_answer, drive_service)

        # 9. Retourner la réponse
        return jsonify({
            'answer': clean_answer,
            'modification_result': modification_result
        })

    except Exception as e:
        import traceback
        print(f"Erreur chat-edit: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/list-documents', methods=['GET'])
def list_documents():
    """Lister les documents éditables de l'utilisateur"""

    if 'google_credentials' not in session:
        return jsonify({'error': 'Non authentifié'}), 401

    credentials, updated = refresh_credentials_if_needed(session['google_credentials'])
    if not credentials:
        return jsonify({'error': 'Session expirée'}), 401

    if updated:
        session['google_credentials'] = updated

    try:
        drive_service = DriveService(credentials)
        documents = drive_service.list_editable_documents()
        return jsonify({'documents': documents})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
```

---

## 10. Exemple complet

### Structure de fichiers

```
votre-projet/
├── app.py                  # Application Flask principale
├── google_auth.py          # Module authentification OAuth
├── drive_service.py        # Service Google APIs
├── command_executor.py     # Exécuteur de commandes IA
├── prompts.py              # Prompts système
├── requirements.txt        # Dépendances
├── .env                    # Variables d'environnement
└── templates/
    └── index.html          # Interface utilisateur
```

### Fichier `app.py` complet

```python
"""
Application principale - Agent IA Google Docs
"""

import os
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_cors import CORS
from dotenv import load_dotenv
from openai import OpenAI

# Charger les variables d'environnement
load_dotenv()

# Imports locaux
from google_auth import (
    get_authorization_url,
    exchange_code_for_credentials,
    credentials_to_dict,
    refresh_credentials_if_needed
)
from drive_service import DriveService
from command_executor import execute_modification
from prompts import build_docs_prompt, build_sheets_prompt, build_slides_prompt

# Initialisation Flask
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key')
CORS(app)

# OAuth en HTTP (dev uniquement)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# Client OpenAI
openai_client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))


# ============== ROUTES AUTH ==============

@app.route('/auth/google/login')
def google_login():
    authorization_url, state = get_authorization_url()
    session['oauth_state'] = state
    return redirect(authorization_url)


@app.route('/oauth2callback')
def oauth2callback():
    state = session.get('oauth_state')
    if not state:
        return jsonify({'error': 'State manquant'}), 400

    credentials = exchange_code_for_credentials(request.url, state)
    session['google_credentials'] = credentials_to_dict(credentials)
    del session['oauth_state']

    return redirect(url_for('index'))


@app.route('/auth/google/logout')
def google_logout():
    session.clear()
    return redirect(url_for('index'))


# ============== ROUTES API ==============

@app.route('/')
def index():
    is_authenticated = 'google_credentials' in session
    return render_template('index.html', authenticated=is_authenticated)


@app.route('/api/list-documents')
def list_documents():
    if 'google_credentials' not in session:
        return jsonify({'error': 'Non authentifié'}), 401

    credentials, updated = refresh_credentials_if_needed(session['google_credentials'])
    if not credentials:
        return jsonify({'error': 'Session expirée'}), 401
    if updated:
        session['google_credentials'] = updated

    drive_service = DriveService(credentials)
    documents = drive_service.list_editable_documents()
    return jsonify({'documents': documents})


@app.route('/api/chat-edit', methods=['POST'])
def chat_edit():
    if 'google_credentials' not in session:
        return jsonify({'error': 'Non authentifié'}), 401

    credentials, updated = refresh_credentials_if_needed(session['google_credentials'])
    if not credentials:
        return jsonify({'error': 'Session expirée'}), 401
    if updated:
        session['google_credentials'] = updated

    data = request.json
    message = data['message']
    document = data['document']
    history = data.get('history', [])

    drive_service = DriveService(credentials)
    mime_type = document.get('mimeType', '')

    # Construire le prompt
    if 'spreadsheet' in mime_type:
        content = drive_service.get_sheet_content(document['id'])
        system_prompt = build_sheets_prompt(document, content.get('values', []))
    elif 'presentation' in mime_type:
        system_prompt = build_slides_prompt(document)
    else:
        content = drive_service.get_document_content(document['id'])
        system_prompt = build_docs_prompt(document, content)

    # Appel IA
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history[-6:])
    messages.append({"role": "user", "content": message})

    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.7
    )

    ai_answer = response.choices[0].message.content
    result, clean_answer = execute_modification(ai_answer, drive_service)

    return jsonify({
        'answer': clean_answer,
        'modification_result': result
    })


if __name__ == '__main__':
    app.run(debug=True, port=5000)
```

---

## 11. Format des commandes IA

### Google Docs

| Commande | Format | Usage |
|----------|--------|-------|
| Remplacer texte | `[REPLACE_TEXT:{"file_id":"...", "find":"...", "replace":"..."}]` | Modifier un mot, titre, phrase |
| Insérer après | `[INSERT_AFTER:{"file_id":"...", "after":"...", "content":"..."}]` | Ajouter après une section |
| Supprimer | `[DELETE_TEXT:{"file_id":"...", "text":"..."}]` | Supprimer un paragraphe |
| Ajouter à la fin | `[MODIFY_DOC:{"file_id":"...", "action":"append", "content":"..."}]` | Ajouter une conclusion |
| Tout remplacer | `[MODIFY_DOC:{"file_id":"...", "action":"replace", "content":"..."}]` | Réécrire entièrement |

### Google Sheets

| Commande | Format | Usage |
|----------|--------|-------|
| Modifier cellules | `[MODIFY_CELLS:{"file_id":"...", "updates":[{"cell":"A1", "value":"..."}, ...]}]` | Ajouter/modifier des données |

---

## 12. Dépannage

### Erreur "Access Denied"

- Vérifiez que les APIs sont activées dans Google Cloud Console
- Vérifiez que les scopes sont correctement configurés
- L'utilisateur doit ré-autoriser si les scopes ont changé

### Erreur "Token expired"

- Le refresh_token est utilisé automatiquement
- Si échec, redirigez vers `/auth/google/login`

### Erreur "File not found"

- Vérifiez que l'utilisateur a accès au fichier
- Le file_id doit être valide

### Les modifications ne s'appliquent pas

- Vérifiez les logs pour voir la commande générée
- Testez la commande manuellement via l'API Google
- Vérifiez que le format JSON est correct

### L'IA ne génère pas les bonnes commandes

- Ajustez le prompt système
- Donnez plus d'exemples dans le prompt
- Réduisez la température du modèle (0.3-0.5)

---

## Support

Pour toute question sur l'intégration, consultez :
- [Documentation Google Drive API](https://developers.google.com/drive/api)
- [Documentation Google Docs API](https://developers.google.com/docs/api)
- [Documentation Google Sheets API](https://developers.google.com/sheets/api)
- [Documentation OpenAI](https://platform.openai.com/docs)
