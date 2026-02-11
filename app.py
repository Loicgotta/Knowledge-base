"""
Drive Knowledge Base - AI Agent with RAG
Main Flask application for Google Drive document Q&A
"""

import os
import sys
import secrets
import traceback
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_cors import CORS
from flask_session import Session
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import our modules
from auth import (
    get_authorization_url,
    exchange_code_for_credentials,
    credentials_to_dict,
    get_valid_credentials,
    is_authenticated,
    logout
)
from drive_service import DriveService
from rag_engine import RAGEngine

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(32))

# Configure session
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_DIR'] = '/tmp/flask_session'
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = 86400 * 7  # 7 days

# Initialize Flask-Session
Session(app)

# Enable CORS
CORS(app)

# Allow OAuth over HTTP for local development
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'


# Store RAG engines per user session
rag_engines = {}


def get_rag_engine():
    """Get or create RAG engine for current user"""
    if 'user_id' not in session:
        session['user_id'] = secrets.token_hex(16)

    user_id = session['user_id']

    if user_id not in rag_engines:
        rag_engines[user_id] = RAGEngine(user_id)

    return rag_engines[user_id]


# ============== Routes ==============

@app.route('/')
def index():
    """Home page"""
    authenticated = is_authenticated()
    stats = None
    selected_folder = None

    if authenticated:
        try:
            rag = get_rag_engine()
            stats = rag.get_stats()
            if session.get('selected_folder_id'):
                selected_folder = {
                    'id': session.get('selected_folder_id'),
                    'name': session.get('selected_folder_name', 'Selected Folder')
                }
        except:
            stats = {'status': 'error'}

    return render_template('index.html',
                         authenticated=authenticated,
                         stats=stats,
                         selected_folder=selected_folder,
                         google_client_id=os.environ.get('GOOGLE_CLIENT_ID'))


@app.route('/login')
def login():
    """Initiate Google OAuth login"""
    authorization_url, state = get_authorization_url()
    session['state'] = state
    return redirect(authorization_url)


@app.route('/oauth2callback')
def oauth2callback():
    """Handle OAuth callback from Google"""
    if 'error' in request.args:
        return f"Error: {request.args['error']}", 400

    state = session.get('state')
    if not state:
        return redirect(url_for('index'))

    try:
        credentials = exchange_code_for_credentials(
            request.url,
            state
        )
        session['credentials'] = credentials_to_dict(credentials)
        return redirect(url_for('index'))
    except Exception as e:
        print(f"OAuth error: {e}")
        return f"Authentication error: {str(e)}", 400


@app.route('/logout')
def logout_route():
    """Logout and clear session"""
    user_id = session.get('user_id')
    if user_id and user_id in rag_engines:
        del rag_engines[user_id]
    logout()
    return redirect(url_for('index'))


@app.route('/folders')
def list_folders():
    """List all folders from Google Drive"""
    if not is_authenticated():
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        credentials = get_valid_credentials()
        if not credentials:
            return jsonify({'error': 'Invalid credentials'}), 401

        drive_service = DriveService(credentials)
        folders = drive_service.list_folders()

        return jsonify({
            'status': 'success',
            'folders': folders
        })

    except Exception as e:
        print(f"Error listing folders: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/select-folder', methods=['POST'])
def select_folder():
    """Select a folder to sync"""
    if not is_authenticated():
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.json
    folder_id = data.get('folder_id')
    folder_name = data.get('folder_name', 'Selected Folder')

    if not folder_id:
        return jsonify({'error': 'No folder_id provided'}), 400

    session['selected_folder_id'] = folder_id
    session['selected_folder_name'] = folder_name

    return jsonify({
        'status': 'success',
        'message': f"Folder '{folder_name}' selected",
        'folder_id': folder_id
    })


@app.route('/browse')
def browse_folder():
    """Browse folder contents (files and subfolders)"""
    if not is_authenticated():
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        credentials = get_valid_credentials()
        if not credentials:
            return jsonify({'error': 'Invalid credentials'}), 401

        folder_id = request.args.get('folder_id')  # None means root

        drive_service = DriveService(credentials)
        items = drive_service.list_folder_contents(folder_id)

        return jsonify({
            'status': 'success',
            'items': items,
            'folder_id': folder_id or 'root'
        })

    except Exception as e:
        print(f"Error browsing folder: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/add-documents', methods=['POST'])
def add_documents():
    """Add documents to existing index without clearing"""
    print("=== ADD DOCUMENTS START ===", flush=True)

    if not is_authenticated():
        print("ERROR: Not authenticated", flush=True)
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        print("Step 1: Getting credentials...", flush=True)
        credentials = get_valid_credentials()
        if not credentials:
            print("ERROR: Invalid credentials", flush=True)
            return jsonify({'error': 'Invalid credentials'}), 401

        print("Step 2: Parsing request data...", flush=True)
        data = request.json or {}
        items = data.get('items', [])  # List of {id, type, name}
        print(f"Items received: {len(items)}", flush=True)

        if not items:
            print("ERROR: No items selected", flush=True)
            return jsonify({'error': 'No items selected'}), 400

        print("Step 3: Creating DriveService...", flush=True)
        drive_service = DriveService(credentials)
        all_documents = []

        # Process each selected item
        file_ids = []
        folder_ids = []

        for item in items:
            if item.get('type') == 'folder':
                folder_ids.append(item['id'])
            else:
                file_ids.append(item['id'])

        print(f"Step 4: Processing {len(file_ids)} files and {len(folder_ids)} folders...", flush=True)

        # Get documents from individual files
        if file_ids:
            print(f"Step 4a: Fetching {len(file_ids)} individual files...", flush=True)
            docs = drive_service.get_documents_by_ids(file_ids)
            print(f"Got {len(docs)} documents from files", flush=True)
            all_documents.extend(docs)

        # Get documents from folders (recursive)
        for folder_id in folder_ids:
            print(f"Step 4b: Fetching documents from folder {folder_id}...", flush=True)
            docs = drive_service.get_all_documents(folder_id=folder_id)
            print(f"Got {len(docs)} documents from folder", flush=True)
            all_documents.extend(docs)

        print(f"Step 5: Total documents to index: {len(all_documents)}", flush=True)

        if not all_documents:
            print("WARNING: No documents found", flush=True)
            return jsonify({
                'status': 'warning',
                'message': 'No documents found in selected items',
                'documents_found': 0
            })

        # Add to existing index (don't clear)
        print("Step 6: Getting RAG engine...", flush=True)
        rag = get_rag_engine()

        print("Step 7: Adding documents to index...", flush=True)
        result = rag.add_documents(all_documents)
        print(f"Step 8: Done! Result: {result}", flush=True)

        total_chunks = result.get('total_chunks', result['chunks_indexed'])
        return jsonify({
            'status': 'success',
            'message': f"Ajoute {result['chunks_indexed']} chunks de {result['documents_processed']} document(s). Total: {total_chunks} chunks",
            'documents_processed': result['documents_processed'],
            'chunks_indexed': result['chunks_indexed'],
            'total_chunks': total_chunks
        })

    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"=== ADD DOCUMENTS ERROR ===\n{error_trace}")
        return jsonify({
            'error': str(e),
            'error_type': type(e).__name__,
            'traceback': error_trace
        }), 500


@app.route('/indexed-documents')
def get_indexed_documents():
    """Get list of indexed document names"""
    if not is_authenticated():
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        rag = get_rag_engine()
        documents = rag.get_indexed_documents()
        return jsonify({
            'status': 'success',
            'documents': documents,
            'count': len(documents)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/sync', methods=['POST'])
def sync_drive():
    """Sync documents from Google Drive"""
    if not is_authenticated():
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        credentials = get_valid_credentials()
        if not credentials:
            return jsonify({'error': 'Invalid credentials'}), 401

        # Get folder_id from request or session
        data = request.json or {}
        folder_id = data.get('folder_id') or session.get('selected_folder_id')

        if not folder_id:
            return jsonify({
                'error': 'No folder selected. Please select a folder first.',
                'needs_folder': True
            }), 400

        # Get documents from Drive
        drive_service = DriveService(credentials)
        documents = drive_service.get_all_documents(folder_id=folder_id)

        if not documents:
            return jsonify({
                'status': 'warning',
                'message': 'No documents found in the selected folder',
                'documents_found': 0
            })

        # Index documents
        rag = get_rag_engine()
        result = rag.index_documents(documents)

        folder_name = session.get('selected_folder_name', 'Selected folder')

        return jsonify({
            'status': 'success',
            'message': f"Successfully indexed {result['chunks_indexed']} chunks from {result['documents_processed']} documents in '{folder_name}'",
            'documents_processed': result['documents_processed'],
            'chunks_indexed': result['chunks_indexed']
        })

    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"Sync error: {e}\n{error_trace}")
        return jsonify({
            'error': str(e),
            'error_type': type(e).__name__,
            'traceback': error_trace
        }), 500


@app.route('/chat', methods=['POST'])
def chat():
    """Chat endpoint for Q&A with document modification capability"""
    if not is_authenticated():
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.json
    if not data or 'message' not in data:
        return jsonify({'error': 'No message provided'}), 400

    message = data['message']
    history = data.get('history', [])

    try:
        rag = get_rag_engine()
        credentials = get_valid_credentials()

        # Get list of editable documents for the agent (Docs, Sheets, Slides)
        editable_docs = []
        if credentials:
            try:
                drive_service = DriveService(credentials)
                # Query for Google Docs, Sheets, and Slides
                mime_types = [
                    "mimeType='application/vnd.google-apps.document'",
                    "mimeType='application/vnd.google-apps.spreadsheet'",
                    "mimeType='application/vnd.google-apps.presentation'"
                ]
                query = f"({' or '.join(mime_types)}) and trashed=false"
                results = drive_service.service.files().list(
                    pageSize=50,
                    fields="files(id, name, mimeType)",
                    q=query,
                    orderBy="modifiedTime desc"
                ).execute()
                editable_docs = results.get('files', [])
            except Exception as e:
                print(f"[chat] Error fetching editable docs: {e}", flush=True)

        # Check if documents are indexed
        stats = rag.get_stats()
        if stats['total_chunks'] == 0:
            return jsonify({
                'answer': "Aucun document n'est indexé. Veuillez d'abord synchroniser vos documents Google Drive en cliquant sur 'Synchroniser'.",
                'sources': [],
                'needs_sync': True
            })

        # Get answer using RAG (with editable docs info)
        result = rag.ask(message, history, editable_docs=editable_docs)

        # Check if the agent wants to modify a document
        answer = result['answer']
        modification_result = None

        if '[MODIFY_DOC:' in answer:
            modification_result = execute_agent_modification(answer, credentials)
            # Clean the answer by removing the command (handles multiline JSON)
            import re
            answer = re.sub(r'\[MODIFY_DOC:\{.*?\}\]', '', answer, flags=re.DOTALL).strip()
            if modification_result and modification_result.get('status') == 'success':
                answer += f"\n\n✅ Document modifié: **{modification_result.get('file_name', 'Document')}**"
            elif modification_result and modification_result.get('status') == 'error':
                answer += f"\n\n❌ Erreur lors de la modification: {modification_result.get('message', 'Erreur inconnue')}"

        return jsonify({
            'answer': answer,
            'sources': result['sources'],
            'chunks_used': result.get('chunks_used', 0),
            'modification': modification_result
        })

    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"Chat error: {e}\n{error_trace}")
        return jsonify({
            'error': str(e),
            'error_type': type(e).__name__,
            'traceback': error_trace
        }), 500


def execute_agent_modification(answer: str, credentials) -> dict:
    """Execute document modification requested by the agent (Docs, Sheets, Slides)"""
    import re
    import json

    try:
        # Parse the modification command: [MODIFY_DOC:{"file_id":"...", "action":"...", "content":"..."}]
        # Use greedy matching for the JSON content
        match = re.search(r'\[MODIFY_DOC:(\{.*\})\]', answer, re.DOTALL)
        if not match:
            print(f"[execute_agent_modification] No MODIFY_DOC command found", flush=True)
            return None

        command_str = match.group(1)
        print(f"[execute_agent_modification] Parsing command: {command_str[:200]}...", flush=True)
        command = json.loads(command_str)

        file_id = command.get('file_id')
        action = command.get('action', 'append')  # 'replace' or 'append'
        content = command.get('content', '')
        doc_type = command.get('type', 'doc')  # 'doc', 'sheet', or 'slides'

        if not file_id or not content:
            print(f"[execute_agent_modification] Missing file_id or content", flush=True)
            return {'status': 'error', 'message': 'Missing file_id or content'}

        print(f"[execute_agent_modification] Type: {doc_type}, Action: {action}, File: {file_id}", flush=True)

        drive_service = DriveService(credentials)

        # Get file metadata to determine type
        file_meta = drive_service.get_file_metadata(file_id)
        file_name = file_meta.get('name', 'Document')
        mime_type = file_meta.get('mimeType', '')

        # Determine document type from mimeType if not specified
        if mime_type == 'application/vnd.google-apps.spreadsheet':
            doc_type = 'sheet'
        elif mime_type == 'application/vnd.google-apps.presentation':
            doc_type = 'slides'
        else:
            doc_type = 'doc'

        print(f"[execute_agent_modification] Detected type: {doc_type}, mimeType: {mime_type}", flush=True)

        # Execute based on document type
        if doc_type == 'sheet':
            # Parse content as rows for sheets
            # Content can be: "row1col1,row1col2\nrow2col1,row2col2" or JSON array
            try:
                if content.startswith('['):
                    data = json.loads(content)
                else:
                    # Parse CSV-like content
                    data = [row.split(',') for row in content.strip().split('\n')]
            except:
                data = [[content]]  # Single cell

            if action == 'replace':
                result = drive_service.update_google_sheet(file_id, data, clear_first=True)
            else:
                result = drive_service.append_to_google_sheet(file_id, data)

        elif doc_type == 'slides':
            # For slides, add a new slide with the content
            title = command.get('title', 'Nouvelle slide')
            result = drive_service.add_slide_with_text(file_id, title, content)

        else:  # Default: Google Doc
            if action == 'replace':
                result = drive_service.update_google_doc(file_id, content)
            else:
                result = drive_service.append_to_google_doc(file_id, content)

        result['file_name'] = file_name
        result['doc_type'] = doc_type
        return result

    except json.JSONDecodeError as e:
        print(f"[execute_agent_modification] JSON parse error: {e}", flush=True)
        return {'status': 'error', 'message': f'Invalid command format: {e}'}
    except Exception as e:
        import traceback
        print(f"[execute_agent_modification] Error: {e}\n{traceback.format_exc()}", flush=True)
        return {'status': 'error', 'message': str(e)}


def remove_command_from_answer(answer: str, marker: str) -> str:
    """Remove a command block from the answer by properly matching braces"""
    start_idx = answer.find(marker)
    if start_idx == -1:
        return answer

    # Find the end of the command by counting braces
    json_start = start_idx + len(marker)
    brace_count = 0
    end_idx = json_start
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
                # Find the closing ]
                end_idx = i + 1
                if end_idx < len(answer) and answer[end_idx] == ']':
                    end_idx += 1
                break

    # Remove the command from the answer
    cleaned = answer[:start_idx] + answer[end_idx:]
    return cleaned.strip()


def execute_cells_modification(answer: str, credentials) -> dict:
    """Execute cell-specific modifications for Google Sheets"""
    import json

    try:
        # Find the start of the command
        start_marker = '[MODIFY_CELLS:'
        start_idx = answer.find(start_marker)
        if start_idx == -1:
            print(f"[execute_cells_modification] No MODIFY_CELLS command found", flush=True)
            return None

        # Find the JSON by counting braces
        json_start = start_idx + len(start_marker)
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

        command_str = answer[json_start:json_end]
        print(f"[execute_cells_modification] Extracted JSON: {command_str}", flush=True)

        command = json.loads(command_str)

        file_id = command.get('file_id')
        updates = command.get('updates', [])

        if not file_id or not updates:
            return {'status': 'error', 'message': 'Missing file_id or updates'}

        print(f"[execute_cells_modification] Updating {len(updates)} cells in {file_id}", flush=True)

        drive_service = DriveService(credentials)
        result = drive_service.update_sheet_cells(file_id, updates)

        return result

    except json.JSONDecodeError as e:
        print(f"[execute_cells_modification] JSON parse error: {e}", flush=True)
        print(f"[execute_cells_modification] Raw string: {command_str}", flush=True)
        return {'status': 'error', 'message': f'Format de commande invalide: {e}'}
    except Exception as e:
        import traceback
        print(f"[execute_cells_modification] Error: {e}\n{traceback.format_exc()}", flush=True)
        return {'status': 'error', 'message': str(e)}


def extract_json_from_command(answer: str, marker: str) -> dict:
    """Extract JSON from a command string using brace counting"""
    import json

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

    command_str = answer[json_start:json_end]
    return json.loads(command_str)


def execute_replace_text(answer: str, credentials) -> dict:
    """Execute text replacement in a Google Doc"""
    try:
        print(f"[execute_replace_text] Starting... Looking for [REPLACE_TEXT: in answer", flush=True)
        command = extract_json_from_command(answer, '[REPLACE_TEXT:')
        if not command:
            print(f"[execute_replace_text] No command found in answer", flush=True)
            return None

        print(f"[execute_replace_text] Command parsed: {command}", flush=True)
        file_id = command.get('file_id')
        find_text = command.get('find')
        replace_text = command.get('replace')

        if not file_id or not find_text:
            print(f"[execute_replace_text] Missing params: file_id={file_id}, find_text={find_text}", flush=True)
            return {'status': 'error', 'message': 'Missing file_id or find text'}

        print(f"[execute_replace_text] Replacing '{find_text}' with '{replace_text}' in {file_id}", flush=True)

        drive_service = DriveService(credentials)
        result = drive_service.replace_text_in_doc(file_id, find_text, replace_text)

        return result

    except Exception as e:
        import traceback
        print(f"[execute_replace_text] Error: {e}\n{traceback.format_exc()}", flush=True)
        return {'status': 'error', 'message': str(e)}


def execute_insert_after(answer: str, credentials) -> dict:
    """Execute text insertion after specific text in a Google Doc"""
    try:
        command = extract_json_from_command(answer, '[INSERT_AFTER:')
        if not command:
            return None

        file_id = command.get('file_id')
        after_text = command.get('after')
        content = command.get('content')

        if not file_id or not after_text or not content:
            return {'status': 'error', 'message': 'Missing file_id, after text, or content'}

        print(f"[execute_insert_after] Inserting after '{after_text}' in {file_id}", flush=True)

        drive_service = DriveService(credentials)
        result = drive_service.insert_text_after(file_id, after_text, content)

        return result

    except Exception as e:
        import traceback
        print(f"[execute_insert_after] Error: {e}\n{traceback.format_exc()}", flush=True)
        return {'status': 'error', 'message': str(e)}


def execute_delete_text(answer: str, credentials) -> dict:
    """Execute text deletion in a Google Doc"""
    try:
        command = extract_json_from_command(answer, '[DELETE_TEXT:')
        if not command:
            return None

        file_id = command.get('file_id')
        text_to_delete = command.get('text')

        if not file_id or not text_to_delete:
            return {'status': 'error', 'message': 'Missing file_id or text to delete'}

        print(f"[execute_delete_text] Deleting '{text_to_delete}' from {file_id}", flush=True)

        drive_service = DriveService(credentials)
        result = drive_service.delete_text_in_doc(file_id, text_to_delete)

        return result

    except Exception as e:
        import traceback
        print(f"[execute_delete_text] Error: {e}\n{traceback.format_exc()}", flush=True)
        return {'status': 'error', 'message': str(e)}


def handle_creation_mode(message, history, drive_service):
    """Handle document creation requests when no document is selected"""
    from openai import OpenAI
    openai_client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

    # Récupérer la liste des dossiers pour permettre de déplacer des fichiers
    folders = drive_service.list_folders_flat()
    folders_list = "\n".join([f"- {f['name']} (ID: {f['id']})" for f in folders[:30]])  # Limiter à 30

    system_prompt = f"""Tu es Friday, un assistant IA expert, autonome et proactif qui aide a gerer des documents Google Drive.

===== CAPACITES =====

1. **CREER des documents:**
   - Google Docs (texte, rapports, notes)
   - Google Sheets (tableaux, donnees, calculs)
   - Google Slides (presentations)

2. **DEPLACER des fichiers** dans Google Drive

===== COMMANDES DISPONIBLES =====

CREER UN DOCUMENT TEXTE:
[CREATE_DOC:{{"title":"Titre", "content":"Contenu initial optionnel"}}]

CREER UN TABLEAU:
[CREATE_SHEET:{{"title":"Titre", "headers":["Col1", "Col2", "Col3"]}}]

CREER UNE PRESENTATION:
[CREATE_SLIDES:{{"title":"Titre"}}]

DEPLACER UN FICHIER:
[MOVE_FILE:{{"file_id":"ID_DU_FICHIER", "destination_folder_id":"ID_DU_DOSSIER"}}]

===== DOSSIERS DISPONIBLES =====
{folders_list if folders_list else "Aucun dossier trouve"}

===== INTELLIGENCE ET DEDUCTION =====

Tu DOIS comprendre l'intention de l'utilisateur meme si elle n'est pas explicite:

- "Fais moi un truc pour suivre mes depenses" → Tu crees un Sheet avec les bonnes colonnes
- "J'ai besoin de presenter mon projet" → Tu crees une presentation
- "Mets ca dans mes documents RH" → Tu deplace vers le dossier RH

Tu choisis TOUJOURS le type de fichier le plus adapte:
- Texte narratif, rapport, lettre → Doc
- Donnees structurees, suivi, budget → Sheet
- Presentation visuelle → Slides

===== REGLES ABSOLUES =====

1. TOUJOURS inclure la commande dans ta reponse quand une action est demandee
2. JAMAIS dire "c'est fait" sans avoir inclus la commande
3. Propose des titres et structures intelligentes basees sur le contexte
4. Reponses courtes et naturelles (pour la synthese vocale)
5. Propose systematiquement la suite logique

===== MEMOIRE CONTEXTUELLE =====
Tu te souviens de TOUT ce qui a ete dit dans la conversation.
Utilise ce contexte pour comprendre les references implicites."""

    messages = [{"role": "system", "content": system_prompt}]
    # Augmentation de la mémoire: 20 messages au lieu de 6
    for msg in history[-20:]:
        messages.append(msg)
    messages.append({"role": "user", "content": message})

    response = openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=messages,
        temperature=0.7,
        max_tokens=1500
    )

    answer = response.choices[0].message.content
    created_document = None

    # Handle CREATE_DOC command
    if '[CREATE_DOC:' in answer:
        result = execute_create_doc(answer, drive_service)
        if result.get('status') == 'success':
            created_document = {
                'id': result['file_id'],
                'name': result['title'],
                'mimeType': 'application/vnd.google-apps.document'
            }
        answer = remove_command_from_answer(answer, '[CREATE_DOC:')

    # Handle CREATE_SHEET command
    elif '[CREATE_SHEET:' in answer:
        result = execute_create_sheet(answer, drive_service)
        if result.get('status') == 'success':
            created_document = {
                'id': result['file_id'],
                'name': result['title'],
                'mimeType': 'application/vnd.google-apps.spreadsheet'
            }
        answer = remove_command_from_answer(answer, '[CREATE_SHEET:')

    # Handle CREATE_SLIDES command
    elif '[CREATE_SLIDES:' in answer:
        result = execute_create_slides(answer, drive_service)
        if result.get('status') == 'success':
            created_document = {
                'id': result['file_id'],
                'name': result['title'],
                'mimeType': 'application/vnd.google-apps.presentation'
            }
        answer = remove_command_from_answer(answer, '[CREATE_SLIDES:')

    # Handle MOVE_FILE command
    elif '[MOVE_FILE:' in answer:
        result = execute_move_file(answer, drive_service)
        answer = remove_command_from_answer(answer, '[MOVE_FILE:')
        if result.get('status') == 'success':
            answer += " ✅"

    return jsonify({
        'response': answer,
        'created_document': created_document
    })


def execute_create_doc(answer, drive_service):
    """Execute CREATE_DOC command"""
    try:
        import re
        match = re.search(r'\[CREATE_DOC:(\{.*?\})\]', answer, re.DOTALL)
        if not match:
            return {'status': 'error', 'message': 'Invalid CREATE_DOC format'}

        params = json.loads(match.group(1))
        title = params.get('title', 'Nouveau document')
        content = params.get('content', '')

        return drive_service.create_google_doc(title, content)

    except Exception as e:
        import traceback
        print(f"[execute_create_doc] Error: {e}\n{traceback.format_exc()}", flush=True)
        return {'status': 'error', 'message': str(e)}


def execute_create_sheet(answer, drive_service):
    """Execute CREATE_SHEET command"""
    try:
        import re
        match = re.search(r'\[CREATE_SHEET:(\{.*?\})\]', answer, re.DOTALL)
        if not match:
            return {'status': 'error', 'message': 'Invalid CREATE_SHEET format'}

        params = json.loads(match.group(1))
        title = params.get('title', 'Nouveau tableau')
        headers = params.get('headers', [])

        return drive_service.create_google_sheet(title, headers)

    except Exception as e:
        import traceback
        print(f"[execute_create_sheet] Error: {e}\n{traceback.format_exc()}", flush=True)
        return {'status': 'error', 'message': str(e)}


def execute_create_slides(answer, drive_service):
    """Execute CREATE_SLIDES command"""
    try:
        import re
        match = re.search(r'\[CREATE_SLIDES:(\{.*?\})\]', answer, re.DOTALL)
        if not match:
            return {'status': 'error', 'message': 'Invalid CREATE_SLIDES format'}

        params = json.loads(match.group(1))
        title = params.get('title', 'Nouvelle presentation')

        return drive_service.create_google_slides(title)

    except Exception as e:
        import traceback
        print(f"[execute_create_slides] Error: {e}\n{traceback.format_exc()}", flush=True)
        return {'status': 'error', 'message': str(e)}


def execute_move_file(answer, drive_service):
    """Execute MOVE_FILE command"""
    try:
        import re
        match = re.search(r'\[MOVE_FILE:(\{.*?\})\]', answer, re.DOTALL)
        if not match:
            return {'status': 'error', 'message': 'Invalid MOVE_FILE format'}

        params = json.loads(match.group(1))
        file_id = params.get('file_id')
        destination_folder_id = params.get('destination_folder_id')

        if not file_id or not destination_folder_id:
            return {'status': 'error', 'message': 'Missing file_id or destination_folder_id'}

        return drive_service.move_file(file_id, destination_folder_id)

    except Exception as e:
        import traceback
        print(f"[execute_move_file] Error: {e}\n{traceback.format_exc()}", flush=True)
        return {'status': 'error', 'message': str(e)}


def execute_create_chart(answer, drive_service):
    """Execute CREATE_CHART command for Google Sheets"""
    try:
        # Utiliser extract_json_from_command pour un meilleur parsing
        params = extract_json_from_command(answer, '[CREATE_CHART:')
        if not params:
            return {'status': 'error', 'message': 'Invalid CREATE_CHART format'}

        file_id = params.get('file_id')
        chart_type = params.get('type', 'COLUMN')
        data_range = params.get('range')
        title = params.get('title', '')

        print(f"[execute_create_chart] file_id={file_id}, type={chart_type}, range={data_range}", flush=True)

        if not file_id or not data_range:
            return {'status': 'error', 'message': 'Missing file_id or range'}

        return drive_service.create_chart_in_sheet(file_id, chart_type, data_range, title)

    except Exception as e:
        import traceback
        print(f"[execute_create_chart] Error: {e}\n{traceback.format_exc()}", flush=True)
        return {'status': 'error', 'message': str(e)}


def execute_insert_table(answer, drive_service):
    """Execute INSERT_TABLE command for Google Docs"""
    try:
        import re
        match = re.search(r'\[INSERT_TABLE:(\{.*?\})\]', answer, re.DOTALL)
        if not match:
            return {'status': 'error', 'message': 'Invalid INSERT_TABLE format'}

        params = json.loads(match.group(1))
        file_id = params.get('file_id')
        rows = params.get('rows', 3)
        cols = params.get('cols', 3)
        data = params.get('data')  # Optional 2D array

        if not file_id:
            return {'status': 'error', 'message': 'Missing file_id'}

        return drive_service.insert_table_in_doc(file_id, rows, cols, data)

    except Exception as e:
        import traceback
        print(f"[execute_insert_table] Error: {e}\n{traceback.format_exc()}", flush=True)
        return {'status': 'error', 'message': str(e)}


def execute_format_range(answer, drive_service):
    """Execute FORMAT_RANGE command for Google Sheets"""
    try:
        import re
        match = re.search(r'\[FORMAT_RANGE:(\{.*?\})\]', answer, re.DOTALL)
        if not match:
            return {'status': 'error', 'message': 'Invalid FORMAT_RANGE format'}

        params = json.loads(match.group(1))
        file_id = params.get('file_id')
        range_str = params.get('range')
        bold = params.get('bold', False)
        background_color = params.get('background_color')
        borders = params.get('borders', False)

        if not file_id or not range_str:
            return {'status': 'error', 'message': 'Missing file_id or range'}

        return drive_service.format_sheet_range(file_id, range_str, bold, background_color, borders)

    except Exception as e:
        import traceback
        print(f"[execute_format_range] Error: {e}\n{traceback.format_exc()}", flush=True)
        return {'status': 'error', 'message': str(e)}


@app.route('/chat-edit', methods=['POST'])
def chat_edit():
    """Chat endpoint for editing or creating documents"""
    if not is_authenticated():
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.json
    if not data or 'message' not in data:
        return jsonify({'error': 'Missing message'}), 400

    message = data['message']
    document = data.get('document')  # {id, name, mimeType} or None for creation mode
    history = data.get('history', [])

    try:
        credentials = get_valid_credentials()
        if not credentials:
            return jsonify({'error': 'Invalid credentials'}), 401

        drive_service = DriveService(credentials)

        # CREATION MODE - no document selected
        if not document:
            return handle_creation_mode(message, history, drive_service)

        mime_type = document.get('mimeType', '')

        # Determine document type and build appropriate prompt
        if 'spreadsheet' in mime_type:
            doc_type = 'Google Sheet'

            # Read current sheet content
            sheet_content = drive_service.get_sheet_content(document['id'])
            values = sheet_content.get('values', [])

            # Format sheet content for the AI
            if values:
                # Create a visual representation with cell references
                sheet_display = "DONNEES DE LA FEUILLE:\n"
                sheet_display += "```\n"
                for row_idx, row in enumerate(values[:50], start=1):  # Limit to 50 rows
                    row_str = f"Ligne {row_idx}: "
                    for col_idx, cell in enumerate(row):
                        col_letter = chr(65 + col_idx) if col_idx < 26 else f"A{chr(65 + col_idx - 26)}"
                        row_str += f"[{col_letter}{row_idx}={cell}] "
                    sheet_display += row_str.strip() + "\n"
                sheet_display += "```\n"
                if len(values) > 50:
                    sheet_display += f"... et {len(values) - 50} lignes supplementaires\n"
            else:
                sheet_display = "La feuille est vide.\n"

            doc_instructions = f"""
================================================================================
                    AGENT GOOGLE SHEETS - INSTRUCTIONS
================================================================================

Tu es un agent specialise dans la modification de Google Sheets convergente.
Tu executes les instructions de l'utilisateur pour modifier tout type de tableau:
modeles financiers, tableaux de bord, inventaires, plannings, rapports, budgets, etc.

================================================================================
                    CONTENU ACTUEL DU DOCUMENT
================================================================================

{sheet_display}

================================================================================
                    1. REGLES FONDAMENTALES
================================================================================

**REGLE #1 - UNE DONNEE = UNE CELLULE**
JAMAIS plusieurs informations dans une seule cellule!
"lundi 8 dec, 12 users, agent1: 3" = 4 cellules separees (A, B, C, D)

**REGLE #2 - FORMULES OBLIGATOIRES**
TOUJOURS des formules Excel, JAMAIS de valeurs calculees manuellement:
- Total → =SUM(B2:B10) PAS "150"
- Moyenne → =AVERAGE(C2:C10) PAS "25.5"
- Comptage → =COUNTA(A:A)-1 PAS "10"
- Pourcentage → =B2/$B$11*100 PAS "15%"
- Conditions → =IF(), =SUMIF(), =COUNTIF()

**REGLE #3 - INTEGRITE DES TOTAUX**
CHAQUE poste de donnees DOIT etre capture par son total.
- Verifier que SUM() couvre TOUTES les lignes
- Les valeurs conditionnelles → integrees via IF() dans le total
- JAMAIS de ligne "informative" hors du calcul total

**REGLE #4 - PARAMETRES CENTRALISES**
Les parametres modifiables (taux, prix, hypotheses):
- Regroupes dans une section dediee
- References absolues ($B$5) dans les formules
- JAMAIS hardcodes dans les formules

================================================================================
                    2. PROCESSUS D'ANALYSE OBLIGATOIRE
================================================================================

AVANT toute modification, tu DOIS analyser:

**ETAPE 1 - STRUCTURE**
- Ligne d'en-tete: A1=?, B1=?, C1=?, D1=?
- Derniere ligne de donnees: ligne N
- Nouvelle ligne = N+1

**ETAPE 2 - MAPPING**
Chaque info utilisateur → une colonne:
- "lundi 8 dec" → Date → colonne A
- "12 utilisateurs" → Users → colonne B
- "Friday: 3" → Friday → colonne C

**ETAPE 3 - VERIFICATION**
- Toutes les formules referencent les bonnes cellules?
- Pas de cellule vide referencee?
- Totaux capturent toutes les lignes?

================================================================================
                    3. COMMANDES DISPONIBLES
================================================================================

**MODIFIER DES CELLULES:**
[MODIFY_CELLS:{{"file_id":"{document['id']}", "updates":[
  {{"cell":"A6", "value":"Lundi 8 dec"}},
  {{"cell":"B6", "value":"12"}},
  {{"cell":"C6", "value":"=SUM(D6:F6)"}}
]}}]

**CREER UN GRAPHIQUE:**
[CREATE_CHART:{{"file_id":"{document['id']}", "type":"LINE|COLUMN|BAR|PIE|SCATTER", "range":"A1:C10", "title":"Titre"}}]
- Evolution/temps → LINE
- Comparaison → COLUMN ou BAR
- Repartition → PIE

**FORMATER:**
[FORMAT_RANGE:{{"file_id":"{document['id']}", "range":"A1:E1", "bold":true, "borders":true}}]

================================================================================
                    4. EXEMPLES
================================================================================

**Ajouter une ligne de donnees:**
Demande: "Ajoute lundi 8 dec, 12 users, Friday 3, Campagne 2"
[MODIFY_CELLS:{{"file_id":"xxx", "updates":[{{"cell":"A6","value":"Lundi 8 dec"}},{{"cell":"B6","value":"12"}},{{"cell":"C6","value":"3"}},{{"cell":"D6","value":"2"}}]}}]
C'est fait!

**Ajouter une formule de total:**
Demande: "Ajoute un total en ligne 10"
[MODIFY_CELLS:{{"file_id":"xxx", "updates":[{{"cell":"A10","value":"TOTAL"}},{{"cell":"B10","value":"=SUM(B2:B9)"}},{{"cell":"C10","value":"=SUM(C2:C9)"}}]}}]
C'est fait!

**Modifier une valeur:**
Demande: "Change B5 en 150"
[MODIFY_CELLS:{{"file_id":"xxx", "updates":[{{"cell":"B5","value":"150"}}]}}]
C'est fait!

================================================================================
                    5. ERREURS A EVITER
================================================================================

| Erreur | Correction |
|--------|------------|
| Valeur hardcodee | Utiliser une formule =SUM(), =AVERAGE() |
| Plusieurs infos dans 1 cellule | Separer en plusieurs cellules |
| Poste absent du total | Etendre la plage SUM() |
| Plage SUM trop courte | Verifier derniere ligne |
| Reference relative | Utiliser $B$5 pour les parametres |

================================================================================
                    6. INTERDICTIONS ABSOLUES
================================================================================

- INTERDIT: "Je vais ajouter..." (N'ANNONCE PAS, EXECUTE!)
- INTERDIT: "C'est fait" SANS commande [...] (MENSONGE)
- OBLIGATOIRE: Commande D'ABORD, message court APRES

**FORMAT DE REPONSE:**
[MODIFY_CELLS:{{...}}]
C'est fait!
"""

        elif 'presentation' in mime_type:
            doc_type = 'Google Slides'
            doc_instructions = f"""Pour ajouter une diapositive:
[MODIFY_DOC:{{"file_id":"{document['id']}", "action":"append", "content":"Titre\\n\\nContenu de la slide"}}]"""

        else:
            doc_type = 'Google Doc'

            # Read current document content
            doc_content = drive_service.get_document_content(document['id'])
            print(f"[chat-edit] Google Doc content retrieved: {len(doc_content) if doc_content else 0} chars", flush=True)
            print(f"[chat-edit] Google Doc content preview: {doc_content[:200] if doc_content else 'EMPTY'}...", flush=True)

            doc_text = doc_content if doc_content else "(Document vide)"

            doc_instructions = f"""
================================================================================
                        DOCUMENT A MODIFIER
================================================================================

{doc_text}

================================================================================
                        INSTRUCTIONS POUR L'AGENT
================================================================================

Tu es un agent specialise dans la modification de documents Google Docs.

**PROCESSUS OBLIGATOIRE EN 3 ETAPES:**

ETAPE 1 - LECTURE COMPLETE
Tu viens de lire le document EN ENTIER ci-dessus. Prends le temps de comprendre:
- La structure globale (titres, sections, paragraphes)
- Le contenu et le contexte du document
- Le style d'ecriture utilise

ETAPE 2 - ANALYSE DE LA DEMANDE
Lis attentivement ce que l'utilisateur demande:
- AJOUTER quelque chose? Ou exactement?
- MODIFIER quelque chose? Quoi precisement?
- SUPPRIMER quelque chose? Quelle partie?

ETAPE 3 - EXECUTION PRECISE
Genere le nouveau contenu du document avec UNIQUEMENT la modification demandee.

**REGLE ABSOLUE:**
NE MODIFIE RIEN D'AUTRE que ce que l'utilisateur a explicitement demande!
- Si on te demande de changer un mot, change SEULEMENT ce mot
- Si on te demande d'ajouter une section, ajoute SEULEMENT cette section
- Si on te demande de supprimer un paragraphe, supprime SEULEMENT ce paragraphe
- TOUT LE RESTE du document doit rester EXACTEMENT identique

**INTERDICTION ABSOLUE:**
- INTERDIT: "Je vais modifier...", "Je vais ajouter..." (JAMAIS ANNONCER)
- INTERDIT: "J'ai modifie..." SANS commande (MENSONGE)
- OBLIGATOIRE: EXECUTER D'ABORD avec [MODIFY_DOC:...], parler APRES

**COMMANDE:**
[MODIFY_DOC:{{"file_id":"{document['id']}", "action":"replace", "content":"[DOCUMENT COMPLET MODIFIE]"}}]

**FORMAT OBLIGATOIRE - COMMANDE D'ABORD:**

Demande: "Ajoute une conclusion"
BONNE REPONSE:
[MODIFY_DOC:{{"file_id":"xxx", "action":"replace", "content":"Titre\\n\\nContenu...\\n\\nConclusion\\n\\nEn resume..."}}]
C'est fait!

MAUVAISE REPONSE:
"Je vais ajouter une conclusion..." (INTERDIT - tu n'executes pas!)
"J'ai ajoute la conclusion." (INTERDIT - pas de commande = mensonge)
"""

        # Récupérer la liste des dossiers pour permettre de déplacer des fichiers
        folders = drive_service.list_folders_flat()
        folders_list = "\n".join([f"- {f['name']} (ID: {f['id']})" for f in folders[:20]])

        # Build type-specific methodology
        if 'spreadsheet' in mime_type:
            type_methodology = """
═══════════════════════════════════════════════════════
ANALYSE PREALABLE DU TABLEAU - METHODOLOGIE COMPLETE
═══════════════════════════════════════════════════════
AVANT TOUTE MODIFICATION, tu DOIS executer ce processus en 7 PHASES :

PHASE 1 : CARTOGRAPHIE STRUCTURELLE (Comprendre l'architecture)
Etape 1.1 - Identifier la ligne d'en-tete :
Question : Quelle ligne contient les titres de colonnes ?
Reponse : Generalement ligne 1
Action : Note mentalement A1, B1, C1, D1... = titres
Etape 1.2 - Compter les dimensions :
Combien de colonnes utilisees ? (A a ?)
Combien de lignes de donnees ? (apres l'en-tete)
Y a-t-il des lignes speciales ? (totaux, moyennes...)

PHASE 2 : ANALYSE VERTICALE (Colonne par colonne)
Pour CHAQUE colonne, tu reponds a ces 5 questions :
1. Quel est le titre ? 2. Quel TYPE de donnees ? 3. Format utilise ?
4. Les valeurs sont-elles uniques ? 5. Role de cette colonne ?

PHASE 3 : ANALYSE HORIZONTALE (Ligne par ligne)
Pour CHAQUE ligne, tu identifies ce qu'elle represente.

PHASE 4 : CROISEMENT MATRICIEL (Ligne x Colonne)
Tu CROISES les deux analyses pour comprendre CHAQUE CELLULE :
B3 = Colonne B (Utilisateurs) + Ligne 3 (Mardi) = Utilisateurs du Mardi

PHASE 5 : TRADUCTION LANGAGE NATUREL = COORDONNEES
L'utilisateur ne parle JAMAIS en coordonnees Excel. Il dit :
"Mets 150 utilisateurs pour vendredi"
= "utilisateurs" = Colonne B, "vendredi" = Ligne 6 = B6

PHASE 6 : DETECTION DES PIEGES COURANTS
- Lignes de totaux (formules)
- Formules existantes
- Ambiguite temporelle

PHASE 7 : GENERATION DE LA COMMANDE
[MODIFY_CELLS:{{"file_id":"...", "updates":[{{"cell":"B6","value":150}}]}}]

**REGLE FORMULES:** TOUJOURS des FORMULES: =SUM(), =AVERAGE(), =SUMIF()
JAMAIS des valeurs calculees manuellement."""

            rules_examples = """
BON (conversationnel + action):
User: "Ajoute lundi et mardi stp, avec 10 et 15 users"
Friday: [MODIFY_CELLS:{{"file_id":"xxx", "updates":[{{"cell":"A2", "value":"Lundi"}}, {{"cell":"B2", "value":"10"}}, {{"cell":"A3", "value":"Mardi"}}, {{"cell":"B3", "value":"15"}}]}}]
Voila les deux jours! Tu veux que je continue?"""

        else:
            # For Docs and Slides - no spreadsheet methodology
            type_methodology = """
═══════════════════════════════════════════════════════
ANALYSE DU DOCUMENT - METHODOLOGIE
═══════════════════════════════════════════════════════
AVANT TOUTE MODIFICATION:

ETAPE 1: LIRE LE CONTENU ACTUEL
Analyse le document fourni ci-dessous.

ETAPE 2: IDENTIFIER LA STRUCTURE
- Titres et sous-titres
- Paragraphes et sections
- Elements cles du document

ETAPE 3: COMPRENDRE LA DEMANDE
- AJOUTER du contenu? Ou exactement?
- MODIFIER du contenu? Quel texte specifique?
- SUPPRIMER du contenu? Quelle partie?

ETAPE 4: CHOISIR LA BONNE COMMANDE
- [MODIFY_DOC:...] pour ajouter a la fin
- [REPLACE_TEXT:...] pour remplacer un texte
- [INSERT_AFTER:...] pour inserer apres un texte
- [DELETE_TEXT:...] pour supprimer"""

            rules_examples = """
BON (conversationnel + action):
User: "Ajoute une conclusion"
Friday: [MODIFY_DOC:{{"file_id":"xxx", "action":"append", "content":"\\n\\nConclusion\\n\\nEn resume..."}}]
Voila la conclusion ajoutee! Tu veux que je modifie autre chose?

User: "Change le titre en 'Nouveau Projet'"
Friday: [REPLACE_TEXT:{{"file_id":"xxx", "find":"Ancien Titre", "replace":"Nouveau Projet"}}]
C'est fait!"""

        # Build system prompt
        system_prompt = f"""Tu es Friday, un assistant IA conversationnel et efficace qui travaille sur "{document['name']}" ({doc_type}).

═══════════════════════════════════════════════════════
QUI TU ES
═══════════════════════════════════════════════════════

Tu es un ASSISTANT CONVERSATIONNEL et EFFICACE:
- Tu parles naturellement, comme un collegue competent et sympa
- Tu comprends le contexte et les sous-entendus
- Tu es agreable tout en etant extremement productif
- Tu peux discuter ET executer en meme temps

{type_methodology}

═══════════════════════════════════════════════════════
REGLES D'EXECUTION - CRITIQUES
═══════════════════════════════════════════════════════

**REGLE #1 - EXECUTION IMMEDIATE ET COMPLETE:**
DEMANDES MULTIPLES - CRUCIAL:
Un seul message peut contenir PLUSIEURS demandes. Tu DOIS TOUTES les traiter.
JAMAIS: "J'ai fait X, redis-moi pour Y" → INTERDIT

**REGLE #2 - INTERDICTION ABSOLUE:**
- INTERDIT: "Je vais ajouter...", "Je vais modifier..." (N'ANNONCE JAMAIS, EXECUTE!)
- INTERDIT: "J'ai ajoute...", "C'est fait" SANS commande [...] (MENSONGE)
- OBLIGATOIRE: [MODIFY_CELLS:{{...}}] ou [MODIFY_DOC:{{...}}] D'ABORD, message APRES
Sans commande = TU MENS A L'UTILISATEUR.

**REGLE #3 - FORMAT OBLIGATOIRE:**
COMMANDE D'ABORD, message court apres:
{rules_examples}

MAUVAIS (INTERDIT):
User: "Ajoute une ligne"
Friday: "Je vais ajouter une ligne pour toi." (PAS DE COMMANDE = ECHEC)

═══════════════════════════════════════════════════════
CAPACITES ET COMMANDES
═══════════════════════════════════════════════════════

{doc_instructions}

**DEPLACER:** [MOVE_FILE:{{"file_id":"{document['id']}", "destination_folder_id":"ID"}}]
DOSSIERS: {folders_list if folders_list else "Aucun"}

═══════════════════════════════════════════════════════
PERSONNALITE ET CONVERSATION
═══════════════════════════════════════════════════════

**TON NATUREL:**
- Parle comme un humain, pas comme un robot
- Tu peux faire des remarques, proposer des suites
- "Tu veux que j'ajoute aussi...?", "Autre chose?"

**GESTION DES AMBIGUITES:**
- Detail mineur manquant → choix intelligent sans demander
- Intention majeure floue → question courte et naturelle

═══════════════════════════════════════════════════════

TU ES CONVERSATIONNEL ET EFFICACE.
TU TRAITES TOUTES LES DEMANDES D'UN MESSAGE.
CHAQUE ACTION = COMMANDE VISIBLE."""

        # Call Gemini
        import google.generativeai as genai
        genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))

        gemini_model = genai.GenerativeModel('gemini-2.5-pro-preview-05-06')

        # Build conversation for Gemini
        gemini_history = []
        for msg in history[-20:]:
            role = "user" if msg["role"] == "user" else "model"
            gemini_history.append({"role": role, "parts": [msg["content"]]})

        chat = gemini_model.start_chat(history=gemini_history)

        # Send message with system prompt prepended
        full_prompt = f"{system_prompt}\n\n---\n\nUser: {message}"
        response = chat.send_message(full_prompt)

        answer = response.text

        # Check for modification commands
        modification_result = None

        # Handle cell-specific updates for Sheets
        if '[MODIFY_CELLS:' in answer:
            modification_result = execute_cells_modification(answer, credentials)
            answer = remove_command_from_answer(answer, '[MODIFY_CELLS:')

        # Handle text replacement in Docs
        elif '[REPLACE_TEXT:' in answer:
            modification_result = execute_replace_text(answer, credentials)
            answer = remove_command_from_answer(answer, '[REPLACE_TEXT:')

        # Handle text insertion after specific text
        elif '[INSERT_AFTER:' in answer:
            modification_result = execute_insert_after(answer, credentials)
            answer = remove_command_from_answer(answer, '[INSERT_AFTER:')

        # Handle text deletion
        elif '[DELETE_TEXT:' in answer:
            modification_result = execute_delete_text(answer, credentials)
            answer = remove_command_from_answer(answer, '[DELETE_TEXT:')

        # Handle document modifications (append/replace)
        elif '[MODIFY_DOC:' in answer:
            modification_result = execute_agent_modification(answer, credentials)
            answer = remove_command_from_answer(answer, '[MODIFY_DOC:')

        # Handle file move
        elif '[MOVE_FILE:' in answer:
            modification_result = execute_move_file(answer, drive_service)
            answer = remove_command_from_answer(answer, '[MOVE_FILE:')

        # Handle chart creation
        elif '[CREATE_CHART:' in answer:
            modification_result = execute_create_chart(answer, drive_service)
            answer = remove_command_from_answer(answer, '[CREATE_CHART:')

        # Handle table insertion
        elif '[INSERT_TABLE:' in answer:
            modification_result = execute_insert_table(answer, drive_service)
            answer = remove_command_from_answer(answer, '[INSERT_TABLE:')

        # Handle range formatting
        elif '[FORMAT_RANGE:' in answer:
            modification_result = execute_format_range(answer, drive_service)
            answer = remove_command_from_answer(answer, '[FORMAT_RANGE:')

        return jsonify({
            'answer': answer,
            'modification_result': modification_result
        })

    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"Chat-edit error: {e}\n{error_trace}")
        return jsonify({
            'error': str(e),
            'error_type': type(e).__name__
        }), 500


@app.route('/stats')
def get_stats():
    """Get indexing statistics"""
    if not is_authenticated():
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        rag = get_rag_engine()
        stats = rag.get_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/clear', methods=['POST'])
def clear_index():
    """Clear all indexed documents"""
    if not is_authenticated():
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        rag = get_rag_engine()
        success = rag.clear_index()
        if success:
            return jsonify({'status': 'success', 'message': 'Index cleared'})
        else:
            return jsonify({'error': 'Failed to clear index'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/drive/info')
def drive_info():
    """Get Drive user info"""
    if not is_authenticated():
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        credentials = get_valid_credentials()
        drive_service = DriveService(credentials)
        info = drive_service.get_user_info()
        return jsonify(info)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/health')
def health():
    """Health check endpoint for Render"""
    return jsonify({'status': 'healthy'})


# ============== Document Modification Endpoints ==============

@app.route('/modify-document', methods=['POST'])
def modify_document():
    """Modify a Google Doc content"""
    print("=== MODIFY DOCUMENT START ===", flush=True)

    if not is_authenticated():
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        credentials = get_valid_credentials()
        if not credentials:
            return jsonify({'error': 'Invalid credentials'}), 401

        data = request.json or {}
        file_id = data.get('file_id')
        new_content = data.get('content')
        action = data.get('action', 'replace')  # 'replace' or 'append'

        if not file_id:
            return jsonify({'error': 'file_id is required'}), 400

        if new_content is None:
            return jsonify({'error': 'content is required'}), 400

        print(f"[modify-document] Action: {action}, File ID: {file_id}", flush=True)
        print(f"[modify-document] Content length: {len(new_content)}", flush=True)

        drive_service = DriveService(credentials)

        # Get file metadata to check type
        file_meta = drive_service.get_file_metadata(file_id)
        if not file_meta:
            return jsonify({'error': 'File not found'}), 404

        mime_type = file_meta.get('mimeType', '')
        file_name = file_meta.get('name', 'Unknown')
        print(f"[modify-document] File: {file_name}, Type: {mime_type}", flush=True)

        # Only support Google Docs for now
        if mime_type != 'application/vnd.google-apps.document':
            return jsonify({
                'error': f'Only Google Docs are supported for modification. File type: {mime_type}'
            }), 400

        # Perform the modification
        if action == 'append':
            result = drive_service.append_to_google_doc(file_id, new_content)
        else:
            result = drive_service.update_google_doc(file_id, new_content)

        if result['status'] == 'success':
            print(f"[modify-document] Success!", flush=True)
            return jsonify({
                'status': 'success',
                'message': f'Document "{file_name}" modified successfully',
                'file_id': file_id,
                'file_name': file_name
            })
        else:
            return jsonify(result), 500

    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"=== MODIFY DOCUMENT ERROR ===\n{error_trace}", flush=True)
        return jsonify({
            'error': str(e),
            'traceback': error_trace
        }), 500


@app.route('/create-document', methods=['POST'])
def create_document():
    """Create a new Google Doc"""
    print("=== CREATE DOCUMENT START ===", flush=True)

    if not is_authenticated():
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        credentials = get_valid_credentials()
        if not credentials:
            return jsonify({'error': 'Invalid credentials'}), 401

        data = request.json or {}
        title = data.get('title')
        content = data.get('content', '')

        if not title:
            return jsonify({'error': 'title is required'}), 400

        print(f"[create-document] Title: {title}", flush=True)

        drive_service = DriveService(credentials)
        result = drive_service.create_google_doc(title, content)

        if result['status'] == 'success':
            print(f"[create-document] Success! File ID: {result['file_id']}", flush=True)
            return jsonify(result)
        else:
            return jsonify(result), 500

    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"=== CREATE DOCUMENT ERROR ===\n{error_trace}", flush=True)
        return jsonify({
            'error': str(e),
            'traceback': error_trace
        }), 500


@app.route('/list-editable-documents')
def list_editable_documents():
    """List Google Docs, Sheets and Slides that can be edited"""
    if not is_authenticated():
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        credentials = get_valid_credentials()
        if not credentials:
            return jsonify({'error': 'Invalid credentials'}), 401

        drive_service = DriveService(credentials)

        # List Google Docs, Sheets and Slides (all editable)
        all_files = []
        page_token = None

        # Query for all three document types
        mime_types = [
            "mimeType='application/vnd.google-apps.document'",
            "mimeType='application/vnd.google-apps.spreadsheet'",
            "mimeType='application/vnd.google-apps.presentation'"
        ]
        query = f"({' or '.join(mime_types)}) and trashed=false"

        while True:
            results = drive_service.service.files().list(
                pageSize=100,
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, webViewLink)",
                q=query,
                orderBy="modifiedTime desc",
                pageToken=page_token
            ).execute()

            files = results.get('files', [])
            all_files.extend(files)

            page_token = results.get('nextPageToken')
            if not page_token:
                break

        return jsonify({
            'status': 'success',
            'documents': all_files,
            'count': len(all_files)
        })

    except Exception as e:
        import traceback
        print(f"[list-editable-documents] Error: {e}\n{traceback.format_exc()}", flush=True)
        return jsonify({'error': str(e)}), 500


@app.route('/tts', methods=['POST'])
def text_to_speech():
    """Convert text to speech using OpenAI TTS API"""
    if not is_authenticated():
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.json
    if not data or 'text' not in data:
        return jsonify({'error': 'No text provided'}), 400

    text = data['text']
    if not text.strip():
        return jsonify({'error': 'Empty text'}), 400

    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

        # Use "onyx" voice - deep male voice
        response = client.audio.speech.create(
            model="tts-1",
            voice="onyx",
            input=text,
            response_format="mp3"
        )

        # Return audio as base64
        import base64
        audio_content = response.content
        audio_base64 = base64.b64encode(audio_content).decode('utf-8')

        return jsonify({
            'status': 'success',
            'audio': audio_base64,
            'format': 'mp3'
        })

    except Exception as e:
        import traceback
        print(f"[tts] Error: {e}\n{traceback.format_exc()}", flush=True)
        return jsonify({'error': str(e)}), 500


@app.route('/transcribe', methods=['POST'])
def transcribe_audio():
    """
    Endpoint pour transcrire l'audio immédiatement
    Retourne juste la transcription pour affichage instantané
    """
    if not is_authenticated():
        return jsonify({'error': 'Not authenticated'}), 401

    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400

    audio_file = request.files['audio']

    try:
        import tempfile
        from openai import OpenAI

        client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

        # Sauvegarder le fichier audio temporairement
        with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as temp_audio:
            audio_file.save(temp_audio.name)
            temp_audio_path = temp_audio.name

        # Transcrire avec Whisper
        with open(temp_audio_path, 'rb') as audio:
            transcription = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio,
                language="fr"
            )

        # Nettoyer le fichier temporaire
        os.unlink(temp_audio_path)

        return jsonify({
            'status': 'success',
            'transcription': transcription.text
        })

    except Exception as e:
        import traceback
        print(f"[transcribe] Error: {e}\n{traceback.format_exc()}", flush=True)
        return jsonify({'error': str(e)}), 500


@app.route('/voice-chat', methods=['POST'])
def voice_chat():
    """
    Endpoint pour conversation vocale en temps réel
    Reçoit: audio (fichier) + document + historique
    Retourne: transcription + réponse texte + audio de la réponse
    """
    if not is_authenticated():
        return jsonify({'error': 'Not authenticated'}), 401

    # Vérifier qu'on a un fichier audio
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400

    audio_file = request.files['audio']
    document_json = request.form.get('document', '{}')
    history_json = request.form.get('history', '[]')

    try:
        import json
        import base64
        import tempfile
        from openai import OpenAI

        document = json.loads(document_json)
        history = json.loads(history_json)

        client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

        # 1. SPEECH-TO-TEXT avec Whisper
        # Sauvegarder le fichier audio temporairement
        with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as temp_audio:
            audio_file.save(temp_audio.name)
            temp_audio_path = temp_audio.name

        # Transcrire avec Whisper
        with open(temp_audio_path, 'rb') as audio:
            transcription = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio,
                language="fr"
            )

        user_message = transcription.text
        print(f"[voice-chat] Transcription: {user_message}", flush=True)

        # Nettoyer le fichier temporaire
        os.unlink(temp_audio_path)

        # Variables pour le résultat
        ai_answer = None
        modification_result = None
        created_document = None

        # Si pas de document sélectionné, mode CRÉATION
        if not document or not document.get('id'):
            # Mode création de documents
            credentials = get_valid_credentials()
            if not credentials:
                return jsonify({'error': 'Invalid credentials'}), 401

            drive_service = DriveService(credentials)

            creation_prompt = """Tu es Friday, un assistant vocal qui peut créer des documents Google.

Tu peux créer:
- Google Docs: [CREATE_DOC:{"title":"Titre", "content":"Contenu optionnel"}]
- Google Sheets: [CREATE_SHEET:{"title":"Titre", "headers":["Col1", "Col2"]}]
- Google Slides: [CREATE_SLIDES:{"title":"Titre"}]

REGLES:
1. Si l'utilisateur veut créer un document, INCLUS la commande CREATE
2. Propose un titre pertinent si non spécifié
3. Pour les tableaux, suggère des colonnes appropriées
4. Réponses courtes pour lecture vocale
5. Si pas de demande de création, réponds normalement"""

            messages = [{"role": "system", "content": creation_prompt}]
            for msg in history[-10:]:
                messages.append(msg)
            messages.append({"role": "user", "content": user_message})

            response = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=messages,
                temperature=0.7,
                max_tokens=500
            )
            ai_answer = response.choices[0].message.content

            # Gérer les commandes de création
            if '[CREATE_DOC:' in ai_answer:
                result = execute_create_doc(ai_answer, drive_service)
                if result.get('status') == 'success':
                    created_document = {
                        'id': result['file_id'],
                        'name': result['title'],
                        'mimeType': 'application/vnd.google-apps.document'
                    }
                ai_answer = remove_command_from_answer(ai_answer, '[CREATE_DOC:')
            elif '[CREATE_SHEET:' in ai_answer:
                result = execute_create_sheet(ai_answer, drive_service)
                if result.get('status') == 'success':
                    created_document = {
                        'id': result['file_id'],
                        'name': result['title'],
                        'mimeType': 'application/vnd.google-apps.spreadsheet'
                    }
                ai_answer = remove_command_from_answer(ai_answer, '[CREATE_SHEET:')
            elif '[CREATE_SLIDES:' in ai_answer:
                result = execute_create_slides(ai_answer, drive_service)
                if result.get('status') == 'success':
                    created_document = {
                        'id': result['file_id'],
                        'name': result['title'],
                        'mimeType': 'application/vnd.google-apps.presentation'
                    }
                ai_answer = remove_command_from_answer(ai_answer, '[CREATE_SLIDES:')

        else:
            # 2. TRAITEMENT IA avec contexte document
            credentials = get_valid_credentials()
            if not credentials:
                return jsonify({'error': 'Invalid credentials'}), 401

            drive_service = DriveService(credentials)
            mime_type = document.get('mimeType', '')

            # Construire le prompt selon le type de document
            if 'spreadsheet' in mime_type:
                doc_type = 'Google Sheet'
                sheet_content = drive_service.get_sheet_content(document['id'])
                values = sheet_content.get('values', [])

                if values:
                    sheet_display = "DONNEES DE LA FEUILLE:\n```\n"
                    for row_idx, row in enumerate(values[:50], start=1):
                        row_str = f"Ligne {row_idx}: "
                        for col_idx, cell in enumerate(row):
                            col_letter = chr(65 + col_idx) if col_idx < 26 else f"A{chr(65 + col_idx - 26)}"
                            row_str += f"[{col_letter}{row_idx}={cell}] "
                        sheet_display += row_str.strip() + "\n"
                    sheet_display += "```\n"
                else:
                    sheet_display = "La feuille est vide.\n"

                doc_instructions = f"""
═══════════════════════════════════════════════════════
ANALYSE PREALABLE - 4 ETAPES OBLIGATOIRES
═══════════════════════════════════════════════════════

**ETAPE 1 - LIRE LIGNE PAR LIGNE:**
- Ligne 1 = en-tetes ou donnees?
- Ligne 2 = quelle entree? (Lundi? Premier client?)
- Ligne 3, 4... = quelles entrees?

**ETAPE 2 - LIRE COLONNE PAR COLONNE:**
- Colonne A = quel type? (jours? noms?)
- Colonne B = quel type? (utilisateurs? montants?)
- Colonne C = quel type? (Friday? categories?)

**ETAPE 3 - CROISER LES INFOS:**
Combine etapes 1+2:
- B3 = Utilisateurs (col B) du Mardi (ligne 3)
- C2 = Friday (col C) du Lundi (ligne 2)

**ETAPE 4 - TRADUIRE ET EXECUTER:**
User: "Mets 60 Friday pour mardi"
→ Friday = colonne C, mardi = ligne 3 → C3=60

**REGLE:** L'utilisateur dit "utilisateurs de mardi", jamais "B3". TOI tu traduis!

═══════════════════════════════════════════════════════
REGLES: 1 donnee = 1 cellule | FORMULES (=SUM) pas de valeurs
═══════════════════════════════════════════════════════

{sheet_display}

COMMANDE: [MODIFY_CELLS:{{"file_id":"{document['id']}", "updates":[{{"cell":"A1", "value":"xxx"}}]}}]
GRAPHIQUE: [CREATE_CHART:{{"file_id":"{document['id']}", "type":"LINE/COLUMN/BAR/PIE", "range":"A1:C10", "title":"..."}}]
"""

            elif 'presentation' in mime_type:
                doc_type = 'Google Slides'
                doc_instructions = f"""Pour ajouter une diapositive:
[MODIFY_DOC:{{"file_id":"{document['id']}", "action":"append", "content":"Titre\\n\\nContenu"}}]"""

            else:
                doc_type = 'Google Doc'
                doc_content = drive_service.get_document_content(document['id'])
                print(f"[voice-chat] Google Doc content retrieved: {len(doc_content) if doc_content else 0} chars", flush=True)

                if not doc_content:
                    doc_content = "(Document vide)"

                doc_instructions = f"""
================================================================================
                        DOCUMENT A MODIFIER
================================================================================

{doc_content}

================================================================================
                        INSTRUCTIONS
================================================================================

Tu viens de lire le document EN ENTIER. Comprends sa structure et son contenu.

**REGLE ABSOLUE:**
NE MODIFIE RIEN D'AUTRE que ce que l'utilisateur demande!

**INTERDICTION:**
- JAMAIS "Je vais modifier..." (N'ANNONCE PAS, EXECUTE!)
- JAMAIS "C'est fait" sans commande (MENSONGE)

**COMMANDE:** [MODIFY_DOC:{{"file_id":"{document['id']}", "action":"replace", "content":"[DOC COMPLET]"}}]

**FORMAT:** Commande d'abord, message court apres.
[MODIFY_DOC:{{...}}]
C'est fait!
"""

            system_prompt = f"""Tu es Friday, assistant vocal conversationnel pour "{document['name']}" ({doc_type}).

{doc_instructions}

═══════════════════════════════════════════════════════
REGLES CRITIQUES
═══════════════════════════════════════════════════════

**#1 - DEMANDES MULTIPLES:**
Un message peut contenir PLUSIEURS demandes → TRAITE-LES TOUTES.
"Ajoute lundi, mardi, mercredi" → 3 ajouts dans ta reponse.
JAMAIS: "J'ai fait lundi, redis-moi pour mardi"

**#2 - EXECUTION IMMEDIATE:**
Demande = commande [...] dans ta reponse.
- "Ajoute" → [MODIFY_CELLS:...] ou [MODIFY_DOC:...]
- "Change" → [REPLACE_TEXT:...] ou [MODIFY_CELLS:...]
- "Supprime" → [DELETE_TEXT:...]

**#3 - INTERDICTION DE SIMULATION:**
JAMAIS confirmer sans [...] dans ta reponse.
"C'est fait" sans commande = MENSONGE

**#4 - TON CONVERSATIONNEL:**
Parle naturellement! Tu peux proposer des suites.
[MODIFY_CELLS:{{...}}]
Voila! Tu veux que j'ajoute autre chose?

**#5 - FORMULES (SHEETS):**
=SUM(), =AVERAGE() → JAMAIS de valeurs calculees.

**#6 - CONCISION:** Max 2 phrases apres commande.

═══════════════════════════════════════════════════════
CONVERSATIONNEL + EFFICACE. TOUTES LES DEMANDES.
═══════════════════════════════════════════════════════
"""

            # Call Gemini for document editing
            import google.generativeai as genai
            genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))

            gemini_model = genai.GenerativeModel('gemini-2.5-pro-preview-05-06')

            # Build conversation for Gemini
            gemini_history = []
            for msg in history[-20:]:
                role = "user" if msg["role"] == "user" else "model"
                gemini_history.append({"role": role, "parts": [msg["content"]]})

            chat = gemini_model.start_chat(history=gemini_history)

            # Send message with system prompt prepended
            full_prompt = f"{system_prompt}\n\n---\n\nUser: {user_message}"
            response = chat.send_message(full_prompt)

            ai_answer = response.text

            # Exécuter les modifications
            modification_result = None

            if '[MODIFY_CELLS:' in ai_answer:
                modification_result = execute_cells_modification(ai_answer, credentials)
                ai_answer = remove_command_from_answer(ai_answer, '[MODIFY_CELLS:')
            elif '[REPLACE_TEXT:' in ai_answer:
                modification_result = execute_replace_text(ai_answer, credentials)
                ai_answer = remove_command_from_answer(ai_answer, '[REPLACE_TEXT:')
            elif '[INSERT_AFTER:' in ai_answer:
                modification_result = execute_insert_after(ai_answer, credentials)
                ai_answer = remove_command_from_answer(ai_answer, '[INSERT_AFTER:')
            elif '[DELETE_TEXT:' in ai_answer:
                modification_result = execute_delete_text(ai_answer, credentials)
                ai_answer = remove_command_from_answer(ai_answer, '[DELETE_TEXT:')
            elif '[MODIFY_DOC:' in ai_answer:
                modification_result = execute_agent_modification(ai_answer, credentials)
                ai_answer = remove_command_from_answer(ai_answer, '[MODIFY_DOC:')
            elif '[MOVE_FILE:' in ai_answer:
                modification_result = execute_move_file(ai_answer, drive_service)
                ai_answer = remove_command_from_answer(ai_answer, '[MOVE_FILE:')
            elif '[CREATE_CHART:' in ai_answer:
                modification_result = execute_create_chart(ai_answer, drive_service)
                ai_answer = remove_command_from_answer(ai_answer, '[CREATE_CHART:')
            elif '[INSERT_TABLE:' in ai_answer:
                modification_result = execute_insert_table(ai_answer, drive_service)
                ai_answer = remove_command_from_answer(ai_answer, '[INSERT_TABLE:')
            elif '[FORMAT_RANGE:' in ai_answer:
                modification_result = execute_format_range(ai_answer, drive_service)
                ai_answer = remove_command_from_answer(ai_answer, '[FORMAT_RANGE:')

        print(f"[voice-chat] AI response: {ai_answer}", flush=True)

        # 3. TEXT-TO-SPEECH avec voix alloy
        tts_response = client.audio.speech.create(
            model="tts-1",
            voice="alloy",
            input=ai_answer,
            response_format="mp3"
        )

        audio_base64 = base64.b64encode(tts_response.content).decode('utf-8')

        return jsonify({
            'status': 'success',
            'transcription': user_message,
            'response': ai_answer,
            'audio': audio_base64,
            'modification_result': modification_result,
            'created_document': created_document
        })

    except Exception as e:
        import traceback
        print(f"[voice-chat] Error: {e}\n{traceback.format_exc()}", flush=True)
        return jsonify({'error': str(e)}), 500


@app.route('/voice-chat-kb', methods=['POST'])
def voice_chat_kb():
    """
    Endpoint pour conversation vocale avec la Knowledge Base
    Reçoit: audio (fichier) + historique
    Retourne: transcription + réponse RAG + audio de la réponse
    """
    if not is_authenticated():
        return jsonify({'error': 'Not authenticated'}), 401

    # Vérifier qu'on a un fichier audio
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400

    audio_file = request.files['audio']
    history_json = request.form.get('history', '[]')

    try:
        import json
        import base64
        import tempfile
        from openai import OpenAI

        history = json.loads(history_json)
        client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

        # 1. SPEECH-TO-TEXT avec Whisper
        with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as temp_audio:
            audio_file.save(temp_audio.name)
            temp_audio_path = temp_audio.name

        with open(temp_audio_path, 'rb') as audio:
            transcription = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio,
                language="fr"
            )

        user_message = transcription.text
        print(f"[voice-chat-kb] Transcription: {user_message}", flush=True)

        # Nettoyer le fichier temporaire
        os.unlink(temp_audio_path)

        # 2. Utiliser le RAG pour répondre
        rag = get_rag_engine()
        stats = rag.get_stats()

        if stats.get('status') == 'empty':
            ai_answer = "Aucun document n'est indexé. Veuillez d'abord ajouter des documents depuis votre Google Drive."
            sources = []
        else:
            result = rag.ask(user_message, history)
            ai_answer = result.get('answer', "Désolé, je n'ai pas trouvé de réponse.")
            sources = result.get('sources', [])

        print(f"[voice-chat-kb] AI response: {ai_answer[:100]}...", flush=True)

        # 3. TEXT-TO-SPEECH avec voix alloy
        tts_response = client.audio.speech.create(
            model="tts-1",
            voice="alloy",
            input=ai_answer,
            response_format="mp3"
        )

        audio_base64 = base64.b64encode(tts_response.content).decode('utf-8')

        return jsonify({
            'status': 'success',
            'transcription': user_message,
            'response': ai_answer,
            'sources': sources,
            'audio': audio_base64
        })

    except Exception as e:
        import traceback
        print(f"[voice-chat-kb] Error: {e}\n{traceback.format_exc()}", flush=True)
        return jsonify({'error': str(e)}), 500


# ============== Error Handlers ==============

@app.errorhandler(404)
def not_found(e):
    return render_template('index.html', error="Page not found"), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({'error': 'Internal server error'}), 500


# ============== Main ==============

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)
