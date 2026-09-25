"""Live probe of Dreamina OmniHuman: photo + audio -> talking-head mp4.

Answers the questions that decide whether the opening-greeting clip is worth building,
none of which can be settled from the docs reachable without a console login:

  1. WHICH req_key. The OmniHuman task key is not published on the open docs pages. This
     tries a list of candidates and reports which one the API accepts, the same way the
     Akool-vs-native avatar question was settled - by one live call, not by guessing.
  2. CAN INPUTS BE INLINE. If the API accepts base64 instead of a URL, the whole public
     tunnel dependency disappears - we would not need BytePlus to reach back into this
     machine at all. This is the single most valuable thing the probe can discover, so it
     tries base64 first and falls back to URLs.
  3. HOW LONG, AND HOW MUCH. Prints wall-clock generation time and the billed duration,
     so the cost model in the plan can be checked against reality before anyone enables
     this for real candidates.
  4. IS IT ANY GOOD. Saves the mp4 locally. A green status code on an uncanny video is
     still a failure, and only a person watching it can tell.

This deliberately uses the official synchronous `byteplus_sdk` VisualService, exactly
like `scripts/train_avatar.py` - the only BytePlus Visual/CV path proven to work on this
account. Do NOT reach for `app/services/byteplus_sign.py` here: that signer is verified
against the RTC OpenAPI, and the SDK's SignerV4 builds its canonical request differently
(it derives the signed-header list from whichever headers are present). Each is right for
its own surface.

CONSENT: the photo must be of someone who has given written consent to have their
likeness animated this way. Same rule as train_avatar.py.

Setup:
    cd backend
    uv pip install -e ".[avatar]"
    # BYTEPLUS_ACCESS_KEY / BYTEPLUS_SECRET_KEY must already be in backend/.env.

Usage:
    .venv/Scripts/python.exe scripts/probe_omnihuman.py <image> <audio>

<image> and <audio> may each be a local path or a public https URL. Local files are sent
as base64 when the API allows it; if it does not, the probe says so and you will need a
public URL (a TOS bucket, or any host giving a direct link).
"""

import base64
import json
import mimetypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402

# OmniHuman's task key is not in the open docs. These are the plausible names, ordered by
# how likely they look given the sibling keys we know are real
# ("realman_lipsync_training_tob" is the confirmed Flash Avatar training key).
CANDIDATE_REQ_KEYS = [
    "realman_avatar_picture_omni_v2",
    "realman_avatar_picture_omni",
    "realman_avatar_picture",
    "omnihuman_video_generation",
    "omni_human_v1",
]

POLL_INTERVAL_SECONDS = 5
PENDING_STATUSES = {"generating", "in_queue", "pending", "running"}


def load_input(value: str) -> tuple[str, str | None]:
    """Return (kind, base64) for one input. kind is 'url' or 'file'."""
    if value.startswith(("http://", "https://")):
        return "url", None
    path = Path(value)
    if not path.is_file():
        sys.exit(f"Not a URL and not a file: {value}")
    return "file", base64.b64encode(path.read_bytes()).decode()


def describe(value: str) -> str:
    if value.startswith(("http://", "https://")):
        return value
    p = Path(value)
    mime = mimetypes.guess_type(p.name)[0] or "?"
    return f"{p.name} ({p.stat().st_size / 1024:.0f} KB, {mime})"


def build_payload(req_key: str, image: str, audio: str) -> dict:
    """Assemble one submit body, inlining whichever inputs are local files."""
    payload: dict = {"req_key": req_key}

    image_kind, image_b64 = load_input(image)
    if image_kind == "url":
        payload["image_url"] = image
    else:
        # The CV API's usual convention for inline images. If OmniHuman does not accept
        # it, the error will say so and the probe reports that as its finding.
        payload["binary_data_base64"] = [image_b64]

    audio_kind, audio_b64 = load_input(audio)
    if audio_kind == "url":
        payload["audio_url"] = audio
    else:
        payload["audio_binary_data_base64"] = audio_b64

    return payload


def redact(payload: dict) -> dict:
    """Payload with the base64 blobs replaced by their sizes, for printing."""
    out = {}
    for k, v in payload.items():
        if "base64" in k:
            blob = v[0] if isinstance(v, list) and v else v
            out[k] = f"<{len(blob) if isinstance(blob, str) else 0} base64 chars>"
        else:
            out[k] = v
    return out


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__.split("Usage:")[1].strip() if "Usage:" in __doc__ else "")
        print("\nUsage: probe_omnihuman.py <image_path_or_url> <audio_path_or_url>")
        return 1
    image, audio = sys.argv[1], sys.argv[2]

    try:
        from byteplus_sdk.visual.VisualService import VisualService
    except ImportError:
        print('Missing dependency. Run: uv pip install -e ".[avatar]"')
        return 1

    if not (settings.byteplus_access_key and settings.byteplus_secret_key):
        print("Set BYTEPLUS_ACCESS_KEY and BYTEPLUS_SECRET_KEY in backend/.env first.")
        return 1

    service = VisualService()
    service.set_ak(settings.byteplus_access_key)
    service.set_sk(settings.byteplus_secret_key)

    print("=" * 78)
    print(f"image  {describe(image)}")
    print(f"audio  {describe(audio)}")
    configured = settings.omnihuman_req_key
    keys = [configured] if configured else CANDIDATE_REQ_KEYS
    print(f"req_key{'  ' + configured + '  (from OMNIHUMAN_REQ_KEY)' if configured else 's  trying ' + str(keys)}")
    print("=" * 78)

    # --- 1. Find a req_key the API accepts.
    req_key = None
    task_id = None
    for candidate in keys:
        payload = build_payload(candidate, image, audio)
        print(f"\n--> cv_submit_task  {json.dumps(redact(payload))[:400]}")
        try:
            resp = service.cv_submit_task(payload)
        except Exception as exc:  # noqa: BLE001 - a probe reports, it does not raise
            print(f"<-- TRANSPORT FAILURE: {type(exc).__name__}: {exc}")
            continue
        print(f"<-- {json.dumps(resp, ensure_ascii=False)[:600]}")
        if resp.get("code") == 10000:
            req_key = candidate
            task_id = resp["data"]["task_id"]
            print(f"\n  ACCEPTED: req_key={req_key}  task_id={task_id}")
            break
        print(f"    rejected: code={resp.get('code')} message={resp.get('message')!r}")

    if not req_key:
        print("\n" + "=" * 78)
        print("FAIL  no req_key was accepted.")
        print("=" * 78)
        print("\nEvery candidate key was rejected. Two possibilities, in order:")
        print("  1. The right key is simply not in the list above - get it from the")
        print("     BytePlus console (Vision AI -> OmniHuman) and set OMNIHUMAN_REQ_KEY.")
        print("  2. This account has OmniHuman through ModelArk's video-generation task")
        print("     API rather than Vision AI/CV, in which case this is the wrong client")
        print("     entirely and the intro-clip work needs replanning against ModelArk.")
        print("\nIf the rejection message mentions base64 or a missing url, re-run with")
        print("public https URLs instead of local files - that also answers question 2.")
        return 1

    # --- 2. Poll to completion, timing it.
    print(f"\nPolling (timeout {settings.omnihuman_poll_timeout_seconds}s)...")
    started = time.time()
    deadline = started + settings.omnihuman_poll_timeout_seconds
    result = None
    while time.time() < deadline:
        result = service.cv_get_result({"req_key": req_key, "task_id": task_id})
        if result.get("code") != 10000:
            print(f"<-- poll failed: {json.dumps(result, ensure_ascii=False)[:600]}")
            return 1
        status = result["data"].get("status")
        elapsed = time.time() - started
        print(f"    [{elapsed:6.1f}s] status={status}")
        if status == "done":
            break
        if status not in PENDING_STATUSES:
            print(f"\nFAIL  unexpected terminal status: {json.dumps(result)[:600]}")
            return 1
        time.sleep(POLL_INTERVAL_SECONDS)
    else:
        print(f"\nFAIL  timed out after {settings.omnihuman_poll_timeout_seconds}s.")
        print("This is itself a finding: raise OMNIHUMAN_POLL_TIMEOUT_SECONDS, and note")
        print("that generation is slower than the intro-clip design assumed.")
        return 1

    generation_seconds = time.time() - started

    # --- 3. Report everything the cost model needs.
    data = result["data"]
    resp_data = data.get("resp_data")
    if isinstance(resp_data, str):
        try:
            resp_data = json.loads(resp_data)
        except ValueError:
            pass
    print(f"\n<-- resp_data: {json.dumps(resp_data, ensure_ascii=False)[:800]}")

    video_url = None
    if isinstance(resp_data, dict):
        video_url = resp_data.get("video_url") or resp_data.get("url")
    if not video_url and data.get("video_url"):
        video_url = data["video_url"]

    out = Path("data") / f"omnihuman-probe-{task_id[:12]}.mp4"
    saved = False
    if video_url:
        try:
            import httpx

            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(httpx.get(video_url, timeout=120.0, follow_redirects=True).content)
            saved = True
        except Exception as exc:  # noqa: BLE001
            print(f"    could not download the video: {exc}")

    print("\n" + "=" * 78)
    print(f"  PASS  req_key            {req_key}")
    print(f"        generation time    {generation_seconds:.1f}s wall clock")
    inline = [k for k in build_payload(req_key, image, audio) if "base64" in k]
    print(f"        inline inputs      {'accepted: ' + str(inline) if inline else 'not exercised (URLs given)'}")
    if inline:
        print("        ^ base64 worked, so the intro clip needs NO public tunnel.")
    print(f"        video_url          {video_url or 'NOT FOUND in response - see resp_data above'}")
    if saved:
        print(f"        saved              {out}  ({out.stat().st_size / 1024 / 1024:.1f} MB)")
        print("\n  Now WATCH IT. A green status code on an uncanny or badly lip-synced")
        print("  video is still a failure, and the plan's whole case for OmniHuman is")
        print("  that the greeting looks better than a waveform.")
    print("=" * 78)
    print("\nNext: set OMNIHUMAN_REQ_KEY in backend/.env to the value above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
