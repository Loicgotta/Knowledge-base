# Google Sheets Editor Agent - Integration Guide

## Overview

This document describes how to integrate the Google Sheets AI Editor agent into your application. The agent uses **Gemini 2.5 Pro** to intelligently modify Google Sheets based on natural language commands.

## Architecture

```
User Request → Flask API → Gemini AI → Parse Commands → Google Sheets API → Response
```

## Prerequisites

### 1. Environment Variables

```bash
# Google OAuth (for Drive/Sheets access)
GOOGLE_CLIENT_ID=your_client_id
GOOGLE_CLIENT_SECRET=your_client_secret

# Gemini API
GEMINI_API_KEY=your_gemini_api_key

# Flask
FLASK_SECRET_KEY=your_secret_key
```

### 2. Required Packages

```txt
# requirements.txt
google-genai>=1.0.0
google-auth>=2.23.4
google-auth-oauthlib>=1.1.0
google-api-python-client>=2.108.0
flask>=3.0.0
```

### 3. Google Cloud Console Setup

1. Enable APIs:
   - Google Drive API
   - Google Sheets API
   - Google Docs API

2. Create OAuth 2.0 credentials with scopes:
   ```python
   SCOPES = [
       'https://www.googleapis.com/auth/drive',
       'https://www.googleapis.com/auth/spreadsheets',
       'https://www.googleapis.com/auth/documents',
   ]
   ```

---

## Key Changes from Previous Version

### 1. Model Upgrade

| Before | After |
|--------|-------|
| `gpt-4.1-mini` | `gemini-2.5-pro` |
| OpenAI SDK | Google GenAI SDK |

**Code change:**
```python
# OLD (OpenAI)
from openai import OpenAI
client = OpenAI()
response = client.chat.completions.create(
    model="gpt-4.1-mini",
    messages=[...]
)

# NEW (Gemini)
from google import genai
client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))
response = client.models.generate_content(
    model='gemini-2.5-pro',
    contents=contents,
    config={'temperature': 0.7, 'max_output_tokens': 65536}
)
```

### 2. Auto-Expand Grid Feature

The new version automatically expands the Google Sheet grid when cells exceed current dimensions.

**Location:** `drive_service.py` → `update_sheet_cells()`

```python
# Automatically detects required dimensions
max_col, max_row = parse_cell_references(updates)

# Expands grid if needed
if max_col > current_cols:
    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=file_id,
        body={'requests': [{'appendDimension': {...}}]}
    ).execute()
```

### 3. Enhanced Prompt Structure

The prompt now includes:
- **Table Analysis Section** (A-E steps)
- **Double-Check Verification** before execution
- **Formula Detection** rules
- **Pattern Recognition** guidelines

### 4. Timeout Configuration

```python
# gunicorn.conf.py
timeout = 300  # 5 minutes for long Gemini requests
```

---

## API Endpoint

### POST `/chat-edit`

**Request:**
```json
{
  "message": "Ajoute une ligne avec 150 utilisateurs pour Mars",
  "document": {
    "id": "spreadsheet_file_id",
    "name": "Mon Budget",
    "mimeType": "application/vnd.google-apps.spreadsheet"
  },
  "history": []
}
```

**Response:**
```json
{
  "answer": "C'est fait! J'ai ajouté la ligne pour Mars.",
  "modification_result": {
    "status": "success",
    "message": "3 cellule(s) mise(s) a jour",
    "updated_cells": 3
  },
  "debug_logs": ["..."]  // Available for debugging
}
```

---

## Available Commands

The agent can generate these commands:

### 1. MODIFY_CELLS
```json
[MODIFY_CELLS:{"file_id":"xxx", "updates":[
  {"cell":"A1", "value":"Header"},
  {"cell":"B2", "value":"=SUM(B3:B10)"}
]}]
```

### 2. CREATE_CHART
```json
[CREATE_CHART:{"file_id":"xxx", "type":"LINE", "range":"A1:C10", "title":"Evolution"}]
```

### 3. FORMAT_RANGE
```json
[FORMAT_RANGE:{"file_id":"xxx", "range":"A1:E1", "bold":true, "borders":true}]
```

---

## Integration Steps

### Step 1: Copy Required Files

```
your_project/
├── app.py                 # Main Flask app with /chat-edit endpoint
├── drive_service.py       # Google Drive/Sheets service class
├── auth.py                # OAuth authentication helpers
├── gunicorn.conf.py       # Server configuration
└── requirements.txt       # Dependencies
```

### Step 2: Initialize DriveService

```python
from drive_service import DriveService

# After OAuth authentication
credentials = get_valid_credentials()
drive_service = DriveService(credentials)

# Read sheet content
content = drive_service.get_sheet_content(file_id)

# Update cells
result = drive_service.update_sheet_cells(file_id, updates)
```

### Step 3: Call Gemini API

```python
from google import genai

client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))

response = client.models.generate_content(
    model='gemini-2.5-pro',
    contents=[
        {"role": "user", "parts": [{"text": system_prompt}]},
        {"role": "model", "parts": [{"text": "Compris."}]},
        {"role": "user", "parts": [{"text": user_message}]}
    ],
    config={'temperature': 0.7, 'max_output_tokens': 65536}
)

answer = response.text
```

### Step 4: Parse and Execute Commands

```python
if '[MODIFY_CELLS:' in answer:
    # Extract JSON from command
    json_data = extract_json_from_command(answer, '[MODIFY_CELLS:')

    # Execute update
    result = drive_service.update_sheet_cells(
        file_id=json_data['file_id'],
        updates=json_data['updates']
    )
```

---

## Error Handling

### Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `Range exceeds grid limits` | Sheet too small | Auto-handled by new version |
| `Token limit exceeded` | Response too long | Increase `max_output_tokens` |
| `WORKER TIMEOUT` | Slow API response | Increase gunicorn `timeout` |
| `Invalid credentials` | OAuth expired | Refresh token flow |

---

## Best Practices

1. **Always use formulas** - Configure the prompt to prefer `=SUM()` over hardcoded values
2. **Preserve existing formulas** - The agent should detect and avoid overwriting formulas
3. **Double-check before execution** - The prompt includes verification steps
4. **Use stable models** - Avoid `-preview` or `-experimental` model versions

---

## Configuration Reference

### gunicorn.conf.py
```python
workers = 2
threads = 4
timeout = 300  # 5 minutes
keepalive = 5
```

### Gemini API Config
```python
config = {
    'temperature': 0.7,      # Creativity level
    'max_output_tokens': 65536  # Max response length (64K)
}
```

---

## Support

For issues or questions, check:
- Google Sheets API docs: https://developers.google.com/sheets/api
- Gemini API docs: https://ai.google.dev/gemini-api/docs
- This repository's issues page
