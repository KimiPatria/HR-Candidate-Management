"""Live probe of BytePlus Translate, in the mould of `probe_speech.py`.

Nothing in the app should call Translate until this passes, because three things about
the integration are taken from documentation rather than from a live call, and each one
fails in a way that is easy to misread:

  1. HOST / REGION / VERSION. Translate is documented at open.byteplusapi.com, service
     `translate`, region `ap-singapore-1`, Version 2020-06-01 - note the region differs
     from RTC's ap-southeast-1. Sign with the wrong region and the request is rejected
     for a bad signature, which looks identical to a bad secret key.
  2. INDONESIAN. `id` is not in the language list published on the pages reachable
     without a console login, and this whole feature exists to serve an EN/ID deployment.
     Error -415 is "unsupported language pair". If that is what comes back, the answer is
     to route translation through ModelArk instead - a different design, not a flag.
  3. THE DETECTION ACTION'S NAME. The docs describe a "Language Detection API" without
     naming its Action in the text we could reach, so this tries the plausible names and
     reports which one the API actually answers to.

Run:
    cd backend && .venv/Scripts/python.exe -m scripts.probe_translate

Costs a few hundred translated characters. Prints every request and response verbatim.
"""

import asyncio
import json
import sys

import httpx

from app.core.config import settings
from app.services import byteplus_sign

_SERVICE = "translate"

EN = "Tell me about a time you had to deliver a project under a tight deadline."
ID = "Saya bergabung sebagai manager proyek di perusahaan teknologi selama tiga tahun."

# The docs describe the detection endpoint without naming its Action where we could read
# it. Volcengine's equivalent is LangDetect; BytePlus may have renamed it. Try both.
DETECT_ACTIONS = ["LangDetect", "DetectLanguage", "DetectText"]


async def call(action: str, payload: dict, *, version: str | None = None) -> dict | None:
    """One signed Translate call, printing both directions. None on transport failure."""
    version = version or settings.translate_api_version
    print(f"\n--> {action}  {json.dumps(payload, ensure_ascii=False)[:300]}")
    try:
        body = await byteplus_sign.call(
            service=_SERVICE,
            base_url=settings.translate_openapi_base,
            region=settings.translate_region,
            action=action,
            version=version,
            payload=payload,
        )
    except httpx.HTTPStatusError as exc:
        # The interesting part of a rejection is the body, not the status line: a bad
        # region and a bad key both return 4xx, and only the message tells them apart.
        print(f"<-- HTTP {exc.response.status_code}")
        print(f"    {exc.response.text[:800]}")
        return None
    except Exception as exc:  # noqa: BLE001 - a probe reports failures, it does not raise
        print(f"<-- TRANSPORT FAILURE: {type(exc).__name__}: {exc}")
        return None

    print(f"<-- {json.dumps(body, ensure_ascii=False)[:800]}")
    return body


def error_of(body: dict | None) -> dict | None:
    if not body:
        return None
    return (body.get("ResponseMetadata") or {}).get("Error")


def translations_of(body: dict | None) -> list[str]:
    if not body:
        return []
    return [t.get("Translation", "") for t in (body.get("TranslationList") or [])]


async def main() -> int:
    if not (settings.byteplus_access_key and settings.byteplus_secret_key):
        sys.exit("BYTEPLUS_ACCESS_KEY / BYTEPLUS_SECRET_KEY are not set in backend/.env")
    if settings.mock_ai:
        print("NOTE: MOCK_AI=true, but this script always calls the real API.\n")

    print("=" * 78)
    print(f"host    {settings.translate_openapi_base}")
    print(f"service {_SERVICE}")
    print(f"region  {settings.translate_region}   (RTC uses {settings.byteplus_region})")
    print(f"version {settings.translate_api_version}")
    print(f"key     {settings.byteplus_access_key[:6]}...")
    print("=" * 78)

    findings: list[tuple[bool, str]] = []

    # --- 1. Does anything sign correctly at all? en -> zh is the safest pair to ask for,
    # so a failure here is unambiguously about host/region/version, not about Indonesian.
    print("\n### 1. signing + reachability (en -> zh)")
    body = await call(
        "TranslateText",
        {"SourceLanguage": "en", "TargetLanguage": "zh", "TextList": [EN]},
    )
    err = error_of(body)
    signed_ok = bool(translations_of(body)) and not err
    findings.append(
        (signed_ok, f"host/region/version accepted (region={settings.translate_region})")
    )
    if not signed_ok:
        # Distinguish the two very different reasons this can fail. If the response echoes
        # our Action/Version/Service/Region back at us, the request was parsed and
        # authenticated - the signature is fine and only the account is not entitled.
        # Sending someone to re-check the region in that case wastes an afternoon.
        meta = (body or {}).get("ResponseMetadata") or {}
        signature_accepted = bool(meta.get("Service") == _SERVICE and meta.get("Action"))
        code = str((err or {}).get("Code", ""))
        message = str((err or {}).get("Message", ""))

        print("\n  The en -> zh call failed, so everything below would fail too.")
        if err:
            print(f"  Error: {err}")

        if signature_accepted:
            print("\n  BUT the signature was ACCEPTED: the response echoes back")
            print(f"    Action={meta.get('Action')}  Version={meta.get('Version')}")
            print(f"    Service={meta.get('Service')}  Region={meta.get('Region')}")
            print("  which the API only does once it has authenticated the request. So")
            print("  TRANSLATE_OPENAPI_BASE, TRANSLATE_REGION, TRANSLATE_API_VERSION and")
            print("  the signing in app/services/byteplus_sign.py are all CORRECT.")
            findings[-1] = (
                True,
                f"host/region/version/signing accepted (region={settings.translate_region})",
            )
            findings.append((False, f"account entitled to Translate ({code} {message})"))

            if code == "-403" or "account status" in message:
                print("\n  This is an ACCOUNT PROVISIONING problem, not a code problem.")
                print("  'account status abnormal: unsynchronized' is what BytePlus returns")
                print("  for a service the account has not activated, or whose activation")
                print("  has not propagated yet.")
                print("\n  Fix it in the console, then re-run this probe:")
                print("    console.byteplus.com -> Translate -> activate the service")
                print("    docs.byteplus.com/en/docs/translate/"
                      "docs-activating-byteplus-translate-services")
                print("  Newly activated services can take a while to sync; if it still")
                print("  says 'unsynchronized' well after activation, that is one for")
                print("  BytePlus support, not something to work around in code.")
        else:
            print("\n  The request was rejected before authentication. Check, in order:")
            print("    TRANSLATE_REGION   - ap-singapore-1, NOT the RTC ap-southeast-1")
            print("    TRANSLATE_OPENAPI_BASE")
            print("    BYTEPLUS_ACCESS_KEY / BYTEPLUS_SECRET_KEY")

        return summarise(findings)

    # --- 2. Indonesian, both directions. This is the gate the whole feature sits behind.
    print("\n### 2. Indonesian as a target (en -> id)")
    body = await call(
        "TranslateText",
        {"SourceLanguage": "en", "TargetLanguage": "id", "TextList": [EN]},
    )
    en_to_id = translations_of(body)
    err = error_of(body)
    ok_to_id = bool(en_to_id and en_to_id[0].strip()) and not err
    findings.append((ok_to_id, "en -> id supported"))
    if err:
        print(f"    Error: {err}   (-415 means the language pair is unsupported)")

    print("\n### 3. Indonesian as a source (id -> en)")
    body = await call(
        "TranslateText",
        {"SourceLanguage": "id", "TargetLanguage": "en", "TextList": [ID]},
    )
    id_to_en = translations_of(body)
    ok_from_id = bool(id_to_en and id_to_en[0].strip()) and not error_of(body)
    findings.append((ok_from_id, "id -> en supported"))
    if ok_from_id:
        print(f"\n    source: {ID}")
        print(f"    result: {id_to_en[0]}")
        print("    ^ read this - a passing status code with garbage output is the failure")
        print("      mode that matters, and only a human can see it.")

    # --- 4. Auto-detection via an omitted SourceLanguage, which is what the app will do
    # for documents whose language nobody declared.
    print("\n### 4. omitted SourceLanguage (auto-detect, Indonesian input)")
    body = await call("TranslateText", {"TargetLanguage": "en", "TextList": [ID]})
    detected = None
    if body and body.get("TranslationList"):
        detected = body["TranslationList"][0].get("DetectedSourceLanguage")
    findings.append((detected == "id", f"auto-detect returned {detected!r} (want 'id')"))

    # --- 5. Which Action the detection endpoint answers to.
    print("\n### 5. language-detection Action name")
    detect_action = None
    for action in DETECT_ACTIONS:
        body = await call(action, {"TextList": [ID]})
        if body and not error_of(body):
            detect_action = action
            print(f"    ^ '{action}' is the one. Use this in services/translate.py.")
            break
    findings.append(
        (bool(detect_action), f"detection Action = {detect_action or 'none of ' + str(DETECT_ACTIONS)}")
    )

    # --- 6. Batch limit. Docs say 8; the app batches to that number, so confirm it rather
    # than discovering it as a partial failure on somebody's 9-turn transcript.
    print("\n### 6. batch limit (docs say 8 items per request)")
    body = await call(
        "TranslateText",
        {
            "SourceLanguage": "en",
            "TargetLanguage": "id",
            "TextList": [f"Sentence number {i}." for i in range(8)],
        },
    )
    eight = len(translations_of(body))
    findings.append((eight == 8, f"8 items returned {eight} translations"))

    body = await call(
        "TranslateText",
        {
            "SourceLanguage": "en",
            "TargetLanguage": "id",
            "TextList": [f"Sentence number {i}." for i in range(9)],
        },
    )
    nine = len(translations_of(body))
    nine_err = error_of(body)
    print(f"    9 items -> {nine} translations, error={nine_err}")
    if nine == 9 and not nine_err:
        print("    NOTE: 9 was accepted. The documented cap of 8 is conservative;")
        print("    services/translate.py batches at 8 anyway, which is still correct.")

    return summarise(findings)


def summarise(findings: list[tuple[bool, str]]) -> int:
    print("\n" + "=" * 78)
    for ok, label in findings:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    print("=" * 78)

    blockers = [label for ok, label in findings if not ok]
    if not blockers:
        print("\nAll checks passed. BytePlus Translate is usable for EN/ID.")
        return 0
    print(f"\n{len(blockers)} check(s) failed:")
    for label in blockers:
        print(f"  - {label}")
    print("\nIf the Indonesian checks are the ones failing, do NOT work around it here.")
    print("The fallback is to route translation through the existing ModelArk client,")
    print("which is a different design and should be decided, not defaulted into.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
