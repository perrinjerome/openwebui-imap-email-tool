"""
title: IMAP Email Tool
author: Sandro Scalco - liitu consulting gmbh
description: Access and manage emails via IMAP/SMTP. Search, read, compose, and send emails.
version: 0.2.0
required_open_webui_version: 0.4.0
requirements: imapclient
"""

import email
import json
import re
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid, parseaddr, parsedate_to_datetime
from typing import Optional

from imapclient import IMAPClient
from pydantic import BaseModel, Field


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
        """Configuration for IMAP and SMTP connection."""

        # IMAP Settings
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

        # SMTP Settings
        smtp_host: str = Field(
            default="",
            description="SMTP server hostname (e.g., smtp.gmail.com). Leave empty to use same as IMAP.",
        )
        smtp_port: int = Field(
            default=587,
            description="SMTP server port (587 for STARTTLS, 465 for SSL)",
        )
        smtp_use_tls: bool = Field(
            default=True,
            description="Use STARTTLS for SMTP (port 587)",
        )
        smtp_use_ssl: bool = Field(
            default=False,
            description="Use SSL for SMTP (port 465). If true, smtp_use_tls is ignored.",
        )

        # Sender Info
        sender_name: str = Field(
            default="",
            description="Display name for sent emails (e.g., 'John Doe')",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _get_smtp_host(self) -> str:
        """Get SMTP host, defaulting to IMAP host with 'smtp' prefix."""
        if self.valves.smtp_host:
            return self.valves.smtp_host
        # Try to derive SMTP from IMAP (imap.example.com -> smtp.example.com)
        if self.valves.imap_host.startswith("imap."):
            return "smtp." + self.valves.imap_host[5:]
        return self.valves.imap_host

    def _get_sender_email(self) -> str:
        """Get sender email address."""
        return self.valves.imap_username

    def _create_message(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = "",
        in_reply_to: str = "",
        references: str = "",
        html_body: str = "",
    ) -> MIMEMultipart:
        """Create an email message."""
        msg = MIMEMultipart("alternative")

        # Headers
        sender_email = self._get_sender_email()
        sender_name = self.valves.sender_name or ""
        msg["From"] = formataddr((sender_name, sender_email))
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid()

        if cc:
            msg["Cc"] = cc
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references

        # Body
        msg.attach(MIMEText(body, "plain", "utf-8"))
        if html_body:
            msg.attach(MIMEText(html_body, "html", "utf-8"))

        return msg

    def _get_client(self) -> IMAPClient:
        """Create and connect IMAP client."""
        if not self.valves.imap_host or not self.valves.imap_username:
            raise ValueError(
                "IMAP not configured. Please set host, username and password in Valves."
            )

        client = IMAPClient(
            self.valves.imap_host,
            port=self.valves.imap_port,
            ssl=self.valves.use_ssl,
        )
        client.login(self.valves.imap_username, self.valves.imap_password)
        return client

    def list_folders(self) -> str:
        """
        List all email folders/mailboxes with message counts.

        :return: JSON list of folders with name, message count, and unread count
        """
        try:
            client = self._get_client()
            try:
                folders = []
                for flags, delimiter, name in client.list_folders():
                    try:
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

                return json.dumps(folders, indent=2)
            finally:
                client.logout()
        except Exception as e:
            return json.dumps({"error": str(e)})

    def search_emails(
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
        try:
            client = self._get_client()
            try:
                client.select_folder(folder, readonly=True)

                # Parse search criteria
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

                uids = client.search(search_criteria)
                uids = sorted(uids, reverse=True)[:limit]

                if not uids:
                    return json.dumps({"count": 0, "results": []})

                # Fetch envelope data
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

                return json.dumps({"count": len(results), "results": results}, indent=2)
            finally:
                client.logout()
        except Exception as e:
            return json.dumps({"error": str(e)})

    def get_email(self, uid: int, folder: str = "INBOX") -> str:
        """
        Get the full content of an email by its UID.

        :param uid: The unique identifier of the email (from search results)
        :param folder: The folder containing the email (default: INBOX)
        :return: Full email content including headers and body
        """
        try:
            client = self._get_client()
            try:
                client.select_folder(folder, readonly=True)

                fetch_data = client.fetch([uid], ["RFC822", "FLAGS"])

                if uid not in fetch_data:
                    return json.dumps({"error": f"Email with UID {uid} not found"})

                data = fetch_data[uid]
                raw_email = data.get(b"RFC822")
                flags = [
                    f.decode() if isinstance(f, bytes) else f
                    for f in data.get(b"FLAGS", ())
                ]

                if not raw_email:
                    return json.dumps({"error": "Could not fetch email content"})

                msg = email.message_from_bytes(raw_email)

                # Parse headers
                subject = decode_mime_header(msg.get("Subject", ""))
                from_addr = parse_address(msg.get("From"))
                to_addrs = parse_address_list(msg.get("To"))
                cc_addrs = parse_address_list(msg.get("Cc"))

                # Parse date
                date = None
                date_str = msg.get("Date")
                if date_str:
                    try:
                        date = parsedate_to_datetime(date_str)
                    except Exception:
                        pass

                # Parse body
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

                # Prefer text, fallback to stripped HTML
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

                return json.dumps(result, indent=2)
            finally:
                client.logout()
        except Exception as e:
            return json.dumps({"error": str(e)})

    def list_unread(self, folder: str = "INBOX", limit: int = 10) -> str:
        """
        List unread emails in a folder.

        :param folder: The folder to check (default: INBOX)
        :param limit: Maximum number of emails to return (default: 10)
        :return: JSON list of unread emails
        """
        return self.search_emails(query="UNSEEN", folder=folder, limit=limit)

    def get_folder_stats(self, folder: str = "INBOX") -> str:
        """
        Get statistics for a folder (total and unread count).

        :param folder: The folder to get stats for (default: INBOX)
        :return: JSON with total_messages and unread_messages
        """
        try:
            client = self._get_client()
            try:
                status = client.folder_status(folder, ["MESSAGES", "UNSEEN"])
                return json.dumps(
                    {
                        "folder": folder,
                        "total_messages": status.get(b"MESSAGES", 0),
                        "unread_messages": status.get(b"UNSEEN", 0),
                    },
                    indent=2,
                )
            finally:
                client.logout()
        except Exception as e:
            return json.dumps({"error": str(e)})

    def mark_as_read(self, uid: int, folder: str = "INBOX") -> str:
        """
        Mark an email as read.

        :param uid: The UID of the email
        :param folder: The folder containing the email (default: INBOX)
        :return: Success or error message
        """
        try:
            client = self._get_client()
            try:
                client.select_folder(folder, readonly=False)
                client.add_flags([uid], ["\\Seen"])
                return json.dumps({"success": True, "message": "Email marked as read"})
            finally:
                client.logout()
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

    def move_email(self, uid: int, source_folder: str, dest_folder: str) -> str:
        """
        Move an email to a different folder.

        :param uid: The UID of the email to move
        :param source_folder: The current folder of the email
        :param dest_folder: The destination folder
        :return: Success or error message
        """
        try:
            client = self._get_client()
            try:
                client.select_folder(source_folder, readonly=False)
                try:
                    client.move([uid], dest_folder)
                except Exception:
                    # Fallback: copy then delete
                    client.copy([uid], dest_folder)
                    client.delete_messages([uid])
                    client.expunge()

                return json.dumps(
                    {"success": True, "message": f"Email moved to {dest_folder}"}
                )
            finally:
                client.logout()
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

    def create_draft(
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
        try:
            if not to or not subject:
                return json.dumps(
                    {"success": False, "error": "Recipient (to) and subject are required"}
                )

            # Create the message
            msg = self._create_message(to=to, subject=subject, body=body, cc=cc)

            # Find Drafts folder and save
            client = self._get_client()
            try:
                # Common draft folder names
                draft_folders = ["Drafts", "[Gmail]/Drafts", "INBOX.Drafts", "Draft"]
                drafts_folder = None

                for flags, delimiter, name in client.list_folders():
                    if name in draft_folders or b"\\Drafts" in flags:
                        drafts_folder = name
                        break

                if not drafts_folder:
                    # Default to "Drafts"
                    drafts_folder = "Drafts"

                # Append to drafts folder
                client.append(
                    drafts_folder,
                    msg.as_bytes(),
                    flags=["\\Draft", "\\Seen"],
                    msg_time=datetime.now(timezone.utc),
                )

                return json.dumps(
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
                )
            finally:
                client.logout()
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

    def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = "",
    ) -> str:
        """
        Send an email immediately via SMTP.

        :param to: Recipient email address(es), comma-separated for multiple
        :param subject: Email subject line
        :param body: Email body text
        :param cc: CC recipients, comma-separated (optional)
        :param bcc: BCC recipients, comma-separated (optional)
        :return: JSON with success status
        """
        try:
            if not to or not subject:
                return json.dumps(
                    {"success": False, "error": "Recipient (to) and subject are required"}
                )

            # Create the message
            msg = self._create_message(to=to, subject=subject, body=body, cc=cc, bcc=bcc)

            # Collect all recipients
            all_recipients = [addr.strip() for addr in to.split(",")]
            if cc:
                all_recipients.extend([addr.strip() for addr in cc.split(",")])
            if bcc:
                all_recipients.extend([addr.strip() for addr in bcc.split(",")])

            # Send via SMTP
            smtp_host = self._get_smtp_host()
            smtp_port = self.valves.smtp_port
            sender_email = self._get_sender_email()

            if self.valves.smtp_use_ssl:
                # SSL connection (port 465)
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
                    server.login(self.valves.imap_username, self.valves.imap_password)
                    server.sendmail(sender_email, all_recipients, msg.as_string())
            else:
                # STARTTLS connection (port 587)
                with smtplib.SMTP(smtp_host, smtp_port) as server:
                    if self.valves.smtp_use_tls:
                        context = ssl.create_default_context()
                        server.starttls(context=context)
                    server.login(self.valves.imap_username, self.valves.imap_password)
                    server.sendmail(sender_email, all_recipients, msg.as_string())

            # Optionally save to Sent folder
            try:
                client = self._get_client()
                try:
                    # Find Sent folder
                    sent_folders = ["Sent", "[Gmail]/Sent Mail", "INBOX.Sent", "Sent Items"]
                    sent_folder = None

                    for flags, delimiter, name in client.list_folders():
                        if name in sent_folders or b"\\Sent" in flags:
                            sent_folder = name
                            break

                    if sent_folder:
                        client.append(
                            sent_folder,
                            msg.as_bytes(),
                            flags=["\\Seen"],
                            msg_time=datetime.now(timezone.utc),
                        )
                finally:
                    client.logout()
            except Exception:
                # Don't fail if we can't save to Sent
                pass

            return json.dumps(
                {
                    "success": True,
                    "message": f"Email sent successfully to {to}",
                    "details": {
                        "to": to,
                        "cc": cc,
                        "bcc": bcc,
                        "subject": subject,
                    },
                },
                indent=2,
            )
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

    def reply_to_email(
        self,
        uid: int,
        body: str,
        folder: str = "INBOX",
        reply_all: bool = False,
    ) -> str:
        """
        Reply to an existing email.

        :param uid: The UID of the email to reply to
        :param body: The reply message body
        :param folder: The folder containing the original email (default: INBOX)
        :param reply_all: If true, reply to all recipients (default: false)
        :return: JSON with success status
        """
        try:
            # First, get the original email
            client = self._get_client()
            try:
                client.select_folder(folder, readonly=True)
                fetch_data = client.fetch([uid], ["RFC822"])

                if uid not in fetch_data:
                    return json.dumps({"success": False, "error": f"Email {uid} not found"})

                raw_email = fetch_data[uid].get(b"RFC822")
                if not raw_email:
                    return json.dumps({"success": False, "error": "Could not fetch email"})

                original = email.message_from_bytes(raw_email)
            finally:
                client.logout()

            # Extract reply information
            original_from = original.get("From", "")
            original_to = original.get("To", "")
            original_cc = original.get("Cc", "")
            original_subject = decode_mime_header(original.get("Subject", ""))
            original_message_id = original.get("Message-ID", "")
            original_references = original.get("References", "")

            # Build reply subject
            if not original_subject.lower().startswith("re:"):
                reply_subject = f"Re: {original_subject}"
            else:
                reply_subject = original_subject

            # Build recipients
            reply_to = original_from
            reply_cc = ""

            if reply_all:
                # Include original To and CC, excluding ourselves
                my_email = self._get_sender_email().lower()
                additional = []

                for addr_str in [original_to, original_cc]:
                    if addr_str:
                        for addr in addr_str.split(","):
                            addr = addr.strip()
                            parsed = parseaddr(addr)[1].lower()
                            if parsed and parsed != my_email and parsed != parseaddr(original_from)[1].lower():
                                additional.append(addr)

                if additional:
                    reply_cc = ", ".join(additional)

            # Build references
            new_references = original_references
            if original_message_id:
                if new_references:
                    new_references = f"{new_references} {original_message_id}"
                else:
                    new_references = original_message_id

            # Create and send reply
            msg = self._create_message(
                to=reply_to,
                subject=reply_subject,
                body=body,
                cc=reply_cc,
                in_reply_to=original_message_id,
                references=new_references,
            )

            # Send via SMTP
            all_recipients = [parseaddr(reply_to)[1]]
            if reply_cc:
                all_recipients.extend([parseaddr(a)[1] for a in reply_cc.split(",")])

            smtp_host = self._get_smtp_host()
            sender_email = self._get_sender_email()

            if self.valves.smtp_use_ssl:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(smtp_host, self.valves.smtp_port, context=context) as server:
                    server.login(self.valves.imap_username, self.valves.imap_password)
                    server.sendmail(sender_email, all_recipients, msg.as_string())
            else:
                with smtplib.SMTP(smtp_host, self.valves.smtp_port) as server:
                    if self.valves.smtp_use_tls:
                        context = ssl.create_default_context()
                        server.starttls(context=context)
                    server.login(self.valves.imap_username, self.valves.imap_password)
                    server.sendmail(sender_email, all_recipients, msg.as_string())

            return json.dumps(
                {
                    "success": True,
                    "message": f"Reply sent to {reply_to}",
                    "details": {
                        "to": reply_to,
                        "cc": reply_cc,
                        "subject": reply_subject,
                        "in_reply_to": original_message_id,
                    },
                },
                indent=2,
            )
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})
