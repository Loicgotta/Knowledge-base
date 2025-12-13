# Guide d'Intégration - Agent IA Google Drive

Un agent conversationnel qui permet de modifier des documents Google (Docs, Sheets) via texte ou voix.

---

## Comment ça marche ?

```
1. L'utilisateur se connecte avec Google
2. Il sélectionne un document (Doc ou Sheet)
3. Il discute avec l'agent (texte ou voix)
4. L'agent modifie le document en temps réel
```

### Flux simplifié

```
Utilisateur ──▶ "Ajoute lundi avec 10 ventes"
                        │
                        ▼
                  OpenAI GPT-4
                        │
                        ▼
            [MODIFY_CELLS:{"cell":"A5", "value":"lundi"}...]
                        │
                        ▼
                Google Sheets API
                        │
                        ▼
              Document mis à jour ✓
```

---

## Ce dont vous avez besoin

| Service | Pourquoi |
|---------|----------|
| **Google Cloud** | OAuth + APIs Drive/Docs/Sheets |
| **OpenAI** | GPT-4 (chat) + Whisper (voix→texte) + TTS (texte→voix) |
| **Python 3.8+** | Backend |

```bash
pip install google-auth google-auth-oauthlib google-api-python-client openai flask
```

---

## Configuration Google Cloud (5 minutes)

### 1. Activer les APIs
Dans [Google Cloud Console](https://console.cloud.google.com) → APIs & Services → Library :
- Google Drive API
- Google Docs API
- Google Sheets API

### 2. Créer les identifiants OAuth
APIs & Services → Credentials → Create → OAuth client ID → **Web application**

Redirect URI : `http://localhost:5000/oauth2callback`

### 3. Variables d'environnement
```bash
GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=xxx
OPENAI_API_KEY=sk-xxx
BASE_URL=http://localhost:5000
```

---

## Les 4 composants à intégrer

### 1. Authentification OAuth

**But** : Obtenir l'accès aux documents Google de l'utilisateur.

```python
from google_auth_oauthlib.flow import Flow

SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/spreadsheets',
]

# Démarrer la connexion
flow = Flow.from_client_config(config, scopes=SCOPES)
auth_url, state = flow.authorization_url()
# → Rediriger l'utilisateur vers auth_url

# Après retour de Google
flow.fetch_token(authorization_response=callback_url)
credentials = flow.credentials  # ← À sauvegarder en session
```

---

### 2. DriveService (lecture/écriture)

**But** : Lire et modifier les documents Google.

```python
from googleapiclient.discovery import build

class DriveService:
    def __init__(self, credentials):
        self.docs = build('docs', 'v1', credentials=credentials)
        self.sheets = build('sheets', 'v4', credentials=credentials)

    # LECTURE
    def get_doc_content(self, file_id):
        """Retourne le texte d'un Google Doc"""
        doc = self.docs.documents().get(documentId=file_id).execute()
        # ... extraire le texte

    def get_sheet_content(self, file_id):
        """Retourne les cellules d'un Sheet"""
        return self.sheets.spreadsheets().values().get(
            spreadsheetId=file_id, range='A1:Z100'
        ).execute()

    # ÉCRITURE
    def update_cells(self, file_id, updates):
        """updates = [{"cell": "A1", "value": "xxx"}, ...]"""
        data = [{'range': u['cell'], 'values': [[u['value']]]} for u in updates]
        self.sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=file_id,
            body={'valueInputOption': 'USER_ENTERED', 'data': data}
        ).execute()

    def replace_text(self, file_id, find, replace):
        """Chercher/remplacer dans un Doc"""
        self.docs.documents().batchUpdate(
            documentId=file_id,
            body={'requests': [{'replaceAllText': {
                'containsText': {'text': find},
                'replaceText': replace
            }}]}
        ).execute()
```

---

### 3. Exécuteur de commandes

**But** : L'IA génère des commandes structurées, ce module les exécute.

L'IA répond avec des commandes comme :
```
[MODIFY_CELLS:{"file_id":"xxx", "updates":[{"cell":"A1", "value":"Lundi"}]}]
C'est fait !
```

Le parseur extrait et exécute la commande :

```python
import json

def execute_command(ai_response, drive_service):
    """
    1. Trouve la commande dans la réponse
    2. L'exécute via DriveService
    3. Retourne la réponse nettoyée (sans la commande)
    """

    if '[MODIFY_CELLS:' in ai_response:
        # Extraire le JSON
        start = ai_response.find('[MODIFY_CELLS:') + len('[MODIFY_CELLS:')
        json_data = extract_json(ai_response, start)

        # Exécuter
        drive_service.update_cells(json_data['file_id'], json_data['updates'])

        # Nettoyer la réponse
        clean_response = remove_command(ai_response, '[MODIFY_CELLS:')
        return clean_response

    # Autres commandes : REPLACE_TEXT, INSERT_AFTER, DELETE_TEXT...
    return ai_response
```

**Commandes disponibles :**

| Type | Commande | Usage |
|------|----------|-------|
| **Sheets** | `[MODIFY_CELLS:{...}]` | Modifier des cellules |
| **Docs** | `[REPLACE_TEXT:{...}]` | Chercher/remplacer |
| **Docs** | `[INSERT_AFTER:{...}]` | Insérer après un texte |
| **Docs** | `[DELETE_TEXT:{...}]` | Supprimer du texte |

---

### 4. Prompt système (le plus important)

**But** : Donner les instructions à l'IA pour qu'elle génère les bonnes commandes.

```python
system_prompt = f"""
Tu modifies le document "{document_name}".

CONTENU ACTUEL:
{document_content}

COMMANDE À UTILISER:
[MODIFY_CELLS:{{"file_id":"{file_id}", "updates":[{{"cell":"A1", "value":"xxx"}}]}}]

=== RÈGLES STRICTES ===

1. TOUJOURS EXÉCUTER : Si on te demande de modifier → tu inclus la commande
2. JAMAIS MENTIR : Ne dis pas "c'est fait" sans avoir mis la commande
3. STRUCTURE : Commande d'abord, confirmation ensuite

EXEMPLE CORRECT:
User: "Ajoute lundi avec 10 ventes"
Assistant: [MODIFY_CELLS:{{"file_id":"xxx", "updates":[{{"cell":"A5", "value":"lundi"}}, {{"cell":"B5", "value":"10"}}]}}]
C'est fait !

EXEMPLE INTERDIT:
User: "Ajoute lundi"
Assistant: "C'est fait, j'ai ajouté lundi."  ← PAS DE COMMANDE = MENSONGE
"""
```

---

## Endpoints

### Chat (texte)

```python
@app.route('/chat-edit', methods=['POST'])
def chat_edit():
    message = request.json['message']
    document = request.json['document']  # {id, name, mimeType}

    # 1. Lire le document
    content = drive_service.get_sheet_content(document['id'])

    # 2. Construire le prompt
    prompt = build_prompt(document, content)

    # 3. Appeler GPT-4
    response = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": message}
        ]
    )

    # 4. Exécuter la commande
    answer = response.choices[0].message.content
    clean_answer = execute_command(answer, drive_service)

    return {'answer': clean_answer}
```

### Voix (push-to-talk)

```python
@app.route('/voice-chat', methods=['POST'])
def voice_chat():
    audio = request.files['audio']  # WebM depuis le navigateur
    document = json.loads(request.form['document'])

    # 1. SPEECH-TO-TEXT (Whisper)
    transcription = openai.audio.transcriptions.create(
        model="whisper-1",
        file=audio,
        language="fr"
    )
    user_message = transcription.text

    # 2. TRAITEMENT IA (même logique que /chat-edit)
    answer = process_with_gpt(user_message, document)
    clean_answer = execute_command(answer, drive_service)

    # 3. TEXT-TO-SPEECH (voix alloy)
    tts = openai.audio.speech.create(
        model="tts-1",
        voice="alloy",
        input=clean_answer
    )
    audio_base64 = base64.b64encode(tts.content).decode()

    return {
        'transcription': user_message,
        'response': clean_answer,
        'audio': audio_base64
    }
```

---

## Interface vocale (push-to-talk)

Le bouton change de couleur selon l'état :

| État | Couleur | Action |
|------|---------|--------|
| Prêt | 🟣 Violet | Cliquer → commencer à parler |
| Enregistrement | 🔴 Rouge | Cliquer → envoyer |
| Traitement | 🟠 Orange | Attendre |
| Agent parle | 🟢 Vert | Cliquer → interrompre |

```javascript
function toggleVoiceRecording() {
    if (isSpeaking) {
        // Interrompre l'agent
        currentAudio.pause();
        startRecording();
    } else if (isRecording) {
        // Arrêter et envoyer
        stopRecording();
    } else {
        // Commencer à parler
        startRecording();
    }
}
```

---

## Résumé

| Fichier | Rôle |
|---------|------|
| `google_auth.py` | OAuth Google |
| `drive_service.py` | Lecture/écriture documents |
| `command_executor.py` | Parse et exécute les commandes IA |
| `prompts.py` | Instructions pour l'IA |
| `app.py` | Endpoints `/chat-edit` et `/voice-chat` |

**Flux complet :**
```
Utilisateur parle → Whisper → GPT-4 → Commande → Google API → Document modifié → TTS → Réponse vocale
```

---

## Liens utiles

- [Google Drive API](https://developers.google.com/drive/api)
- [Google Docs API](https://developers.google.com/docs/api)
- [Google Sheets API](https://developers.google.com/sheets/api)
- [OpenAI API](https://platform.openai.com/docs)
