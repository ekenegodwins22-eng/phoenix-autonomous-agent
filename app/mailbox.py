"""Small client for the user's TempNest/Gmail alias API.

The alias API is optional at boot so the agent can run without mailbox credentials.
Set MAILBOX_API_TOKEN if that service requires a bearer token. Paths are kept in
one place because the mailbox API may evolve independently from this agent.
"""
import re
import httpx
from app.config import settings

class MailboxError(RuntimeError): pass

class MailboxClient:
    def __init__(self):
        self.base = settings.mailbox_api_base_url.rstrip('/')

    def _headers(self):
        h = {"Accept": "application/json", "Content-Type": "application/json"}
        if settings.mailbox_api_token:
            h["Authorization"] = f"Bearer {settings.mailbox_api_token}"
        if settings.mailbox_api_cookie:
            h["Cookie"] = settings.mailbox_api_cookie
        return h

    @staticmethod
    def _identifier(value: str, field: str) -> str:
        value = str(value or "")
        if not value or not re.fullmatch(r"[0-9]+", value):
            raise MailboxError(f"Invalid mailbox {field}")
        return value

    async def request(self, method: str, path: str, *, params=None, payload=None):
        if not self.base:
            raise MailboxError("MAILBOX_API_BASE_URL is not configured")
        if not path.startswith('/') or path.startswith('//'):
            raise MailboxError("Mailbox path must be a relative API path")
        async with httpx.AsyncClient(timeout=settings.mailbox_timeout_seconds, follow_redirects=False) as client:
            response = await client.request(method, self.base + path, headers=self._headers(), params=params, json=payload)
        if response.status_code >= 400:
            raise MailboxError(f"Mailbox API returned HTTP {response.status_code}")
        try: return response.json()
        except ValueError: return {"text": response.text[:2000]}

    async def health(self):
        return await self.request('GET', '/healthz')

    async def session(self):
        return await self.request('GET', '/session')

    async def restore_session(self, alias_email: str):
        return await self.request('POST', '/session/restore', payload={'alias_email': self._identifier(alias_email, 'email')})

    async def recover_session(self, payload=None):
        return await self.request('POST', '/session/recovery', payload=payload or {})

    async def list_accounts(self):
        return await self.request('GET', '/gmail/accounts')

    async def list_aliases(self):
        return await self.request('GET', '/aliases')

    async def get_alias(self, alias_id: str):
        return await self.request('GET', f'/aliases/{self._identifier(alias_id, "alias ID")}')

    async def update_alias(self, alias_id: str, payload: dict):
        return await self.request('PATCH', f'/aliases/{self._identifier(alias_id, "alias ID")}', payload=payload)

    async def delete_alias(self, alias_id: str):
        return await self.request('DELETE', f'/aliases/{self._identifier(alias_id, "alias ID")}')

    async def create_alias(self, *, address_type='alias', variation_type='dots', variation_label=None, custom_name=None, gmail_account_id=None, use_primary=False):
        body = {"address_type": address_type, "variation_type": variation_type, "use_primary": use_primary}
        for key, value in {"variation_label": variation_label, "custom_name": custom_name, "gmail_account_id": gmail_account_id}.items():
            if value is not None: body[key] = value
        return await self.request('POST', '/aliases', payload=body)

    async def list_alias_messages(self, alias_id: str, params=None):
        return await self.request('GET', f'/aliases/{self._identifier(alias_id, "alias ID")}/messages', params=params)

    async def list_messages(self, params=None):
        return await self.request('GET', '/messages', params=params)

    async def get_message(self, message_id: str):
        return await self.request('GET', f'/messages/{self._identifier(message_id, "message ID")}')

    async def find_verification_code(self, pattern: str = r"\b\d{4,8}\b"):
        """Return the newest numeric verification code without returning message contents."""
        messages = await self.list_messages(params={"limit": 50, "offset": 0})
        rows = messages if isinstance(messages, list) else messages.get("messages", []) if isinstance(messages, dict) else []
        code_pattern = re.compile(pattern)
        for message in rows:
            if not isinstance(message, dict):
                continue
            haystack = " ".join(str(message.get(key) or "") for key in ("subject", "snippet", "body_text"))
            match = code_pattern.search(haystack)
            if match:
                return {"code": match.group(0), "message_id": message.get("id"), "subject": str(message.get("subject") or "")[:160]}
        return None

    async def get_attachment(self, message_id: str, attachment_id: str):
        return await self.request('GET', f'/messages/{self._identifier(message_id, "message ID")}/attachments/{self._identifier(attachment_id, "attachment ID")}')

mailbox = MailboxClient()
