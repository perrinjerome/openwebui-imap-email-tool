"""
title: IMAP Email Tool
author: Sandro Scalco - liitu consulting gmbh
description: Access and manage emails via IMAP. Search, read, and organize emails.
version: 0.3.0
required_open_webui_version: 0.4.0
#requirements: imapclient
"""

import asyncio
import email
import json
import logging
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid, parseaddr, parsedate_to_datetime
from typing import Optional

log = logging.getLogger("openwebui_imap_tool")

from imapclient import IMAPClient
from pydantic import BaseModel, Field

_thread_local = threading.local()


@contextmanager
def _imap_timer(operation: str):
    """Log and collect the wall-clock time of an IMAP operation."""
    t0 = time.monotonic()
    yield
    elapsed = round((time.monotonic() - t0) * 1000, 1)
    log.debug("IMAP %s (%.1fms)", operation, elapsed)
    timings = getattr(_thread_local, "timings", None)
    if timings is not None:
        timings.append({"op": operation, "ms": elapsed})


def _start_timing():
    """Begin collecting IMAP call timings for the current thread."""
    _thread_local.timings = []
    _thread_local.t0 = time.monotonic()


def _finalize_response(json_str: str) -> str:
    """Inject collected timing data into a JSON response string."""
    timings = getattr(_thread_local, "timings", None) or []
    t0 = getattr(_thread_local, "t0", None)
    _thread_local.timings = None
    _thread_local.t0 = None

    total_ms = round((time.monotonic() - t0) * 1000, 1) if t0 else None
    try:
        data = json.loads(json_str)
        data["_timing"] = {"total_ms": total_ms, "calls": timings}
        return json.dumps(data, indent=2)
    except Exception:
        return json_str


# =============================================================================
# Helper Classes and Functions
# =============================================================================


@dataclass
class EmailAddress:
    """Parsed email address."""

    name: str
    email: str

    def __str__(self) -> str:
        if self.name:
            return f"{self.name} <{self.email}>"
        return self.email

    def to_dict(self) -> dict:
        return {"name": self.name, "email": self.email}


def decode_mime_header(header: Optional[str]) -> str:
    """Decode MIME encoded header."""
    if not header:
        return ""

    decoded_parts = []
    for part, charset in decode_header(header):
        if isinstance(part, bytes):
            charset = charset or "utf-8"
            try:
                decoded_parts.append(part.decode(charset, errors="replace"))
            except (LookupError, UnicodeDecodeError):
                decoded_parts.append(part.decode("utf-8", errors="replace"))
        else:
            decoded_parts.append(part)

    return "".join(decoded_parts)


def parse_address(addr_str: Optional[str]) -> EmailAddress:
    """Parse an email address string."""
    if not addr_str:
        return EmailAddress("", "")

    name, email_addr = parseaddr(addr_str)
    return EmailAddress(decode_mime_header(name), email_addr)


def parse_address_list(addr_str: Optional[str]) -> list[EmailAddress]:
    """Parse a comma-separated list of email addresses."""
    if not addr_str:
        return []

    addresses = []
    current = ""
    in_quotes = False

    for char in addr_str:
        if char == '"':
            in_quotes = not in_quotes
        elif char == "," and not in_quotes:
            if current.strip():
                addresses.append(parse_address(current.strip()))
            current = ""
            continue
        current += char

    if current.strip():
        addresses.append(parse_address(current.strip()))

    return addresses


# =============================================================================
# Open WebUI Tool
# =============================================================================


class Tools:
    """IMAP Email Tool for Open WebUI."""

    class Valves(BaseModel):
        """Configuration for IMAP connection."""

        imap_host: str = Field(
            default="",
            description="IMAP server hostname (e.g., imap.gmail.com)",
        )
        imap_port: int = Field(
            default=993,
            description="IMAP server port (993 for SSL, 143 for plain)",
        )
        imap_username: str = Field(
            default="",
            description="Email address / username for IMAP login",
        )
        imap_password: str = Field(
            default="",
            description="Password or app-specific password for IMAP",
        )
        use_ssl: bool = Field(
            default=True,
            description="Use SSL/TLS connection for IMAP",
        )
        sender_name: str = Field(
            default="",
            description="Display name for draft emails (e.g., 'John Doe')",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _create_message(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
    ) -> MIMEMultipart:
        """Create an email message for drafts."""
        msg = MIMEMultipart("alternative")

        sender_email = self.valves.imap_username
        sender_name = self.valves.sender_name or ""
        msg["From"] = formataddr((sender_name, sender_email))
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid()

        if cc:
            msg["Cc"] = cc

        msg.attach(MIMEText(body, "plain", "utf-8"))

        return msg

    def _get_client(self) -> IMAPClient:
        """Create and connect IMAP client."""
        if not self.valves.imap_host or not self.valves.imap_username:
            raise ValueError(
                "IMAP not configured. Please set host, username and password in Valves."
            )

        log.debug(
            "Connecting to %s:%d (ssl=%s) as %s",
            self.valves.imap_host,
            self.valves.imap_port,
            self.valves.use_ssl,
            self.valves.imap_username,
        )
        with _imap_timer("CONNECT"):
            client = IMAPClient(
                self.valves.imap_host,
                port=self.valves.imap_port,
                ssl=self.valves.use_ssl,
            )
        with _imap_timer("LOGIN"):
            client.login(self.valves.imap_username, self.valves.imap_password)
        return client

    def _sync_list_folders(self) -> str:
        _start_timing()
        log.info("list_folders()")
        try:
            client = self._get_client()
            try:
                with _imap_timer("LIST"):
                    raw_folders = client.list_folders()
                folders = []
                for flags, delimiter, name in raw_folders:
                    try:
                        with _imap_timer(f"STATUS {name!r}"):
                            status = client.folder_status(name, ["MESSAGES", "UNSEEN"])
                        folders.append(
                            {
                                "name": name,
                                "messages": status.get(b"MESSAGES", 0),
                                "unread": status.get(b"UNSEEN", 0),
                            }
                        )
                    except Exception:
                        folders.append({"name": name, "messages": None, "unread": None})

                log.info("list_folders: found %d folders", len(folders))
                return _finalize_response(json.dumps(folders, indent=2))
            finally:
                client.logout()
        except Exception as e:
            log.exception("list_folders failed")
            return _finalize_response(json.dumps({"error": str(e)}))

    async def list_folders(self) -> str:
        """
        List all email folders/mailboxes with message counts.

        :return: JSON list of folders with name, message count, and unread count
        """
        return await asyncio.to_thread(self._sync_list_folders)

    def _sync_search_emails(self, query: str, folder: str, limit: int) -> str:
        _start_timing()
        log.info("search_emails(query=%r, folder=%r, limit=%d)", query, folder, limit)
        try:
            client = self._get_client()
            try:
                with _imap_timer(f"SELECT {folder!r}"):
                    client.select_folder(folder, readonly=True)

                if query.upper() == "ALL":
                    search_criteria = ["ALL"]
                elif query.upper() == "UNSEEN":
                    search_criteria = ["UNSEEN"]
                elif query.upper() == "FLAGGED":
                    search_criteria = ["FLAGGED"]
                elif query.upper().startswith("FROM:"):
                    search_criteria = ["FROM", query[5:].strip()]
                elif query.upper().startswith("SUBJECT:"):
                    search_criteria = ["SUBJECT", query[8:].strip()]
                elif query.upper().startswith("SINCE:"):
                    search_criteria = ["SINCE", query[6:].strip()]
                elif query.upper().startswith("TEXT:"):
                    search_criteria = ["TEXT", query[5:].strip()]
                else:
                    search_criteria = ["TEXT", query]

                with _imap_timer(f"SEARCH {search_criteria}"):
                    uids = client.search(search_criteria)
                log.info("IMAP SEARCH returned %d UIDs", len(uids))
                uids = sorted(uids, reverse=True)[:limit]

                if not uids:
                    return _finalize_response(json.dumps({"count": 0, "results": []}))

                with _imap_timer(f"FETCH ENVELOPE for {len(uids)} UIDs"):
                    fetch_data = client.fetch(uids, ["ENVELOPE", "FLAGS"])

                results = []
                for uid, data in fetch_data.items():
                    envelope = data.get(b"ENVELOPE")
                    flags = data.get(b"FLAGS", ())

                    if envelope:
                        subject = decode_mime_header(
                            envelope.subject.decode("utf-8", errors="replace")
                            if envelope.subject
                            else ""
                        )

                        from_addr = ""
                        if envelope.from_ and len(envelope.from_) > 0:
                            addr = envelope.from_[0]
                            name = (
                                addr.name.decode("utf-8", errors="replace")
                                if addr.name
                                else ""
                            )
                            mailbox = (
                                addr.mailbox.decode("utf-8", errors="replace")
                                if addr.mailbox
                                else ""
                            )
                            host = (
                                addr.host.decode("utf-8", errors="replace")
                                if addr.host
                                else ""
                            )
                            email_str = f"{mailbox}@{host}"
                            from_addr = f"{name} <{email_str}>" if name else email_str

                        date = envelope.date
                        date_str = date.isoformat() if date else None

                        results.append(
                            {
                                "uid": uid,
                                "subject": subject,
                                "from": from_addr,
                                "date": date_str,
                                "is_read": b"\\Seen" in flags,
                                "is_flagged": b"\\Flagged" in flags,
                            }
                        )

                log.info("search_emails: returning %d results", len(results))
                return _finalize_response(
                    json.dumps({"count": len(results), "results": results}, indent=2)
                )
            finally:
                client.logout()
        except Exception as e:
            log.exception("search_emails failed")
            return _finalize_response(json.dumps({"error": str(e)}))

    async def search_emails(
        self,
        query: str = "ALL",
        folder: str = "INBOX",
        limit: int = 10,
    ) -> str:
        """
        Search for emails in a folder.

        :param query: Search query. Examples: "ALL", "UNSEEN", "FLAGGED", "FROM:john@example.com", "SUBJECT:meeting", "TEXT:project"
        :param folder: Folder to search in (default: INBOX)
        :param limit: Maximum number of results (default: 10)
        :return: JSON list of matching emails with uid, subject, from, date
        """
        return await asyncio.to_thread(self._sync_search_emails, query, folder, limit)

    def _sync_get_email(self, uid: int, folder: str) -> str:
        _start_timing()
        log.info("get_email(uid=%d, folder=%r)", uid, folder)
        try:
            client = self._get_client()
            try:
                with _imap_timer(f"SELECT {folder!r}"):
                    client.select_folder(folder, readonly=True)

                with _imap_timer(f"FETCH RFC822 uid={uid}"):
                    fetch_data = client.fetch([uid], ["RFC822", "FLAGS"])

                if uid not in fetch_data:
                    return _finalize_response(
                        json.dumps({"error": f"Email with UID {uid} not found"})
                    )

                data = fetch_data[uid]
                raw_email = data.get(b"RFC822")
                flags = [
                    f.decode() if isinstance(f, bytes) else f
                    for f in data.get(b"FLAGS", ())
                ]

                if not raw_email:
                    return _finalize_response(
                        json.dumps({"error": "Could not fetch email content"})
                    )

                log.debug("IMAP FETCH uid=%d: %d bytes", uid, len(raw_email))
                msg = email.message_from_bytes(raw_email)

                subject = decode_mime_header(msg.get("Subject", ""))
                from_addr = parse_address(msg.get("From"))
                to_addrs = parse_address_list(msg.get("To"))
                cc_addrs = parse_address_list(msg.get("Cc"))

                date = None
                date_str = msg.get("Date")
                if date_str:
                    try:
                        date = parsedate_to_datetime(date_str)
                    except Exception:
                        pass

                body_text = None
                body_html = None

                if msg.is_multipart():
                    for part in msg.walk():
                        content_type = part.get_content_type()
                        if content_type == "text/plain" and body_text is None:
                            payload = part.get_payload(decode=True)
                            if payload:
                                charset = part.get_content_charset() or "utf-8"
                                body_text = payload.decode(charset, errors="replace")
                        elif content_type == "text/html" and body_html is None:
                            payload = part.get_payload(decode=True)
                            if payload:
                                charset = part.get_content_charset() or "utf-8"
                                body_html = payload.decode(charset, errors="replace")
                else:
                    content_type = msg.get_content_type()
                    payload = msg.get_payload(decode=True)
                    if payload:
                        charset = msg.get_content_charset() or "utf-8"
                        text = payload.decode(charset, errors="replace")
                        if content_type == "text/html":
                            body_html = text
                        else:
                            body_text = text

                body = body_text
                if not body and body_html:
                    body = re.sub(r"<[^>]+>", "", body_html)
                    body = re.sub(r"\s+", " ", body).strip()

                result = {
                    "uid": uid,
                    "subject": subject,
                    "from": str(from_addr),
                    "to": [str(a) for a in to_addrs],
                    "cc": [str(a) for a in cc_addrs],
                    "date": date.isoformat() if date else None,
                    "body": body or "",
                    "is_read": "\\Seen" in flags,
                    "is_flagged": "\\Flagged" in flags,
                }

                log.info("get_email: uid=%d subject=%r", uid, subject)
                return _finalize_response(json.dumps(result, indent=2))
            finally:
                client.logout()
        except Exception as e:
            log.exception("get_email failed")
            return _finalize_response(json.dumps({"error": str(e)}))

    async def get_email(self, uid: int, folder: str = "INBOX") -> str:
        """
        Get the full content of an email by its UID.

        :param uid: The unique identifier of the email (from search results)
        :param folder: The folder containing the email (default: INBOX)
        :return: Full email content including headers and body
        """
        return await asyncio.to_thread(self._sync_get_email, uid, folder)

    async def list_unread(self, folder: str = "INBOX", limit: int = 10) -> str:
        """
        List unread emails in a folder.

        :param folder: The folder to check (default: INBOX)
        :param limit: Maximum number of emails to return (default: 10)
        :return: JSON list of unread emails
        """
        return await self.search_emails(query="UNSEEN", folder=folder, limit=limit)

    def _sync_get_folder_stats(self, folder: str) -> str:
        _start_timing()
        log.info("get_folder_stats(folder=%r)", folder)
        try:
            client = self._get_client()
            try:
                with _imap_timer(f"STATUS {folder!r}"):
                    status = client.folder_status(folder, ["MESSAGES", "UNSEEN"])
                total = status.get(b"MESSAGES", 0)
                unread = status.get(b"UNSEEN", 0)
                log.info("get_folder_stats: %s has %d messages (%d unread)", folder, total, unread)
                return _finalize_response(json.dumps(
                    {
                        "folder": folder,
                        "total_messages": total,
                        "unread_messages": unread,
                    },
                    indent=2,
                ))
            finally:
                client.logout()
        except Exception as e:
            log.exception("get_folder_stats failed")
            return _finalize_response(json.dumps({"error": str(e)}))

    async def get_folder_stats(self, folder: str = "INBOX") -> str:
        """
        Get statistics for a folder (total and unread count).

        :param folder: The folder to get stats for (default: INBOX)
        :return: JSON with total_messages and unread_messages
        """
        return await asyncio.to_thread(self._sync_get_folder_stats, folder)

    def _sync_mark_as_read(self, uid: int, folder: str) -> str:
        _start_timing()
        log.info("mark_as_read(uid=%d, folder=%r)", uid, folder)
        try:
            client = self._get_client()
            try:
                with _imap_timer(f"SELECT {folder!r}"):
                    client.select_folder(folder, readonly=False)
                with _imap_timer(f"STORE +FLAGS \\Seen uid={uid}"):
                    client.add_flags([uid], ["\\Seen"])
                log.info("mark_as_read: uid=%d marked as read", uid)
                return _finalize_response(
                    json.dumps({"success": True, "message": "Email marked as read"})
                )
            finally:
                client.logout()
        except Exception as e:
            log.exception("mark_as_read failed")
            return _finalize_response(json.dumps({"success": False, "error": str(e)}))

    async def mark_as_read(self, uid: int, folder: str = "INBOX") -> str:
        """
        Mark an email as read.

        :param uid: The UID of the email
        :param folder: The folder containing the email (default: INBOX)
        :return: Success or error message
        """
        return await asyncio.to_thread(self._sync_mark_as_read, uid, folder)

    def _sync_create_draft(self, to: str, subject: str, body: str, cc: str) -> str:
        _start_timing()
        log.info("create_draft(to=%r, subject=%r)", to, subject)
        try:
            if not to or not subject:
                return _finalize_response(json.dumps(
                    {"success": False, "error": "Recipient (to) and subject are required"}
                ))

            msg = self._create_message(to=to, subject=subject, body=body, cc=cc)

            client = self._get_client()
            try:
                with _imap_timer("LIST (find drafts folder)"):
                    raw_folders = client.list_folders()

                draft_folders = ["Drafts", "[Gmail]/Drafts", "INBOX.Drafts", "Draft"]
                drafts_folder = None

                for flags, delimiter, name in raw_folders:
                    if name in draft_folders or b"\\Drafts" in flags:
                        drafts_folder = name
                        break

                if not drafts_folder:
                    drafts_folder = "Drafts"

                with _imap_timer(f"APPEND to {drafts_folder!r}"):
                    client.append(
                        drafts_folder,
                        msg.as_bytes(),
                        flags=["\\Draft", "\\Seen"],
                        msg_time=datetime.now(timezone.utc),
                    )

                log.info("create_draft: saved to %s", drafts_folder)
                return _finalize_response(json.dumps(
                    {
                        "success": True,
                        "message": f"Draft saved to {drafts_folder}",
                        "draft": {
                            "to": to,
                            "subject": subject,
                            "cc": cc,
                            "folder": drafts_folder,
                        },
                    },
                    indent=2,
                ))
            finally:
                client.logout()
        except Exception as e:
            log.exception("create_draft failed")
            return _finalize_response(json.dumps({"success": False, "error": str(e)}))

    async def create_draft(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
    ) -> str:
        """
        Create a draft email and save it to the Drafts folder.

        :param to: Recipient email address(es), comma-separated for multiple
        :param subject: Email subject line
        :param body: Email body text
        :param cc: CC recipients, comma-separated (optional)
        :return: JSON with success status and draft info
        """
        return await asyncio.to_thread(self._sync_create_draft, to, subject, body, cc)

    def _sync_move_email(self, uid: int, source_folder: str, dest_folder: str) -> str:
        _start_timing()
        log.info("move_email(uid=%d, %r -> %r)", uid, source_folder, dest_folder)
        try:
            client = self._get_client()
            try:
                with _imap_timer(f"SELECT {source_folder!r}"):
                    client.select_folder(source_folder, readonly=False)
                try:
                    with _imap_timer(f"MOVE uid={uid} -> {dest_folder!r}"):
                        client.move([uid], dest_folder)
                except Exception:
                    log.debug("MOVE not supported, falling back to COPY+DELETE")
                    with _imap_timer(f"COPY uid={uid} -> {dest_folder!r}"):
                        client.copy([uid], dest_folder)
                    with _imap_timer(f"DELETE uid={uid}"):
                        client.delete_messages([uid])
                    with _imap_timer("EXPUNGE"):
                        client.expunge()

                log.info("move_email: uid=%d moved to %s", uid, dest_folder)
                return _finalize_response(json.dumps(
                    {"success": True, "message": f"Email moved to {dest_folder}"}
                ))
            finally:
                client.logout()
        except Exception as e:
            log.exception("move_email failed")
            return _finalize_response(json.dumps({"success": False, "error": str(e)}))

    async def move_email(self, uid: int, source_folder: str, dest_folder: str) -> str:
        """
        Move an email to a different folder.

        :param uid: The UID of the email to move
        :param source_folder: The current folder of the email
        :param dest_folder: The destination folder
        :return: Success or error message
        """
        return await asyncio.to_thread(self._sync_move_email, uid, source_folder, dest_folder)

