# Guide d'Intégration - Agent IA Google Docs/Sheets

Ce guide explique comment intégrer l'agent IA de modification de documents Google à votre plateforme existante.

---

## Table des matières

1. [Vue d'ensemble](#1-vue-densemble)
2. [Prérequis](#2-prérequis)
3. [Configuration Google Cloud](#3-configuration-google-cloud)
4. [Code d'authentification OAuth](#4-code-dauthentification-oauth)
5. [Service Google Drive](#5-service-google-drive)
6. [Exécution des commandes IA](#6-exécution-des-commandes-ia)
7. [Prompts système](#7-prompts-système)
8. [Endpoint Chat (texte)](#8-endpoint-chat-texte)
9. [Endpoint Voice (push-to-talk)](#9-endpoint-voice-push-to-talk)
10. [Interface Frontend](#10-interface-frontend)
11. [Variables d'environnement](#11-variables-denvironnement)

---

## 1. Vue d'ensemble

### Fonctionnalités

- **Sélection de document** : L'utilisateur choisit un document Google (Docs, Sheets, Slides)
- **Chat texte** : Modification du document via messages texte
- **Conversation vocale** : Mode push-to-talk avec transcription Whisper et réponse TTS
- **Modifications précises** : Remplacement de texte, insertion, suppression, modification de cellules

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        VOTRE PLATEFORME                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────┐     ┌─────────────────┐     ┌──────────────┐  │
│  │  Frontend   │────▶│  /chat-edit     │────▶│   OpenAI     │  │
│  │  (Texte)    │     │  /voice-chat    │     │   GPT-4      │  │
│  └─────────────┘     └────────┬────────┘     └──────────────┘  │
│                               │                                 │
│                               ▼                                 │
│                    ┌─────────────────────┐                     │
│                    │  execute_modif()    │                     │
│                    │  Parse les commands │                     │
│                    └──────────┬──────────┘                     │
│                               │                                 │
│                               ▼                                 │
│                    ┌─────────────────────┐                     │
│                    │   DriveService      │                     │
│                    │   (Google APIs)     │                     │
│                    └──────────┬──────────┘                     │
│                               │                                 │
└───────────────────────────────┼─────────────────────────────────┘
                                ▼
                     ┌─────────────────────┐
                     │    Google APIs      │
                     │  Drive/Docs/Sheets  │
                     └─────────────────────┘
```

---

## 2. Prérequis

- Python 3.8+
- Compte Google Cloud Platform
- Clé API OpenAI
- Framework web (Flask, FastAPI, etc.)

### Dépendances Python

```bash
pip install google-auth google-auth-oauthlib google-api-python-client openai flask python-dotenv
```

---

## 3. Configuration Google Cloud

### 3.1 Activer les APIs

Dans Google Cloud Console, activez :
- Google Drive API
- Google Docs API
- Google Sheets API
- Google Slides API

### 3.2 Créer les identifiants OAuth

1. **APIs & Services > Credentials > Create Credentials > OAuth client ID**
2. Type : **Web application**
3. Redirect URIs :
   - `http://localhost:5000/oauth2callback` (dev)
   - `https://votre-domaine.com/oauth2callback` (prod)

### 3.3 Configurer l'écran de consentement

Ajoutez les scopes :
```
https://www.googleapis.com/auth/drive
https://www.googleapis.com/auth/documents
https://www.googleapis.com/auth/spreadsheets
https://www.googleapis.com/auth/presentations
```

---

## 4. Code d'authentification OAuth

```python
# google_auth.py

import os
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request

SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/presentations',
]

def get_google_client_config():
    return {
        "web": {
            "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
            "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [os.environ.get("BASE_URL") + "/oauth2callback"],
        }
    }

def create_oauth_flow():
    return Flow.from_client_config(
        get_google_client_config(),
        scopes=SCOPES,
        redirect_uri=os.environ.get("BASE_URL") + "/oauth2callback"
    )

def get_authorization_url():
    """Génère l'URL de connexion Google"""
    flow = create_oauth_flow()
    url, state = flow.authorization_url(access_type='offline', prompt='consent')
    return url, state

def exchange_code_for_credentials(authorization_response, state):
    """Échange le code contre des credentials"""
    flow = create_oauth_flow()
    flow.state = state
    flow.fetch_token(authorization_response=authorization_response)
    return flow.credentials

def credentials_to_dict(credentials):
    """Convertit credentials en dict pour stockage"""
    return {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': list(credentials.scopes) if credentials.scopes else []
    }

def dict_to_credentials(creds_dict):
    """Convertit dict en credentials"""
    return Credentials(
        token=creds_dict['token'],
        refresh_token=creds_dict.get('refresh_token'),
        token_uri=creds_dict['token_uri'],
        client_id=creds_dict['client_id'],
        client_secret=creds_dict['client_secret'],
        scopes=creds_dict.get('scopes', [])
    )

def refresh_if_needed(creds_dict):
    """Rafraîchit les credentials si expirés"""
    credentials = dict_to_credentials(creds_dict)
    if not credentials.valid:
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            return credentials, credentials_to_dict(credentials)
        return None, None
    return credentials, creds_dict
```

### Routes OAuth

```python
from flask import redirect, request, session

@app.route('/auth/google/login')
def google_login():
    url, state = get_authorization_url()
    session['oauth_state'] = state
    return redirect(url)

@app.route('/oauth2callback')
def oauth2callback():
    credentials = exchange_code_for_credentials(request.url, session['oauth_state'])
    session['google_credentials'] = credentials_to_dict(credentials)
    return redirect('/')

@app.route('/auth/google/logout')
def google_logout():
    session.clear()
    return redirect('/')
```

---

## 5. Service Google Drive

```python
# drive_service.py

from googleapiclient.discovery import build

class DriveService:
    def __init__(self, credentials):
        self.drive = build('drive', 'v3', credentials=credentials)
        self.docs = build('docs', 'v1', credentials=credentials)
        self.sheets = build('sheets', 'v4', credentials=credentials)
        self.slides = build('slides', 'v1', credentials=credentials)

    # ===== LECTURE =====

    def list_editable_documents(self):
        """Liste les documents éditables (Docs, Sheets, Slides)"""
        query = "(mimeType='application/vnd.google-apps.document' or " \
                "mimeType='application/vnd.google-apps.spreadsheet' or " \
                "mimeType='application/vnd.google-apps.presentation') and trashed=false"
        results = self.drive.files().list(q=query, pageSize=100,
                                          fields="files(id,name,mimeType)").execute()
        return results.get('files', [])

    def get_document_content(self, file_id):
        """Lit le contenu d'un Google Doc"""
        doc = self.docs.documents().get(documentId=file_id).execute()
        text_parts = []
        for elem in doc.get('body', {}).get('content', []):
            if 'paragraph' in elem:
                for e in elem['paragraph'].get('elements', []):
                    if 'textRun' in e:
                        text_parts.append(e['textRun'].get('content', ''))
        return ''.join(text_parts)

    def get_sheet_content(self, file_id, range_name='A1:Z1000'):
        """Lit le contenu d'un Google Sheet"""
        result = self.sheets.spreadsheets().values().get(
            spreadsheetId=file_id, range=range_name).execute()
        return result

    # ===== MODIFICATION GOOGLE DOCS =====

    def replace_text_in_doc(self, file_id, find_text, replace_text):
        """Chercher/Remplacer du texte"""
        requests = [{'replaceAllText': {
            'containsText': {'text': find_text, 'matchCase': False},
            'replaceText': replace_text
        }}]
        result = self.docs.documents().batchUpdate(
            documentId=file_id, body={'requests': requests}).execute()
        occurrences = result.get('replies', [{}])[0].get('replaceAllText', {}).get('occurrencesChanged', 0)
        return {'status': 'success', 'message': f'{occurrences} occurrence(s) remplacée(s)'}

    def insert_text_after(self, file_id, after_text, new_text):
        """Insérer du texte après un texte existant"""
        doc = self.docs.documents().get(documentId=file_id).execute()
        full_text = self.get_document_content(file_id)
        position = full_text.find(after_text)
        if position == -1:
            return {'status': 'error', 'message': f'Texte "{after_text}" non trouvé'}
        insert_index = position + len(after_text) + 1
        requests = [{'insertText': {'location': {'index': insert_index}, 'text': new_text}}]
        self.docs.documents().batchUpdate(documentId=file_id, body={'requests': requests}).execute()
        return {'status': 'success', 'message': 'Texte inséré'}

    def delete_text_in_doc(self, file_id, text_to_delete):
        """Supprimer un texte"""
        return self.replace_text_in_doc(file_id, text_to_delete, '')

    def append_to_doc(self, file_id, content):
        """Ajouter à la fin du document"""
        doc = self.docs.documents().get(documentId=file_id).execute()
        end_index = max([e.get('endIndex', 1) for e in doc.get('body', {}).get('content', [])], default=1)
        requests = [{'insertText': {'location': {'index': max(1, end_index - 1)}, 'text': '\n' + content}}]
        self.docs.documents().batchUpdate(documentId=file_id, body={'requests': requests}).execute()
        return {'status': 'success', 'message': 'Contenu ajouté'}

    def replace_doc_content(self, file_id, new_content):
        """Remplacer tout le contenu"""
        doc = self.docs.documents().get(documentId=file_id).execute()
        end_index = max([e.get('endIndex', 1) for e in doc.get('body', {}).get('content', [])], default=1)
        requests = []
        if end_index > 2:
            requests.append({'deleteContentRange': {'range': {'startIndex': 1, 'endIndex': end_index - 1}}})
        if new_content:
            requests.append({'insertText': {'location': {'index': 1}, 'text': new_content}})
        if requests:
            self.docs.documents().batchUpdate(documentId=file_id, body={'requests': requests}).execute()
        return {'status': 'success', 'message': 'Document remplacé'}

    # ===== MODIFICATION GOOGLE SHEETS =====

    def update_sheet_cells(self, file_id, updates):
        """Modifier des cellules: [{"cell": "A1", "value": "xxx"}, ...]"""
        data = [{'range': u['cell'], 'values': [[u['value']]]} for u in updates if u.get('cell')]
        if not data:
            return {'status': 'error', 'message': 'Aucune mise à jour'}
        result = self.sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=file_id,
            body={'valueInputOption': 'USER_ENTERED', 'data': data}
        ).execute()
        return {'status': 'success', 'message': f'{result.get("totalUpdatedCells", 0)} cellule(s) mise(s) à jour'}
```

---

## 6. Exécution des commandes IA

```python
# command_executor.py

import json

def extract_json_from_command(answer, marker):
    """Extrait le JSON d'une commande avec gestion des accolades imbriquées"""
    start = answer.find(marker)
    if start == -1:
        return None

    json_start = start + len(marker)
    brace_count = 0
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
                return json.loads(answer[json_start:i+1])
    return None

def remove_command_from_answer(answer, marker):
    """Retire la commande pour affichage utilisateur"""
    start = answer.find(marker)
    if start == -1:
        return answer

    brace_count = 0
    in_string = False
    end = start

    for i, char in enumerate(answer[start:], start=start):
        if char == '"' and (i == 0 or answer[i-1] != '\\'):
            in_string = not in_string
        if not in_string:
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
            elif char == ']' and brace_count == 0:
                end = i + 1
                break

    return (answer[:start] + answer[end:]).strip()

def execute_modification(answer, drive_service):
    """Parse et exécute les commandes de l'IA"""
    result = None

    if '[MODIFY_CELLS:' in answer:
        cmd = extract_json_from_command(answer, '[MODIFY_CELLS:')
        if cmd:
            result = drive_service.update_sheet_cells(cmd['file_id'], cmd['updates'])
        answer = remove_command_from_answer(answer, '[MODIFY_CELLS:')

    elif '[REPLACE_TEXT:' in answer:
        cmd = extract_json_from_command(answer, '[REPLACE_TEXT:')
        if cmd:
            result = drive_service.replace_text_in_doc(cmd['file_id'], cmd['find'], cmd['replace'])
        answer = remove_command_from_answer(answer, '[REPLACE_TEXT:')

    elif '[INSERT_AFTER:' in answer:
        cmd = extract_json_from_command(answer, '[INSERT_AFTER:')
        if cmd:
            result = drive_service.insert_text_after(cmd['file_id'], cmd['after'], cmd['content'])
        answer = remove_command_from_answer(answer, '[INSERT_AFTER:')

    elif '[DELETE_TEXT:' in answer:
        cmd = extract_json_from_command(answer, '[DELETE_TEXT:')
        if cmd:
            result = drive_service.delete_text_in_doc(cmd['file_id'], cmd['text'])
        answer = remove_command_from_answer(answer, '[DELETE_TEXT:')

    elif '[MODIFY_DOC:' in answer:
        cmd = extract_json_from_command(answer, '[MODIFY_DOC:')
        if cmd:
            if cmd.get('action') == 'append':
                result = drive_service.append_to_doc(cmd['file_id'], cmd['content'])
            elif cmd.get('action') == 'replace':
                result = drive_service.replace_doc_content(cmd['file_id'], cmd['content'])
        answer = remove_command_from_answer(answer, '[MODIFY_DOC:')

    return result, answer
```

---

## 7. Prompts système

### Règles strictes (appliquées à tous les prompts)

```python
STRICT_RULES = """
===== REGLES ABSOLUES - JAMAIS D'EXCEPTION =====

**REGLE 1 - TOUJOURS EXECUTER:**
Quand l'utilisateur te demande de faire quelque chose, tu DOIS OBLIGATOIREMENT inclure la commande dans ta réponse.
- Pas d'excuse, pas de "je ne peux pas", pas de "je vais essayer"
- Si on te demande d'ajouter/modifier/supprimer → TU LE FAIS
- La SEULE exception: si l'utilisateur dit "non", "laisse tomber", "annule"

**REGLE 2 - JAMAIS MENTIR:**
Tu ne dois JAMAIS dire "c'est fait" ou "j'ai modifié" si ta réponse NE CONTIENT PAS de commande.
- Si ta réponse ne contient pas de commande → tu n'as RIEN fait
- INTERDIT de confirmer une action sans avoir inclus la commande correspondante

**REGLE 3 - STRUCTURE DE REPONSE:**
Quand on te demande une tâche:
1. D'abord la commande: [MODIFY_CELLS:...] ou [REPLACE_TEXT:...] etc.
2. Ensuite une confirmation courte: "C'est fait" / "Voilà" / "OK"

MAUVAIS (INTERDIT):
User: "Ajoute lundi avec 12 utilisateurs"
Assistant: "C'est fait, j'ai ajouté la ligne."
→ INTERDIT car il n'y a pas de commande!

BON:
User: "Ajoute lundi avec 12 utilisateurs"
Assistant: "[MODIFY_CELLS:{...}] C'est fait."
→ CORRECT car la commande est présente

**REGLE 4 - EN CAS DE DOUTE:**
Si tu ne comprends pas exactement:
- Pose une question pour clarifier
- NE FAIS PAS de modification si tu n'es pas sûr
- NE DIS PAS "c'est fait" si tu n'as pas compris
"""
```

### Prompt Google Sheets

```python
def build_sheets_prompt(document, values):
    sheet_display = ""
    for row_idx, row in enumerate(values[:50], start=1):
        row_str = f"Ligne {row_idx}: "
        for col_idx, cell in enumerate(row):
            col_letter = chr(65 + col_idx) if col_idx < 26 else f"A{chr(65 + col_idx - 26)}"
            row_str += f"[{col_letter}{row_idx}={cell}] "
        sheet_display += row_str.strip() + "\n"

    return f"""
***** REGLE FONDAMENTALE *****
CHAQUE DONNEE = UNE CELLULE SEPAREE. JAMAIS plusieurs informations dans une seule cellule!
*****************************

DONNEES DE LA FEUILLE:
```
{sheet_display}
```

COMMANDE:
[MODIFY_CELLS:{{"file_id":"{document['id']}", "updates":[{{"cell":"A1", "value":"xxx"}}]}}]

{STRICT_RULES}
"""
```

### Prompt Google Docs

```python
def build_docs_prompt(document, doc_content):
    if len(doc_content) > 2000:
        doc_content = doc_content[:2000] + "\n... (tronqué)"

    return f"""
CONTENU ACTUEL:
---
{doc_content}
---

COMMANDES:
- Remplacer: [REPLACE_TEXT:{{"file_id":"{document['id']}", "find":"...", "replace":"..."}}]
- Insérer après: [INSERT_AFTER:{{"file_id":"{document['id']}", "after":"...", "content":"..."}}]
- Supprimer: [DELETE_TEXT:{{"file_id":"{document['id']}", "text":"..."}}]
- Ajouter à la fin: [MODIFY_DOC:{{"file_id":"{document['id']}", "action":"append", "content":"..."}}]

{STRICT_RULES}
"""
```

---

## 8. Endpoint Chat (texte)

```python
from openai import OpenAI

@app.route('/chat-edit', methods=['POST'])
def chat_edit():
    data = request.json
    message = data['message']
    document = data['document']  # {id, name, mimeType}
    history = data.get('history', [])

    # Récupérer credentials
    credentials, _ = refresh_if_needed(session['google_credentials'])
    drive_service = DriveService(credentials)

    # Construire le prompt selon le type
    mime_type = document.get('mimeType', '')
    if 'spreadsheet' in mime_type:
        content = drive_service.get_sheet_content(document['id'])
        system_prompt = build_sheets_prompt(document, content.get('values', []))
    else:
        content = drive_service.get_document_content(document['id'])
        system_prompt = build_docs_prompt(document, content)

    # Appel OpenAI
    client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history[-6:])
    messages.append({"role": "user", "content": message})

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.7
    )

    answer = response.choices[0].message.content

    # Exécuter les modifications
    result, clean_answer = execute_modification(answer, drive_service)

    return jsonify({
        'answer': clean_answer,
        'modification_result': result
    })
```

---

## 9. Endpoint Voice (push-to-talk)

### Backend

```python
@app.route('/voice-chat', methods=['POST'])
def voice_chat():
    """
    Reçoit: audio (fichier WebM) + document + history
    Retourne: transcription + réponse + audio (base64 MP3)
    """
    import tempfile
    import base64

    audio_file = request.files['audio']
    document = json.loads(request.form.get('document', '{}'))
    history = json.loads(request.form.get('history', '[]'))

    credentials, _ = refresh_if_needed(session['google_credentials'])
    client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

    # 1. SPEECH-TO-TEXT avec Whisper
    with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as temp:
        audio_file.save(temp.name)
        with open(temp.name, 'rb') as f:
            transcription = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="fr"
            )
        os.unlink(temp.name)

    user_message = transcription.text

    # 2. Traitement IA
    drive_service = DriveService(credentials)
    mime_type = document.get('mimeType', '')

    if 'spreadsheet' in mime_type:
        content = drive_service.get_sheet_content(document['id'])
        system_prompt = build_sheets_prompt(document, content.get('values', []))
    else:
        content = drive_service.get_document_content(document['id'])
        system_prompt = build_docs_prompt(document, content)

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history[-10:])
    messages.append({"role": "user", "content": user_message})

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.7
    )

    ai_answer = response.choices[0].message.content
    result, clean_answer = execute_modification(ai_answer, drive_service)

    # 3. TEXT-TO-SPEECH avec voix alloy
    tts_response = client.audio.speech.create(
        model="tts-1",
        voice="alloy",
        input=clean_answer,
        response_format="mp3"
    )

    audio_base64 = base64.b64encode(tts_response.content).decode('utf-8')

    return jsonify({
        'transcription': user_message,
        'response': clean_answer,
        'audio': audio_base64,
        'modification_result': result
    })
```

---

## 10. Interface Frontend

### Modal Push-to-Talk

```html
<!-- Modal conversation vocale -->
<div id="voice-modal" style="display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.85); z-index: 1000;">
    <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; color: white;">

        <!-- Bouton fermer -->
        <button onclick="stopVoiceConversation()" style="position: absolute; top: 1.5rem; right: 1.5rem;">✕</button>

        <!-- Nom du document -->
        <div id="voice-doc-name"></div>

        <!-- Bouton push-to-talk -->
        <button id="push-to-talk-btn" onclick="toggleVoiceRecording()"
                style="width: 150px; height: 150px; border-radius: 50%; background: linear-gradient(135deg, #667eea, #764ba2);">
            🎤
        </button>

        <!-- Status -->
        <div id="voice-status">Appuyez pour parler</div>

        <!-- Transcription -->
        <div id="voice-transcription"></div>

        <!-- Historique -->
        <div id="voice-history"></div>
    </div>
</div>
```

### JavaScript Push-to-Talk

```javascript
let voiceConversationActive = false;
let voiceMediaRecorder = null;
let voiceAudioChunks = [];
let voiceConversationHistory = [];
let currentAudio = null;
let isRecording = false;
let isSpeaking = false;

function startVoiceConversation() {
    if (!selectedDocument) {
        alert('Sélectionnez d\'abord un document.');
        return;
    }
    document.getElementById('voice-modal').style.display = 'block';
    document.getElementById('voice-doc-name').textContent = 'Document: ' + selectedDocument.name;
    voiceConversationHistory = [];
    voiceConversationActive = true;
    updateVoiceUI('ready');
}

function stopVoiceConversation() {
    voiceConversationActive = false;
    if (currentAudio) currentAudio.pause();
    if (voiceMediaRecorder?.state === 'recording') voiceMediaRecorder.stop();
    document.getElementById('voice-modal').style.display = 'none';
}

function toggleVoiceRecording() {
    // Si l'agent parle, l'interrompre
    if (isSpeaking && currentAudio) {
        currentAudio.pause();
        currentAudio = null;
        isSpeaking = false;
        startRecording();
        return;
    }

    // Toggle enregistrement
    if (isRecording) {
        stopRecording();
    } else {
        startRecording();
    }
}

async function startRecording() {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    voiceMediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
    voiceAudioChunks = [];
    isRecording = true;
    updateVoiceUI('recording');

    voiceMediaRecorder.ondataavailable = (e) => voiceAudioChunks.push(e.data);
    voiceMediaRecorder.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        const blob = new Blob(voiceAudioChunks, { type: 'audio/webm' });
        if (blob.size > 1000) await processVoiceInput(blob);
        else updateVoiceUI('ready');
    };

    voiceMediaRecorder.start();
}

function stopRecording() {
    isRecording = false;
    voiceMediaRecorder?.stop();
}

function updateVoiceUI(state) {
    const btn = document.getElementById('push-to-talk-btn');
    const status = document.getElementById('voice-status');

    const states = {
        ready: { bg: '#667eea', text: 'Appuyez pour parler' },
        recording: { bg: '#ef4444', text: 'Enregistrement... Appuyez pour envoyer' },
        processing: { bg: '#f59e0b', text: 'Traitement...' },
        speaking: { bg: '#10b981', text: 'L\'agent parle... Appuyez pour interrompre' }
    };

    btn.style.background = states[state].bg;
    status.textContent = states[state].text;
}

async function processVoiceInput(audioBlob) {
    updateVoiceUI('processing');

    const formData = new FormData();
    formData.append('audio', audioBlob, 'recording.webm');
    formData.append('document', JSON.stringify(selectedDocument));
    formData.append('history', JSON.stringify(voiceConversationHistory));

    const response = await fetch('/voice-chat', { method: 'POST', body: formData });
    const data = await response.json();

    document.getElementById('voice-transcription').textContent = 'Vous: "' + data.transcription + '"';

    voiceConversationHistory.push({ role: 'user', content: data.transcription });
    voiceConversationHistory.push({ role: 'assistant', content: data.response });

    // Jouer la réponse audio
    if (data.audio) {
        isSpeaking = true;
        updateVoiceUI('speaking');
        currentAudio = new Audio('data:audio/mp3;base64,' + data.audio);
        currentAudio.onended = () => {
            isSpeaking = false;
            updateVoiceUI('ready');
        };
        currentAudio.play();
    } else {
        updateVoiceUI('ready');
    }
}
```

### États du bouton

| État | Couleur | Action au clic |
|------|---------|----------------|
| **ready** | 🟣 Violet | Démarrer l'enregistrement |
| **recording** | 🔴 Rouge | Arrêter et envoyer |
| **processing** | 🟠 Orange | Attendre |
| **speaking** | 🟢 Vert | Interrompre l'agent |

---

## 11. Variables d'environnement

```bash
# .env

# Google OAuth
GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=xxx

# URLs
BASE_URL=http://localhost:5000

# OpenAI
OPENAI_API_KEY=sk-xxx

# Flask
FLASK_SECRET_KEY=une-cle-secrete-aleatoire
```

---

## Résumé des commandes IA

### Google Sheets
```
[MODIFY_CELLS:{"file_id":"xxx", "updates":[{"cell":"A1", "value":"xxx"}]}]
```

### Google Docs
```
[REPLACE_TEXT:{"file_id":"xxx", "find":"...", "replace":"..."}]
[INSERT_AFTER:{"file_id":"xxx", "after":"...", "content":"..."}]
[DELETE_TEXT:{"file_id":"xxx", "text":"..."}]
[MODIFY_DOC:{"file_id":"xxx", "action":"append", "content":"..."}]
[MODIFY_DOC:{"file_id":"xxx", "action":"replace", "content":"..."}]
```

---

## Support

- [Google Drive API](https://developers.google.com/drive/api)
- [Google Docs API](https://developers.google.com/docs/api)
- [Google Sheets API](https://developers.google.com/sheets/api)
- [OpenAI API](https://platform.openai.com/docs)
