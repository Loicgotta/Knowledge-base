"""
Google Drive Service
Handles fetching and processing documents from Google Drive
"""

import io
import os
from typing import List, Dict, Optional
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.errors import HttpError

# Document processing
from pypdf import PdfReader
from docx import Document as DocxDocument
from bs4 import BeautifulSoup
import openpyxl


# Supported MIME types for processing
SUPPORTED_MIME_TYPES = {
    'application/pdf': 'pdf',
    'application/vnd.google-apps.document': 'gdoc',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
    'application/vnd.google-apps.spreadsheet': 'gsheet',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
    'text/plain': 'txt',
    'text/html': 'html',
    'text/markdown': 'md',
    'application/vnd.google-apps.presentation': 'gslides',
}

# Export formats for Google Workspace documents
EXPORT_MIME_TYPES = {
    'application/vnd.google-apps.document': 'text/plain',
    'application/vnd.google-apps.spreadsheet': 'text/csv',
    'application/vnd.google-apps.presentation': 'text/plain',
}


class DriveService:
    """Service for interacting with Google Drive API"""

    def __init__(self, credentials):
        """Initialize Drive service with credentials"""
        self.service = build('drive', 'v3', credentials=credentials)
        self.credentials = credentials

    def list_folders(self) -> List[Dict]:
        """
        List all folders from Google Drive

        Returns:
            List of folder metadata dictionaries
        """
        all_folders = []
        page_token = None

        try:
            while True:
                results = self.service.files().list(
                    pageSize=100,
                    fields="nextPageToken, files(id, name, mimeType)",
                    q="mimeType='application/vnd.google-apps.folder' and trashed=false",
                    pageToken=page_token
                ).execute()

                folders = results.get('files', [])
                all_folders.extend(folders)

                page_token = results.get('nextPageToken')
                if not page_token:
                    break

            return all_folders

        except HttpError as error:
            print(f"Error listing folders: {error}")
            return []

    def list_folder_contents(self, folder_id: Optional[str] = None) -> List[Dict]:
        """
        List contents of a folder (files and subfolders)

        Args:
            folder_id: Folder ID to list contents from. If None, lists root.

        Returns:
            List of file/folder metadata with type indicator
        """
        all_items = []
        page_token = None

        if folder_id:
            query = f"'{folder_id}' in parents and trashed=false"
        else:
            query = "'root' in parents and trashed=false"

        try:
            while True:
                results = self.service.files().list(
                    pageSize=100,
                    fields="nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                    q=query,
                    orderBy="folder,name",
                    pageToken=page_token
                ).execute()

                items = results.get('files', [])
                for item in items:
                    is_folder = item['mimeType'] == 'application/vnd.google-apps.folder'
                    is_supported = item['mimeType'] in SUPPORTED_MIME_TYPES

                    if is_folder or is_supported:
                        all_items.append({
                            'id': item['id'],
                            'name': item['name'],
                            'mimeType': item['mimeType'],
                            'type': 'folder' if is_folder else 'file',
                            'modifiedTime': item.get('modifiedTime', ''),
                            'size': item.get('size', 0)
                        })

                page_token = results.get('nextPageToken')
                if not page_token:
                    break

            return all_items

        except HttpError as error:
            print(f"Error listing folder contents: {error}")
            return []

    def get_documents_by_ids(self, file_ids: List[str]) -> List[Dict]:
        """
        Fetch specific documents by their IDs

        Args:
            file_ids: List of file IDs to fetch

        Returns:
            List of documents with metadata and content
        """
        documents = []

        for file_id in file_ids:
            try:
                # Get file metadata
                file_meta = self.service.files().get(
                    fileId=file_id,
                    fields="id, name, mimeType, modifiedTime"
                ).execute()

                mime_type = file_meta['mimeType']

                # Skip folders
                if mime_type == 'application/vnd.google-apps.folder':
                    continue

                # Skip unsupported types
                if mime_type not in SUPPORTED_MIME_TYPES:
                    continue

                file_name = file_meta['name']
                print(f"Processing: {file_name}")

                content = self.get_file_content(file_id, mime_type)

                if content and content.strip():
                    documents.append({
                        'id': file_id,
                        'name': file_name,
                        'mime_type': mime_type,
                        'content': content,
                        'modified_time': file_meta.get('modifiedTime', '')
                    })

            except HttpError as error:
                print(f"Error fetching file {file_id}: {error}")
                continue

        return documents

    def list_files(self, page_size: int = 100, folder_id: Optional[str] = None) -> List[Dict]:
        """
        List files from Google Drive

        Args:
            page_size: Number of files to fetch per page
            folder_id: Optional folder ID to list files from

        Returns:
            List of file metadata dictionaries
        """
        all_files = []
        page_token = None

        # Build query for supported file types
        mime_conditions = " or ".join([f"mimeType='{mime}'" for mime in SUPPORTED_MIME_TYPES.keys()])

        if folder_id:
            query = f"'{folder_id}' in parents and trashed=false"
        else:
            query = f"({mime_conditions}) and trashed=false"

        try:
            while True:
                results = self.service.files().list(
                    pageSize=page_size,
                    fields="nextPageToken, files(id, name, mimeType, modifiedTime, size, parents)",
                    q=query,
                    pageToken=page_token
                ).execute()

                files = results.get('files', [])
                all_files.extend(files)

                page_token = results.get('nextPageToken')
                if not page_token:
                    break

            return all_files

        except HttpError as error:
            print(f"Error listing files: {error}")
            return []

    def list_files_recursive(self, folder_id: str) -> List[Dict]:
        """
        Recursively list all files in a folder and its subfolders

        Args:
            folder_id: The folder ID to start from

        Returns:
            List of file metadata dictionaries
        """
        all_files = []

        def process_folder(fid: str):
            items = self.list_files(folder_id=fid)
            for item in items:
                if item['mimeType'] == 'application/vnd.google-apps.folder':
                    # Recursively process subfolder
                    process_folder(item['id'])
                elif item['mimeType'] in SUPPORTED_MIME_TYPES:
                    all_files.append(item)

        process_folder(folder_id)
        return all_files

    def get_file_content(self, file_id: str, mime_type: str) -> Optional[str]:
        """
        Get content of a file from Google Drive

        Args:
            file_id: The ID of the file to fetch
            mime_type: The MIME type of the file

        Returns:
            Text content of the file or None if failed
        """
        try:
            # For Google Workspace documents, export as text
            if mime_type in EXPORT_MIME_TYPES:
                return self._export_google_doc(file_id, mime_type)
            else:
                return self._download_and_parse(file_id, mime_type)

        except HttpError as error:
            print(f"Error fetching file {file_id}: {error}")
            return None
        except Exception as e:
            print(f"Error processing file {file_id}: {e}")
            return None

    def _export_google_doc(self, file_id: str, mime_type: str) -> Optional[str]:
        """Export Google Workspace document to text"""
        export_mime = EXPORT_MIME_TYPES.get(mime_type, 'text/plain')

        request = self.service.files().export_media(
            fileId=file_id,
            mimeType=export_mime
        )

        file_content = io.BytesIO()
        downloader = MediaIoBaseDownload(file_content, request)

        done = False
        while not done:
            status, done = downloader.next_chunk()

        content = file_content.getvalue().decode('utf-8', errors='ignore')
        return content

    def _download_and_parse(self, file_id: str, mime_type: str) -> Optional[str]:
        """Download and parse a file based on its MIME type"""
        request = self.service.files().get_media(fileId=file_id)

        file_content = io.BytesIO()
        downloader = MediaIoBaseDownload(file_content, request)

        done = False
        while not done:
            status, done = downloader.next_chunk()

        file_content.seek(0)

        # Parse based on MIME type
        if mime_type == 'application/pdf':
            return self._parse_pdf(file_content)
        elif mime_type == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
            return self._parse_docx(file_content)
        elif mime_type == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
            return self._parse_xlsx(file_content)
        elif mime_type == 'text/html':
            return self._parse_html(file_content)
        elif mime_type in ['text/plain', 'text/markdown']:
            return file_content.read().decode('utf-8', errors='ignore')
        else:
            return file_content.read().decode('utf-8', errors='ignore')

    def _parse_pdf(self, file_content: io.BytesIO) -> str:
        """Extract text from PDF file"""
        try:
            reader = PdfReader(file_content)
            text_parts = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    text_parts.append(text)
            return "\n\n".join(text_parts)
        except Exception as e:
            print(f"Error parsing PDF: {e}")
            return ""

    def _parse_docx(self, file_content: io.BytesIO) -> str:
        """Extract text from DOCX file"""
        try:
            doc = DocxDocument(file_content)
            text_parts = []
            for paragraph in doc.paragraphs:
                if paragraph.text.strip():
                    text_parts.append(paragraph.text)
            return "\n\n".join(text_parts)
        except Exception as e:
            print(f"Error parsing DOCX: {e}")
            return ""

    def _parse_xlsx(self, file_content: io.BytesIO) -> str:
        """Extract text from XLSX file"""
        try:
            workbook = openpyxl.load_workbook(file_content, read_only=True)
            text_parts = []

            for sheet_name in workbook.sheetnames:
                sheet = workbook[sheet_name]
                text_parts.append(f"=== Sheet: {sheet_name} ===")

                for row in sheet.iter_rows(values_only=True):
                    row_text = " | ".join([str(cell) if cell is not None else "" for cell in row])
                    if row_text.strip():
                        text_parts.append(row_text)

            return "\n".join(text_parts)
        except Exception as e:
            print(f"Error parsing XLSX: {e}")
            return ""

    def _parse_html(self, file_content: io.BytesIO) -> str:
        """Extract text from HTML file"""
        try:
            soup = BeautifulSoup(file_content.read(), 'html.parser')
            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.decompose()
            text = soup.get_text(separator='\n')
            # Clean up whitespace
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = '\n'.join(chunk for chunk in chunks if chunk)
            return text
        except Exception as e:
            print(f"Error parsing HTML: {e}")
            return ""

    def get_all_documents(self, folder_id: Optional[str] = None) -> List[Dict]:
        """
        Fetch all documents and their content from Drive

        Args:
            folder_id: Optional folder ID to fetch documents from (recursive)

        Returns:
            List of documents with metadata and content
        """
        if folder_id:
            files = self.list_files_recursive(folder_id)
        else:
            files = self.list_files()

        documents = []

        for file in files:
            file_id = file['id']
            file_name = file['name']
            mime_type = file['mimeType']

            print(f"Processing: {file_name}")

            content = self.get_file_content(file_id, mime_type)

            if content and content.strip():
                documents.append({
                    'id': file_id,
                    'name': file_name,
                    'mime_type': mime_type,
                    'content': content,
                    'modified_time': file.get('modifiedTime', '')
                })

        return documents

    def get_user_info(self) -> Dict:
        """Get information about the authenticated user's Drive"""
        try:
            about = self.service.about().get(fields="user, storageQuota").execute()
            return about
        except HttpError as error:
            print(f"Error getting user info: {error}")
            return {}
