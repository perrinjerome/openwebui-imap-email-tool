# IMAP Email Tool for Open WebUI

Access and manage emails via IMAP directly from Open WebUI. Search, read, and organize emails through an AI assistant.

## Features

### Reading & Searching
- **List Folders** - View all email folders with message counts and unread stats
- **Search Emails** - Flexible search with multiple criteria (ALL, UNSEEN, FLAGGED, FROM, SUBJECT, TEXT, SINCE)
- **Get Email** - Read full email content including headers and body
- **List Unread** - Quick access to unread messages
- **Folder Stats** - Get total and unread message counts for any folder

### Managing
- **Mark as Read** - Update email read status
- **Move Email** - Move messages between folders (with automatic fallback to copy+delete)

### Drafts
- **Create Draft** - Compose and save drafts to the Drafts folder

## Requirements

- Open WebUI 0.4.0 or later
- Python package: `imapclient`

## Installation

### Step 1: Add the Tool

1. Open **Open WebUI** in your browser
2. Go to **Workspace** → **Tools** (in the left sidebar)
3. Click the **+** button to create a new tool
4. Copy the entire contents of `openwebui_tool.py` and paste it into the code editor
5. Click **Save**
6. The tool "IMAP Email Tool" should now appear in your tools list

### Step 2: Configure the Tool (Valves)

1. In **Workspace** → **Tools**, find "IMAP Email Tool"
2. Click the **gear icon** (⚙️) to open Valves settings
3. Fill in your email server credentials (see [Configuration](#configuration) below)
4. Click **Save**

### Step 3: Create a Model with the Email Tool

1. Go to **Workspace** → **Models**
2. Click **+** to create a new model
3. Configure the model:
   - **Name**: Give it a descriptive name (e.g., "Email Assistant")
   - **Base Model**: Select your preferred LLM (GPT-4, Claude, Llama, etc.)
   - **System Prompt**: Copy and paste the system prompt from [system_prompt.md](system_prompt.md)
   - **Tools**: Enable the **IMAP Email Tool** by selecting it from the available tools
4. Click **Save**

### Step 4: Start Using

1. Go to **New Chat**
2. Select your newly created "Email Assistant" model from the model dropdown
3. Start chatting! Try commands like:
   - "Show me my unread emails"
   - "Search for emails from john@example.com"
   - "Read the latest email"

## Configuration

Configure the tool via **Valves** (tool settings):

### IMAP Settings
| Setting | Default | Description |
|---------|---------|-------------|
| `imap_host` | - | IMAP server hostname (e.g., `imap.gmail.com`) |
| `imap_port` | 993 | IMAP port (993 for SSL, 143 for plain) |
| `imap_username` | - | Email address / username |
| `imap_password` | - | Password or app-specific password |
| `use_ssl` | true | Use SSL/TLS connection |
| `sender_name` | - | Display name for draft emails (e.g., "John Doe") |

### Example Configurations

**Gmail:**
```
imap_host: imap.gmail.com
imap_port: 993
```
> Note: Gmail requires an [App Password](https://support.google.com/accounts/answer/185833)

**Microsoft 365 / Outlook:**
```
imap_host: outlook.office365.com
imap_port: 993
```

## System Prompt

The system prompt teaches the AI how to effectively use the email tool. It includes:

- Available functions and their parameters
- Best practices for searching and reading emails
- Example workflows for common tasks
- Formatting guidelines

See [system_prompt.md](system_prompt.md) for a ready-to-use prompt.

## Available Functions

| Function | Parameters | Description |
|----------|------------|-------------|
| `list_folders()` | - | List all folders with message counts |
| `search_emails()` | `query`, `folder`, `limit` | Search emails |
| `get_email()` | `uid`, `folder` | Get full email content |
| `list_unread()` | `folder`, `limit` | List unread emails |
| `get_folder_stats()` | `folder` | Get folder statistics |
| `mark_as_read()` | `uid`, `folder` | Mark email as read |
| `move_email()` | `uid`, `source_folder`, `dest_folder` | Move email |
| `create_draft()` | `to`, `subject`, `body`, `cc` | Create and save draft |

### Search Query Syntax

| Query | Description |
|-------|-------------|
| `ALL` | All emails |
| `UNSEEN` | Unread emails |
| `FLAGGED` | Flagged/starred emails |
| `FROM:email` | Emails from specific sender |
| `SUBJECT:text` | Emails with subject containing text |
| `TEXT:content` | Full-text search |
| `SINCE:date` | Emails since date |

## Example Usage

**User:** "Show me my unread emails"
- AI calls `list_unread()` and presents results

**User:** "Read the email from John"
- AI calls `search_emails("FROM:john")` to find UIDs
- AI calls `get_email(uid)` to fetch content

## Security Notes

- Use app-specific passwords when available (Gmail, Microsoft, etc.)
- Credentials are stored in Open WebUI's valve configuration
- The tool uses secure connections (SSL/TLS) by default

## Author

**Sandro Scalco** - [liitu consulting gmbh](https://liitu.ch)

## License

MIT License

## Version

0.3.0
