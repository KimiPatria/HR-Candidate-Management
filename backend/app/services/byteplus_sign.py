"""Volc Engine V4 request signing, shared by every BytePlus OpenAPI this app calls.

Lifted verbatim out of `byteplus_rtc.py`, where it lived hardcoded to the RTC service,
and parameterised on (service, host, region) so a second OpenAPI - Translate - can reuse
it without a second copy of the algorithm. The provenance of the algorithm itself is
unchanged and still worth stating:

  - This implements the same signature scheme as the official `volcengine` PyPI package's
    `SignerV4.sign` (auth/SignerV4.py): a canonical request over method / path / query /
    signed-headers-block / signed-header-names / body-hash, then an HMAC-SHA256 signing
    key chain over secret-key -> date -> region -> service -> "request".
  - It is ported rather than imported because that package is synchronous and reads
    `~/.volc/credentials` at import time - not a fit for this async codebase.
  - It is confirmed against the LIVE RTC API, not just against reference source: a real
    signed StartVoiceChat call built with these headers returned `{"Result": "ok"}`.

DO NOT mix this with `byteplus_sdk`'s own `SignerV4` (used by scripts/train_avatar.py and
scripts/probe_omnihuman.py for the Visual/CV service). That one derives `signed_headers`
dynamically from whichever of Content-Type / Content-Md5 / Host / X-* are present, so the
two produce different canonical requests. Each is correct for the surface it was verified
against; neither is a drop-in for the other.

Three things vary per service and are therefore arguments, not constants:
  - `service`   - the credential-scope segment: "rtc", "translate", ...
  - `base_url`  - the host is derived from this, and it is also what gets signed
  - `region`    - NOT a single account-wide value. RTC is ap-southeast-1 while Translate
                  is documented as ap-singapore-1, so callers pass their own.
"""

import hashlib
import hmac
import json
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from app.core.config import settings

# Every BytePlus OpenAPI in this app posts a JSON body to `/` with the operation in the
# query string, so path and content-type are fixed rather than parameterised.
_CANONICAL_PATH = "/"
_CONTENT_TYPE = "application/json"
_SIGNED_HEADERS = "content-type;host;x-content-sha256;x-date"


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def signed_headers(
    *,
    service: str,
    base_url: str,
    region: str,
    action: str,
    version: str,
    body: bytes,
    access_key: str = "",
    secret_key: str = "",
) -> dict[str, str]:
    """Build the Authorization + X-Date + X-Content-Sha256 header set for one call.

    `access_key`/`secret_key` default to the account-wide pair in settings; both new
    integrations reuse it, so they are only arguments to keep this function testable
    without touching global config.
    """
    access_key = access_key or settings.byteplus_access_key
    secret_key = secret_key or settings.byteplus_secret_key

    now = datetime.now(timezone.utc)
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = x_date[:8]
    host = base_url.split("://", 1)[-1].split("/", 1)[0]
    payload_hash = hashlib.sha256(body).hexdigest()

    query = f"Action={quote(action)}&Version={quote(version)}"
    canonical_request = "\n".join(
        [
            "POST",
            _CANONICAL_PATH,
            query,
            f"content-type:{_CONTENT_TYPE}",
            f"host:{host}",
            f"x-content-sha256:{payload_hash}",
            f"x-date:{x_date}",
            "",
            _SIGNED_HEADERS,
            payload_hash,
        ]
    )
    credential_scope = f"{short_date}/{region}/{service}/request"
    string_to_sign = "\n".join(
        [
            "HMAC-SHA256",
            x_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )

    k_date = _sign(secret_key.encode(), short_date)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    k_signing = _sign(k_service, "request")
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

    return {
        "Content-Type": _CONTENT_TYPE,
        "Host": host,
        "X-Date": x_date,
        "X-Content-Sha256": payload_hash,
        "Authorization": (
            f"HMAC-SHA256 Credential={access_key}/{credential_scope}, "
            f"SignedHeaders={_SIGNED_HEADERS}, Signature={signature}"
        ),
    }


async def call(
    *,
    service: str,
    base_url: str,
    region: str,
    action: str,
    version: str,
    payload: dict,
    client: httpx.AsyncClient | None = None,
    timeout: float = 20.0,
) -> dict:
    """POST one signed OpenAPI request and return the decoded JSON.

    Pass `client` to reuse a pooled connection (see modelark.py for why that matters on a
    hot path); omit it and a short-lived client is opened for this call alone.
    """
    body = json.dumps(payload).encode()
    url = f"{base_url.rstrip('/')}/?Action={action}&Version={version}"
    headers = signed_headers(
        service=service,
        base_url=base_url,
        region=region,
        action=action,
        version=version,
        body=body,
    )

    if client is not None:
        resp = await client.post(url, headers=headers, content=body, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    async with httpx.AsyncClient(timeout=timeout) as owned:
        resp = await owned.post(url, headers=headers, content=body)
        resp.raise_for_status()
        return resp.json()
