"""
RAG Engine
Retrieval Augmented Generation engine using OpenAI embeddings and ChromaDB
"""

import os
import hashlib
import time
from typing import List, Dict, Optional
from openai import OpenAI
import chromadb
import tiktoken


class RAGEngine:
    """RAG Engine for document retrieval and question answering"""

    def __init__(self, user_id: str):
        """
        Initialize RAG Engine for a specific user

        Args:
            user_id: Unique identifier for the user (for isolated collections)
        """
        self.user_id = user_id
        self.collection_name = f"docs_{self._hash_user_id(user_id)}"
        self.persist_path = os.environ.get("CHROMA_PERSIST_DIR", "./chroma_data")

        # Initialize OpenAI client
        self.openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

        # Initialize ChromaDB
        self._init_chroma()

        # Tokenizer for text splitting
        self.tokenizer = tiktoken.get_encoding("cl100k_base")

        # Configuration
        self.chunk_size = 500  # tokens
        self.chunk_overlap = 50  # tokens
        self.embedding_model = "text-embedding-3-small"
        self.chat_model = "gpt-4o-mini"
        self.max_context_chunks = 5

    def _init_chroma(self):
        """Initialize or reinitialize ChromaDB connection"""
        self.chroma_client = chromadb.PersistentClient(path=self.persist_path)
        self.collection = self.chroma_client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"}
        )

    def _ensure_connection(self):
        """Ensure ChromaDB connection is valid, reconnect if needed"""
        try:
            # Test connection by getting collection count
            self.collection.count()
        except Exception as e:
            print(f"ChromaDB connection lost, reconnecting: {e}")
            self._init_chroma()

    def _hash_user_id(self, user_id: str) -> str:
        """Create a short hash of user ID for collection naming"""
        return hashlib.md5(user_id.encode()).hexdigest()[:12]

    def _count_tokens(self, text: str) -> int:
        """Count tokens in text"""
        return len(self.tokenizer.encode(text))

    def _split_text(self, text: str, doc_name: str) -> List[Dict]:
        """
        Split text into chunks with metadata

        Args:
            text: The text to split
            doc_name: Name of the source document

        Returns:
            List of chunk dictionaries
        """
        tokens = self.tokenizer.encode(text)
        chunks = []

        start = 0
        chunk_idx = 0

        while start < len(tokens):
            end = start + self.chunk_size

            # Get chunk tokens
            chunk_tokens = tokens[start:end]
            chunk_text = self.tokenizer.decode(chunk_tokens)

            chunks.append({
                'text': chunk_text,
                'doc_name': doc_name,
                'chunk_index': chunk_idx
            })

            # Move start position with overlap
            start = end - self.chunk_overlap
            chunk_idx += 1

        return chunks

    def _get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Get embeddings for a list of texts

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        # Process in batches to avoid API limits
        batch_size = 100
        all_embeddings = []
        total_batches = (len(texts) + batch_size - 1) // batch_size

        print(f"[_get_embeddings] Starting: {len(texts)} texts in {total_batches} batches")

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_num = i // batch_size + 1
            print(f"[_get_embeddings] Processing batch {batch_num}/{total_batches} ({len(batch)} texts)...")

            try:
                response = self.openai_client.embeddings.create(
                    model=self.embedding_model,
                    input=batch
                )
                batch_embeddings = [item.embedding for item in response.data]
                all_embeddings.extend(batch_embeddings)
                print(f"[_get_embeddings] Batch {batch_num} done")
            except Exception as e:
                print(f"[_get_embeddings] ERROR in batch {batch_num}: {e}")
                raise

        print(f"[_get_embeddings] All embeddings generated: {len(all_embeddings)}")
        return all_embeddings

    def index_documents(self, documents: List[Dict], clear_existing: bool = True) -> Dict:
        """
        Index documents into the vector store

        Args:
            documents: List of documents with 'name' and 'content' keys
            clear_existing: If True, clear existing index before adding

        Returns:
            Indexing statistics
        """
        # Ensure connection is valid before starting
        self._ensure_connection()

        all_chunks = []
        all_ids = []
        all_metadatas = []

        for doc in documents:
            doc_name = doc['name']
            content = doc['content']
            doc_id = doc.get('id', hashlib.md5(doc_name.encode()).hexdigest())

            # Split document into chunks
            chunks = self._split_text(content, doc_name)

            for chunk in chunks:
                chunk_id = f"{doc_id}_{chunk['chunk_index']}"
                all_chunks.append(chunk['text'])
                all_ids.append(chunk_id)
                all_metadatas.append({
                    'doc_name': chunk['doc_name'],
                    'doc_id': doc_id,
                    'chunk_index': chunk['chunk_index']
                })

        if not all_chunks:
            return {'status': 'empty', 'chunks_indexed': 0}

        # Reinitialize ChromaDB to ensure fresh connection
        self._init_chroma()

        if clear_existing:
            # Clear existing collection for fresh index
            try:
                self.chroma_client.delete_collection(self.collection_name)
            except:
                pass

            self.collection = self.chroma_client.create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
        else:
            # Ensure we have a valid collection reference for incremental adds
            self.collection = self.chroma_client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}
            )

        # Get embeddings for all chunks
        print(f"Generating embeddings for {len(all_chunks)} chunks...")
        embeddings = self._get_embeddings(all_chunks)

        # Add to collection with retry logic
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.collection.upsert(
                    ids=all_ids,
                    embeddings=embeddings,
                    documents=all_chunks,
                    metadatas=all_metadatas
                )
                break
            except Exception as e:
                print(f"Upsert attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                    self._init_chroma()
                    self.collection = self.chroma_client.get_or_create_collection(
                        name=self.collection_name,
                        metadata={"hnsw:space": "cosine"}
                    )
                else:
                    raise

        return {
            'status': 'success',
            'documents_processed': len(documents),
            'chunks_indexed': len(all_chunks)
        }

    def add_documents(self, documents: List[Dict]) -> Dict:
        """
        Add documents to existing index without clearing

        Args:
            documents: List of documents with 'name' and 'content' keys

        Returns:
            Indexing statistics
        """
        return self.index_documents(documents, clear_existing=False)

    def get_indexed_documents(self) -> List[str]:
        """Get list of indexed document names"""
        try:
            # Ensure connection is valid
            self._ensure_connection()
            # Get all metadatas from collection
            results = self.collection.get(include=['metadatas'])
            doc_names = set()
            if results['metadatas']:
                for meta in results['metadatas']:
                    if meta.get('doc_name'):
                        doc_names.add(meta['doc_name'])
            return list(doc_names)
        except Exception as e:
            print(f"Error getting indexed documents: {e}")
            # Try to reconnect and retry once
            try:
                self._init_chroma()
                results = self.collection.get(include=['metadatas'])
                doc_names = set()
                if results['metadatas']:
                    for meta in results['metadatas']:
                        if meta.get('doc_name'):
                            doc_names.add(meta['doc_name'])
                return list(doc_names)
            except:
                return []

    def search(self, query: str, n_results: int = 5) -> List[Dict]:
        """
        Search for relevant chunks

        Args:
            query: Search query
            n_results: Number of results to return

        Returns:
            List of relevant chunks with metadata
        """
        # Ensure connection is valid
        self._ensure_connection()

        # Get query embedding
        query_embedding = self._get_embeddings([query])[0]

        # Search collection with retry
        try:
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                include=['documents', 'metadatas', 'distances']
            )
        except Exception as e:
            print(f"Search failed, retrying: {e}")
            self._init_chroma()
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                include=['documents', 'metadatas', 'distances']
            )

        # Format results
        formatted_results = []
        if results['documents'] and results['documents'][0]:
            for i, doc in enumerate(results['documents'][0]):
                formatted_results.append({
                    'content': doc,
                    'metadata': results['metadatas'][0][i] if results['metadatas'] else {},
                    'distance': results['distances'][0][i] if results['distances'] else 0
                })

        return formatted_results

    def ask(self, question: str, conversation_history: List[Dict] = None) -> Dict:
        """
        Answer a question using RAG

        Args:
            question: The user's question
            conversation_history: Optional list of previous messages

        Returns:
            Response with answer and sources
        """
        # Search for relevant context
        relevant_chunks = self.search(question, n_results=self.max_context_chunks)

        if not relevant_chunks:
            return {
                'answer': "Je n'ai pas trouvé d'informations pertinentes dans vos documents pour répondre à cette question. Assurez-vous que vos documents Drive ont été indexés.",
                'sources': [],
                'context_used': False
            }

        # Build context from relevant chunks
        context_parts = []
        sources = set()

        for chunk in relevant_chunks:
            context_parts.append(chunk['content'])
            if chunk['metadata'].get('doc_name'):
                sources.add(chunk['metadata']['doc_name'])

        context = "\n\n---\n\n".join(context_parts)

        # Build messages for chat
        system_message = """Tu es un assistant IA expert qui répond aux questions en te basant sur les documents fournis.

Instructions:
- Réponds en te basant UNIQUEMENT sur le contexte fourni
- Si l'information n'est pas dans le contexte, dis-le clairement
- Cite les sources quand c'est pertinent
- Réponds dans la même langue que la question (français ou anglais)
- Sois précis et utile
- Tu peux faire des analyses, comparaisons et synthèses basées sur les documents"""

        messages = [{"role": "system", "content": system_message}]

        # Add conversation history if provided
        if conversation_history:
            for msg in conversation_history[-6:]:  # Keep last 6 messages for context
                messages.append(msg)

        # Add current question with context
        user_message = f"""Contexte des documents:
{context}

Question: {question}"""

        messages.append({"role": "user", "content": user_message})

        # Get response from OpenAI
        response = self.openai_client.chat.completions.create(
            model=self.chat_model,
            messages=messages,
            temperature=0.7,
            max_tokens=2000
        )

        answer = response.choices[0].message.content

        return {
            'answer': answer,
            'sources': list(sources),
            'context_used': True,
            'chunks_used': len(relevant_chunks)
        }

    def get_stats(self) -> Dict:
        """Get statistics about the indexed documents"""
        try:
            self._ensure_connection()
            count = self.collection.count()
            return {
                'total_chunks': count,
                'collection_name': self.collection_name,
                'status': 'ready' if count > 0 else 'empty'
            }
        except Exception as e:
            # Try to reconnect
            try:
                self._init_chroma()
                count = self.collection.count()
                return {
                    'total_chunks': count,
                    'collection_name': self.collection_name,
                    'status': 'ready' if count > 0 else 'empty'
                }
            except:
                return {
                    'total_chunks': 0,
                    'status': 'error',
                    'error': str(e)
                }

    def clear_index(self) -> bool:
        """Clear all indexed documents for this user"""
        try:
            self._init_chroma()  # Ensure fresh connection
            self.chroma_client.delete_collection(self.collection_name)
            self.collection = self.chroma_client.create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
            return True
        except Exception as e:
            print(f"Error clearing index: {e}")
            return False
