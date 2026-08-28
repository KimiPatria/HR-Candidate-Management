"""One-time Flash Avatar training.

Turns a video of a real, consenting person into a reusable avatar `resource_id`. This
is BytePlus's Visual/CV service (`byteplus_sdk.visual.VisualService`), an async batch
job queue - submit a training video, poll until done, get an id back. It is entirely
separate from the interview's live turn loop and from BytePlus RTC, which is why this
is a standalone script rather than part of the running app: you run it once per avatar,
not per interview or per candidate.

CONSENT: the input video must be of someone who has given written consent to have their
likeness used this way.

Setup:
    cd backend
    uv pip install -e ".[avatar]"
    # BYTEPLUS_ACCESS_KEY / BYTEPLUS_SECRET_KEY must already be set in backend/.env -
    # this reuses those account keys, it is not a separate credential.

Usage:
    .venv/Scripts/python scripts/train_avatar.py <video_url> [alpha_url]

`video_url` must be a public HTTPS URL BytePlus's servers can fetch (their own example
uses a bytepluscdn.com link) - a local file path will not work. If you don't have
somewhere to host the template video, ask whoever manages your BytePlus account about a
TOS (object storage) bucket, or use any file host that gives a direct HTTPS link.

Prints the resulting resource_id to paste into backend/.env as AVATAR_ID.

VERIFY: whether this resource_id is literally the value BytePlus RTC's live
AvatarConfig expects (currently guessed as ProviderParams.AvatarId in
app/services/byteplus_rtc.py) is not yet confirmed against real RTC+Avatar integration
docs - only that this is how you obtain a resource_id in the first place.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402

REQ_KEY = "realman_lipsync_training_tob"
POLL_INTERVAL_SECONDS = 5
POLL_TIMEOUT_SECONDS = 600
TERMINAL_STATUSES = {"generating", "in_queue"}


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: train_avatar.py <video_url> [alpha_url]")
        return 1
    video_url = sys.argv[1]
    alpha_url = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        from byteplus_sdk.visual.VisualService import VisualService
    except ImportError:
        print('Missing dependency. Run: uv pip install -e ".[avatar]"')
        return 1

    if not settings.byteplus_access_key or not settings.byteplus_secret_key:
        print("Set BYTEPLUS_ACCESS_KEY and BYTEPLUS_SECRET_KEY in backend/.env first.")
        return 1

    service = VisualService()
    service.set_ak(settings.byteplus_access_key)
    service.set_sk(settings.byteplus_secret_key)

    req_body = {"req_key": REQ_KEY, "video_url": video_url}
    if alpha_url:
        req_body["alpha_url"] = alpha_url

    print(f"Submitting training job for {video_url} ...")
    resp = service.cv_submit_task(req_body)
    if resp.get("code") != 10000:
        print(f"Submit failed: {resp}")
        return 1
    task_id = resp["data"]["task_id"]
    print(f"Task ID: {task_id}")

    deadline = time.time() + POLL_TIMEOUT_SECONDS
    while time.time() < deadline:
        result = service.cv_get_result({"req_key": REQ_KEY, "task_id": task_id})
        if result.get("code") != 10000:
            print(f"Poll failed: {result}")
            return 1

        status = result["data"]["status"]
        if status == "done":
            resource_id = json.loads(result["data"]["resp_data"])["resource_id"]
            print("\nTraining complete.")
            print(f"resource_id: {resource_id}")
            print("\nAdd this to backend/.env as:")
            print(f"AVATAR_ID={resource_id}")
            return 0

        if status in TERMINAL_STATUSES:
            print(f"Status: {status}, waiting {POLL_INTERVAL_SECONDS}s...")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        print(f"Training did not complete: status={status}, response={result}")
        return 1

    print("Timed out waiting for training to complete.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
