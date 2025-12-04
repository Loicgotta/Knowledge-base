# Drive Knowledge Base - Assistant IA RAG

Un agent IA qui se connecte à votre Google Drive et répond à vos questions sur vos documents en utilisant la technologie RAG (Retrieval Augmented Generation).

## Fonctionnalités

- **Authentification Google OAuth** : Connexion sécurisée à Google Drive (lecture seule)
- **Support multi-formats** : PDF, Google Docs, Word, Excel, Google Sheets, texte, HTML, Markdown
- **Recherche intelligente RAG** : Embeddings OpenAI + ChromaDB pour des réponses précises
- **Interface conversationnelle** : Chat interactif avec historique de conversation
- **Déploiement Render** : Configuration prête pour le déploiement

## Architecture

```
├── app.py              # Application Flask principale
├── auth.py             # Service d'authentification Google OAuth
├── drive_service.py    # Service Google Drive (récupération documents)
├── rag_engine.py       # Moteur RAG (embeddings + recherche vectorielle)
├── templates/
│   └── index.html      # Interface utilisateur
├── requirements.txt    # Dépendances Python
├── render.yaml         # Configuration Render
└── Procfile           # Commande de démarrage
```

## Déploiement sur Render

### 1. Prérequis

- Compte [Render](https://render.com)
- Compte [Google Cloud Console](https://console.cloud.google.com)
- Clé API [OpenAI](https://platform.openai.com)

### 2. Configuration Google Cloud

1. Créez un projet sur Google Cloud Console
2. Activez l'API Google Drive
3. Créez des identifiants OAuth 2.0 (type: Application Web)
4. Ajoutez les URI de redirection autorisés :
   - `https://votre-app.onrender.com/oauth2callback`
   - `http://localhost:5000/oauth2callback` (pour le développement)

### 3. Déploiement Render

1. Connectez votre repo GitHub à Render
2. Créez un nouveau Web Service
3. Configurez les variables d'environnement :

| Variable | Description |
|----------|-------------|
| `GOOGLE_CLIENT_ID` | ID client OAuth Google |
| `GOOGLE_CLIENT_SECRET` | Secret client OAuth Google |
| `OPENAI_API_KEY` | Clé API OpenAI |
| `FLASK_SECRET_KEY` | Clé secrète Flask (auto-générée) |

4. Ajoutez un disque persistant pour ChromaDB (optionnel, 1GB)

### 4. Variables d'environnement

Créez un fichier `.env` pour le développement local :

```env
GOOGLE_CLIENT_ID=votre_client_id
GOOGLE_CLIENT_SECRET=votre_client_secret
OPENAI_API_KEY=votre_cle_openai
FLASK_SECRET_KEY=une_cle_secrete_longue
FLASK_ENV=development
```

## Développement local

```bash
# Installer les dépendances
pip install -r requirements.txt

# Lancer l'application
python app.py

# Ouvrir http://localhost:5000
```

## Utilisation

1. **Connexion** : Cliquez sur "Connexion avec Google" et autorisez l'accès en lecture à votre Drive
2. **Synchronisation** : Cliquez sur "Synchroniser" pour indexer vos documents
3. **Questions** : Posez vos questions dans le chat - l'IA cherchera dans vos documents

## Technologies

- **Backend** : Flask, Gunicorn
- **Authentification** : Google OAuth 2.0
- **Embeddings** : OpenAI text-embedding-3-small
- **LLM** : OpenAI GPT-4o-mini
- **Vector Store** : ChromaDB
- **Document Processing** : PyPDF, python-docx, openpyxl

## Sécurité

- Les tokens OAuth sont stockés en session (côté serveur)
- Les credentials ne sont jamais exposés côté client
- Accès en lecture seule au Drive
- Chaque utilisateur a une collection vectorielle isolée

## Licence

MIT
