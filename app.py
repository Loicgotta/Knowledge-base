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


@app.route('/chat-edit', methods=['POST'])
def chat_edit():
    """Chat endpoint for editing a specific pre-selected document"""
    if not is_authenticated():
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.json
    if not data or 'message' not in data or 'document' not in data:
        return jsonify({'error': 'Missing message or document'}), 400

    message = data['message']
    document = data['document']  # {id, name, mimeType}
    history = data.get('history', [])

    try:
        credentials = get_valid_credentials()
        if not credentials:
            return jsonify({'error': 'Invalid credentials'}), 401

        drive_service = DriveService(credentials)
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
                sheet_display = "CONTENU ACTUEL DE LA FEUILLE:\n"
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

            doc_instructions = f"""{sheet_display}

Pour modifier des cellules specifiques, utilise la commande:
[MODIFY_CELLS:{{"file_id":"{document['id']}", "updates":[{{"cell":"A1", "value":"nouvelle valeur"}}, {{"cell":"B2", "value":"autre valeur"}}]}}]

Pour ajouter des lignes a la fin:
[MODIFY_DOC:{{"file_id":"{document['id']}", "action":"append", "content":"col1\\tcol2\\nval1\\tval2"}}]

EXEMPLES:
- "Change la cellule B3" -> [MODIFY_CELLS:{{"file_id":"...", "updates":[{{"cell":"B3", "value":"nouveau"}}]}}]
- "Mets 100 dans C5" -> [MODIFY_CELLS:{{"file_id":"...", "updates":[{{"cell":"C5", "value":"100"}}]}}]
- "Ajoute une ligne avec Jean, 25, Paris" -> [MODIFY_DOC:{{"file_id":"...", "action":"append", "content":"Jean\\t25\\tParis"}}]"""

        elif 'presentation' in mime_type:
            doc_type = 'Google Slides'
            doc_instructions = f"""Pour ajouter une diapositive:
[MODIFY_DOC:{{"file_id":"{document['id']}", "action":"append", "content":"Titre\\n\\nContenu de la slide"}}]"""

        else:
            doc_type = 'Google Doc'
            doc_instructions = f"""Pour ajouter du contenu a la fin:
[MODIFY_DOC:{{"file_id":"{document['id']}", "action":"append", "content":"Le texte a ajouter"}}]

Pour remplacer tout le contenu:
[MODIFY_DOC:{{"file_id":"{document['id']}", "action":"replace", "content":"Le nouveau contenu"}}]"""

        # Build system prompt
        system_prompt = f"""Tu es un assistant vocal qui modifie le document "{document['name']}" ({doc_type}).

{doc_instructions}

REGLES IMPORTANTES:
1. Fais le travail IMMEDIATEMENT en ajoutant la commande a la fin
2. APRES avoir fait le travail, dis SIMPLEMENT que c'est fait (ex: "C'est fait", "Voila", "J'ai mis a jour")
3. NE DIS JAMAIS "je vais faire" ou "je vais ajouter" - FAIS-LE d'abord puis confirme
4. Reponses COURTES (1-2 phrases max) car elles seront lues a voix haute
5. Parle naturellement comme a l'oral

EXEMPLES DE BONNES REPONSES:
- "C'est fait, j'ai ajoute la section conclusion."
- "Voila, les donnees sont mises a jour."
- "OK, c'est ajoute."

MAUVAIS (ne fais pas ca):
- "Je vais ajouter une section conclusion..." (NON - fais-le d'abord!)
- "Bien sur, je vais mettre a jour..." (NON - trop long!)"""

        # Call OpenAI
        from openai import OpenAI
        openai_client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

        messages = [{"role": "system", "content": system_prompt}]

        # Add history
        for msg in history[-6:]:
            messages.append(msg)

        messages.append({"role": "user", "content": message})

        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
            temperature=0.7,
            max_tokens=2000
        )

        answer = response.choices[0].message.content

        # Check for modification commands
        modification_result = None

        # Handle cell-specific updates for Sheets
        if '[MODIFY_CELLS:' in answer:
            modification_result = execute_cells_modification(answer, credentials)
            answer = remove_command_from_answer(answer, '[MODIFY_CELLS:')

        # Handle document modifications (append/replace)
        elif '[MODIFY_DOC:' in answer:
            modification_result = execute_agent_modification(answer, credentials)
            answer = remove_command_from_answer(answer, '[MODIFY_DOC:')

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
