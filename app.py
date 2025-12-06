"""
Drive Knowledge Base - AI Agent with RAG
Main Flask application for Google Drive document Q&A
"""

import os
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
    print("=== ADD DOCUMENTS START ===")

    if not is_authenticated():
        print("ERROR: Not authenticated")
        return jsonify({'error': 'Not authenticated'}), 401

    try:
        print("Step 1: Getting credentials...")
        credentials = get_valid_credentials()
        if not credentials:
            print("ERROR: Invalid credentials")
            return jsonify({'error': 'Invalid credentials'}), 401

        print("Step 2: Parsing request data...")
        data = request.json or {}
        items = data.get('items', [])  # List of {id, type, name}
        print(f"Items received: {len(items)}")

        if not items:
            print("ERROR: No items selected")
            return jsonify({'error': 'No items selected'}), 400

        print("Step 3: Creating DriveService...")
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

        print(f"Step 4: Processing {len(file_ids)} files and {len(folder_ids)} folders...")

        # Get documents from individual files
        if file_ids:
            print(f"Step 4a: Fetching {len(file_ids)} individual files...")
            docs = drive_service.get_documents_by_ids(file_ids)
            print(f"Got {len(docs)} documents from files")
            all_documents.extend(docs)

        # Get documents from folders (recursive)
        for folder_id in folder_ids:
            print(f"Step 4b: Fetching documents from folder {folder_id}...")
            docs = drive_service.get_all_documents(folder_id=folder_id)
            print(f"Got {len(docs)} documents from folder")
            all_documents.extend(docs)

        print(f"Step 5: Total documents to index: {len(all_documents)}")

        if not all_documents:
            print("WARNING: No documents found")
            return jsonify({
                'status': 'warning',
                'message': 'No documents found in selected items',
                'documents_found': 0
            })

        # Add to existing index (don't clear)
        print("Step 6: Getting RAG engine...")
        rag = get_rag_engine()

        print("Step 7: Adding documents to index...")
        result = rag.add_documents(all_documents)
        print(f"Step 8: Done! Result: {result}")

        return jsonify({
            'status': 'success',
            'message': f"Added {result['chunks_indexed']} chunks from {result['documents_processed']} documents",
            'documents_processed': result['documents_processed'],
            'chunks_indexed': result['chunks_indexed']
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
    """Chat endpoint for Q&A"""
    if not is_authenticated():
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.json
    if not data or 'message' not in data:
        return jsonify({'error': 'No message provided'}), 400

    message = data['message']
    history = data.get('history', [])

    try:
        rag = get_rag_engine()

        # Check if documents are indexed
        stats = rag.get_stats()
        if stats['total_chunks'] == 0:
            return jsonify({
                'answer': "Aucun document n'est indexé. Veuillez d'abord synchroniser vos documents Google Drive en cliquant sur 'Synchroniser'.",
                'sources': [],
                'needs_sync': True
            })

        # Get answer using RAG
        result = rag.ask(message, history)

        return jsonify({
            'answer': result['answer'],
            'sources': result['sources'],
            'chunks_used': result.get('chunks_used', 0)
        })

    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"Chat error: {e}\n{error_trace}")
        return jsonify({
            'error': str(e),
            'error_type': type(e).__name__,
            'traceback': error_trace
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
