# Guide d'Intégration - Knowledge Base (RAG)

Un système de questions-réponses intelligent qui indexe vos documents Google Drive et répond à toutes vos questions sur leur contenu.

---

## Comment ça marche ?

```
1. L'utilisateur connecte son Google Drive
2. Il sélectionne un dossier à indexer
3. Les documents sont découpés et convertis en vecteurs
4. Il pose des questions en langage naturel
5. L'IA trouve les passages pertinents et répond
```

### Flux simplifié

```
              INDEXATION (une fois)
              ════════════════════
Google Drive ──▶ Documents ──▶ Découpage ──▶ Embeddings ──▶ ChromaDB
                 (PDF, Doc,    (chunks de     (vecteurs      (stockage)
                  Sheets...)   500 tokens)    1536-dim)

              QUESTION-RÉPONSE (à chaque question)
              ════════════════════════════════════
Question ──▶ Embedding ──▶ Recherche ──▶ Top 5 chunks ──▶ GPT-4 ──▶ Réponse
             (vecteur)     ChromaDB     pertinents       + contexte
```

---

## Ce dont vous avez besoin

| Service | Rôle |
|---------|------|
| **Google Cloud** | OAuth + accès Drive |
| **OpenAI** | Embeddings (`text-embedding-3-small`) + Chat (`gpt-4.1-mini`) |
| **ChromaDB** | Base de données vectorielle (locale) |

```bash
pip install google-auth google-auth-oauthlib google-api-python-client \
            openai chromadb tiktoken flask
```

---

## Les 4 composants à intégrer

### 1. DriveService - Récupération des documents

**But** : Lire tous les documents d'un dossier Google Drive.

**Formats supportés** : PDF, Google Docs, Sheets, Slides, Word, Excel, PowerPoint, TXT, CSV, JSON, HTML, Markdown... (65+ formats)

```python
from googleapiclient.discovery import build

class DriveService:
    def __init__(self, credentials):
        self.drive = build('drive', 'v3', credentials=credentials)
        self.docs = build('docs', 'v1', credentials=credentials)
        self.sheets = build('sheets', 'v4', credentials=credentials)

    def get_all_documents(self, folder_id):
        """Récupère tous les fichiers d'un dossier (récursif)"""
        documents = []
        results = self.drive.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="files(id, name, mimeType)"
        ).execute()

        for file in results.get('files', []):
            if file['mimeType'] == 'application/vnd.google-apps.folder':
                # Récursion dans les sous-dossiers
                documents.extend(self.get_all_documents(file['id']))
            else:
                # Extraire le contenu texte
                content = self.get_file_content(file['id'], file['mimeType'])
                if content:
                    documents.append({
                        'id': file['id'],
                        'name': file['name'],
                        'content': content
                    })
        return documents

    def get_file_content(self, file_id, mime_type):
        """Convertit n'importe quel fichier en texte"""
        if 'google-apps.document' in mime_type:
            return self._export_google_doc(file_id)
        elif 'google-apps.spreadsheet' in mime_type:
            return self._export_google_sheet(file_id)
        elif mime_type == 'application/pdf':
            return self._parse_pdf(file_id)
        # ... autres formats
```

---

### 2. RAGEngine - Indexation et recherche

**But** : Découper les documents, créer les embeddings, et chercher les passages pertinents.

```python
import chromadb
from openai import OpenAI
import tiktoken

class RAGEngine:
    def __init__(self, user_id):
        self.client = OpenAI()
        self.tokenizer = tiktoken.get_encoding("cl100k_base")

        # ChromaDB - une collection par utilisateur
        self.chroma = chromadb.PersistentClient(path="./chroma_data")
        self.collection = self.chroma.get_or_create_collection(
            name=f"docs_{hash(user_id)}",
            metadata={"hnsw:space": "cosine"}
        )

    # ═══════════════════════════════════════════
    # ÉTAPE 1 : DÉCOUPAGE EN CHUNKS
    # ═══════════════════════════════════════════

    def _split_text(self, text, doc_name, doc_id):
        """Découpe le texte en morceaux de 500 tokens avec chevauchement"""
        CHUNK_SIZE = 500
        OVERLAP = 50

        tokens = self.tokenizer.encode(text)
        chunks = []

        for i in range(0, len(tokens), CHUNK_SIZE - OVERLAP):
            chunk_tokens = tokens[i:i + CHUNK_SIZE]
            chunk_text = self.tokenizer.decode(chunk_tokens)

            chunks.append({
                'text': chunk_text,
                'metadata': {
                    'doc_name': doc_name,
                    'doc_id': doc_id,
                    'chunk_index': len(chunks)
                }
            })

        return chunks

    # ═══════════════════════════════════════════
    # ÉTAPE 2 : CRÉATION DES EMBEDDINGS
    # ═══════════════════════════════════════════

    def _get_embeddings(self, texts):
        """Convertit une liste de textes en vecteurs 1536-dim"""
        response = self.client.embeddings.create(
            model="text-embedding-3-small",
            input=texts
        )
        return [item.embedding for item in response.data]

    # ═══════════════════════════════════════════
    # ÉTAPE 3 : INDEXATION DANS CHROMADB
    # ═══════════════════════════════════════════

    def index_documents(self, documents, clear_existing=True):
        """Indexe une liste de documents"""
        if clear_existing:
            # Vider la collection existante
            self.chroma.delete_collection(self.collection.name)
            self.collection = self.chroma.create_collection(
                name=self.collection.name,
                metadata={"hnsw:space": "cosine"}
            )

        all_chunks = []
        for doc in documents:
            chunks = self._split_text(doc['content'], doc['name'], doc['id'])
            all_chunks.extend(chunks)

        # Créer les embeddings par batch
        texts = [c['text'] for c in all_chunks]
        embeddings = self._get_embeddings(texts)

        # Stocker dans ChromaDB
        self.collection.add(
            ids=[f"{c['metadata']['doc_id']}_{c['metadata']['chunk_index']}" for c in all_chunks],
            embeddings=embeddings,
            documents=texts,
            metadatas=[c['metadata'] for c in all_chunks]
        )

        return {'indexed': len(documents), 'chunks': len(all_chunks)}

    # ═══════════════════════════════════════════
    # ÉTAPE 4 : RECHERCHE SÉMANTIQUE
    # ═══════════════════════════════════════════

    def search(self, query, n_results=5):
        """Trouve les chunks les plus pertinents pour une question"""
        # Embedding de la question
        query_embedding = self._get_embeddings([query])[0]

        # Recherche par similarité cosinus
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results
        )

        return [
            {
                'content': results['documents'][0][i],
                'doc_name': results['metadatas'][0][i]['doc_name'],
                'distance': results['distances'][0][i]
            }
            for i in range(len(results['documents'][0]))
        ]

    # ═══════════════════════════════════════════
    # ÉTAPE 5 : GÉNÉRATION DE RÉPONSE
    # ═══════════════════════════════════════════

    def ask(self, question, conversation_history=[]):
        """Répond à une question en utilisant le contexte des documents"""

        # 1. Trouver les passages pertinents
        relevant_chunks = self.search(question, n_results=5)

        # 2. Construire le contexte
        context = "\n\n---\n\n".join([
            f"[Source: {c['doc_name']}]\n{c['content']}"
            for c in relevant_chunks
        ])

        # 3. Prompt système
        system_prompt = f"""Tu es un assistant qui répond aux questions en te basant UNIQUEMENT sur les documents fournis.

DOCUMENTS DISPONIBLES:
{context}

RÈGLES:
- Réponds uniquement avec les informations des documents ci-dessus
- Si l'information n'est pas dans les documents, dis-le clairement
- Cite les sources (noms des documents) dans ta réponse
"""

        # 4. Appel à GPT-4
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(conversation_history[-6:])  # Garder les 6 derniers messages
        messages.append({"role": "user", "content": question})

        response = self.client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
            temperature=0.7,
            max_tokens=2000
        )

        return {
            'answer': response.choices[0].message.content,
            'sources': list(set(c['doc_name'] for c in relevant_chunks)),
            'chunks_used': len(relevant_chunks)
        }
```

---

### 3. Endpoints API

```python
from flask import Flask, request, jsonify, session

app = Flask(__name__)

# ═══════════════════════════════════════════
# GESTION DES DOSSIERS
# ═══════════════════════════════════════════

@app.route('/folders', methods=['GET'])
def list_folders():
    """Liste tous les dossiers Google Drive"""
    drive = DriveService(session['credentials'])
    folders = drive.list_folders()
    return jsonify(folders)

@app.route('/select-folder', methods=['POST'])
def select_folder():
    """Sélectionne un dossier à indexer"""
    folder_id = request.json['folder_id']
    session['selected_folder'] = folder_id
    return jsonify({'status': 'ok'})

# ═══════════════════════════════════════════
# INDEXATION
# ═══════════════════════════════════════════

@app.route('/sync', methods=['POST'])
def sync_documents():
    """Indexe tous les documents du dossier sélectionné"""
    drive = DriveService(session['credentials'])
    rag = RAGEngine(session['user_id'])

    # 1. Récupérer les documents
    documents = drive.get_all_documents(session['selected_folder'])

    # 2. Indexer
    result = rag.index_documents(documents, clear_existing=True)

    return jsonify({
        'message': f"{result['indexed']} documents indexés ({result['chunks']} chunks)"
    })

@app.route('/indexed-documents', methods=['GET'])
def get_indexed_documents():
    """Liste les documents actuellement indexés"""
    rag = RAGEngine(session['user_id'])
    docs = rag.get_indexed_documents()
    return jsonify(docs)

# ═══════════════════════════════════════════
# QUESTIONS-RÉPONSES
# ═══════════════════════════════════════════

@app.route('/chat', methods=['POST'])
def chat():
    """Pose une question sur les documents"""
    question = request.json['message']
    history = request.json.get('history', [])

    rag = RAGEngine(session['user_id'])
    result = rag.ask(question, history)

    return jsonify({
        'answer': result['answer'],
        'sources': result['sources'],
        'chunks_used': result['chunks_used']
    })

# ═══════════════════════════════════════════
# UTILITAIRES
# ═══════════════════════════════════════════

@app.route('/stats', methods=['GET'])
def get_stats():
    """Statistiques de l'index"""
    rag = RAGEngine(session['user_id'])
    return jsonify(rag.get_stats())

@app.route('/clear', methods=['POST'])
def clear_index():
    """Vide l'index"""
    rag = RAGEngine(session['user_id'])
    rag.clear_index()
    return jsonify({'status': 'cleared'})
```

---

### 4. Configuration

```bash
# .env

# Google OAuth
GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=xxx

# OpenAI
OPENAI_API_KEY=sk-xxx

# ChromaDB (optionnel - par défaut ./chroma_data)
CHROMA_PERSIST_DIR=./chroma_data

# Flask
FLASK_SECRET_KEY=une-cle-secrete
```

---

## Comprendre le RAG

### Pourquoi découper en chunks ?

```
Document complet (10 000 mots)
         │
         │  Trop long pour:
         │  - Les embeddings (limite de tokens)
         │  - Le contexte GPT-4 (coût + dilution)
         │
         ▼
    ┌─────────┬─────────┬─────────┬─────────┐
    │ Chunk 1 │ Chunk 2 │ Chunk 3 │ Chunk 4 │  (500 tokens chacun)
    └─────────┴─────────┴─────────┴─────────┘
         │
         │  Avantages:
         │  - Recherche précise (passage exact)
         │  - Contexte pertinent (pas de bruit)
         │  - Économie de tokens
         │
         ▼
    Question: "Quelle est la politique de remboursement?"
         │
         ▼
    Seul le chunk 3 (qui parle de remboursement) est envoyé à GPT-4
```

### Pourquoi un chevauchement (overlap) ?

```
Sans overlap:
┌──────────────────┐┌──────────────────┐
│ ...fin de phrase ││ début de phrase...│
└──────────────────┘└──────────────────┘
        ↑                    ↑
        Coupure brutale = perte de sens

Avec overlap (50 tokens):
┌──────────────────────┐
│ ...fin de phrase qui │
│ continue ici...      │
└──────────────────────┘
         ┌──────────────────────┐
         │ phrase qui continue  │
         │ ici et après...      │
         └──────────────────────┘
              ↑
              Contexte préservé
```

### Similarité cosinus

```
Embedding de la question:  [0.2, 0.8, 0.1, ...]  (1536 dimensions)
Embedding du chunk 1:      [0.1, 0.7, 0.2, ...]  → distance: 0.15 (proche!)
Embedding du chunk 2:      [0.9, 0.1, 0.3, ...]  → distance: 0.85 (loin)
Embedding du chunk 3:      [0.3, 0.9, 0.1, ...]  → distance: 0.12 (très proche!)

→ Chunks 3 et 1 sont retournés car sémantiquement similaires à la question
```

---

## Formats de fichiers supportés

| Catégorie | Formats |
|-----------|---------|
| **Google** | Docs, Sheets, Slides |
| **Microsoft** | Word (.docx), Excel (.xlsx), PowerPoint (.pptx) |
| **Documents** | PDF, TXT, RTF |
| **Web** | HTML, XML, Markdown |
| **Data** | CSV, JSON, YAML |
| **Code** | Python, JavaScript, etc. |

---

## Résumé des endpoints

| Endpoint | Méthode | Description |
|----------|---------|-------------|
| `/folders` | GET | Liste les dossiers Drive |
| `/select-folder` | POST | Sélectionne un dossier |
| `/sync` | POST | Indexe les documents |
| `/indexed-documents` | GET | Liste les docs indexés |
| `/chat` | POST | Pose une question |
| `/stats` | GET | Statistiques de l'index |
| `/clear` | POST | Vide l'index |

---

## Paramètres clés

| Paramètre | Valeur | Explication |
|-----------|--------|-------------|
| **Chunk size** | 500 tokens | Taille optimale pour la recherche |
| **Overlap** | 50 tokens | Préserve le contexte entre chunks |
| **n_results** | 5 | Nombre de chunks retournés |
| **Embedding model** | text-embedding-3-small | Rapide et économique |
| **Chat model** | gpt-4.1-mini | Bon rapport qualité/prix |
| **Temperature** | 0.7 | Créativité modérée |
| **History** | 6 messages | Mémoire de conversation |

---

## Liens utiles

- [OpenAI Embeddings](https://platform.openai.com/docs/guides/embeddings)
- [ChromaDB Documentation](https://docs.trychroma.com/)
- [Google Drive API](https://developers.google.com/drive/api)
- [tiktoken (tokenizer)](https://github.com/openai/tiktoken)
