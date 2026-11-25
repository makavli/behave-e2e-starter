"""YouTube on your own channel (OAuth): upload a private video, wait until it is processed, add a caption track.

Every uploaded video is deleted when its scenario ends, passed or failed (after_scenario runs context.cleanups),
unless the scenario reaches "the uploaded video is kept".
"""
import logging
import time
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import allure
from behave import then, when

from e2e.config import need
from e2e.poll import Fail, eventually
from e2e.rest import expect_ok
from e2e.session import remember_response, render

ROOT = Path(__file__).resolve().parents[2]
CONTENT = ROOT / "content"
POLL_S = 5  # each check is a videos.list call (1 quota unit): never faster, whatever [run] poll_s says


def _upload(context, path, metadata, data, media_type):
    started = time.perf_counter()
    response = context.e2e.client(context.api).upload(path, metadata, data, media_type)
    remember_response(context, response, started)
    return response


def _channel_guard(body, want):
    """The token must act for [youtube_owner] channel_id: a weekly re-consent with the wrong account uploads there."""
    got = [item["id"] for item in body.get("items", [])]
    if got != [want]:
        raise AssertionError(f"the OAuth token acts for channel {got or 'none'}, not channel_id {want}: run "
                             "ops/openbao/google_oauth.py again, signed in with that channel's account")


def _upload_video(context, metadata, data, publish_at=None):
    """Upload it private; on success, remember its id and register its delete (cancelled once deleted or kept)."""
    client = context.e2e.client(context.api)
    channel = expect_ok(client.call("GET", "/channels?part=id&mine=true"), "the token's channel")  # 1 quota unit
    _channel_guard(channel.json(), need(client.cfg, context.api, "channel_id"))
    metadata["status"] = {"privacyStatus": "private", "selfDeclaredMadeForKids": False}
    if publish_at:  # YouTube makes it public at that time, by itself: no run, no PC needed then
        metadata["status"]["publishAt"] = publish_at
    # shortcut: a video created whose answer is lost (timeout, dropped connection) has no id here and stays; its title
    # carries the run's cid to find it in YouTube Studio. Upgrade with a search.list forMine cleanup (100 units).
    response = _upload(context, "/videos?part=snippet,status&uploadType=multipart", metadata, data, "video/mp4")
    if response.ok:
        client, video_id = context.e2e.client(context.api), response.json()["id"]
        context.vars["video_id"] = video_id

        def cleanup():  # the backstop when the scenario stopped before its delete or keep step
            if context.vars.get("video_id") == video_id:
                _delete(client, video_id)
        context.cleanups.append(cleanup)


def _add_captions(context, path, language, name):
    metadata = {"snippet": {"videoId": context.vars["video_id"], "language": language, "name": name, "isDraft": False}}
    return _upload(context, "/captions?part=snippet&uploadType=multipart", metadata, path.read_bytes(),
                   "application/octet-stream")


def _file(folder, name):
    """A content folder's own file. A link leading out of the folder (a clone can carry one) or a caption code with
    a path in it would send some other local file to YouTube: refused."""
    path = (folder / name).resolve()
    if path.parent != folder or not path.is_file():
        raise AssertionError(f"content/{folder.name}: '{name}' is not a file of that folder")
    return path


def _content(context):
    """The folder the publish input names: content/<name>, with its meta.toml read."""
    name = context.e2e.cfg.section("publish")["content"]
    folder = (CONTENT / name).resolve()
    if folder.parent != CONTENT.resolve() or not (folder / "meta.toml").is_file():  # a name, never a path
        raise AssertionError(f"publish = '{name}': no content/<name> folder with a meta.toml")
    return folder, tomllib.loads(_file(folder, "meta.toml").read_text(encoding="utf-8"))


def _publish_at(meta, now=None):
    """meta.toml's optional publish_at as YouTube's status.publishAt (UTC). It must be an unquoted TOML date-time
    with its offset (a bare local time is ambiguous), at least an hour ahead (processing and captions come first)."""
    if "publish_at" in meta.get("captions", {}):  # TOML puts a key below a [table] line inside that table
        raise AssertionError("meta.toml: publish_at is under [captions]; move it above that line")
    when = meta.get("publish_at")
    if when is None:
        return None
    if not isinstance(when, datetime) or when.tzinfo is None:
        raise AssertionError(f"publish_at = {when!r}: write it unquoted with its offset, e.g. 2026-12-07T18:00:00+01:00")
    if when < (now or datetime.now(timezone.utc)) + timedelta(hours=1):
        raise AssertionError(f"publish_at = {when.isoformat()}: less than an hour ahead")
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _delete(client, video_id, tries=3):
    for _ in range(tries):  # DELETE is not in the session's retries: a 5xx or a quota blip gets two more tries here
        response = client.call("DELETE", f"/videos?id={video_id}")
        if response.status_code in (204, 404):
            logging.info("deleted video %s", video_id)
            return
        time.sleep(POLL_S)
    raise RuntimeError(f"video {video_id} not deleted: HTTP {response.status_code} {response.text[:200]}")


@when('I upload the video "{file:S}" titled "{title:S}"')
def upload_video(context, file, title):
    snippet = {"title": render(context, title), "categoryId": "28",
               "description": f"behave E2E test upload ({context.vars['cid']})"}
    _upload_video(context, {"snippet": snippet}, (ROOT / file).read_bytes())


@when("I upload the content to publish")
def upload_content(context):
    folder, meta = context.content = _content(context)
    snippet = {"title": meta["title"], "description": meta["description"], "tags": meta.get("tags", []),
               "categoryId": meta.get("category_id", "28"), "defaultLanguage": meta["language"],
               "defaultAudioLanguage": meta["language"]}
    context.publish_at = _publish_at(meta)  # checked before anything is uploaded
    _upload_video(context, {"snippet": snippet}, _file(folder, "video.mp4").read_bytes(), context.publish_at)


@then("the uploaded video's publish date is the content's")
def video_publish_date(context):
    """Still private, and scheduled exactly as meta.toml's publish_at says (or not scheduled, without one)."""
    client, video_id = context.e2e.client(context.api), context.vars["video_id"]
    status = expect_ok(client.call("GET", f"/videos?part=status&id={video_id}"), "videos.list").json()["items"][0]["status"]
    got, want = status.get("publishAt"), getattr(context, "publish_at", None)  # None after a plain video upload
    assert status["privacyStatus"] == "private", f"video {video_id} is {status['privacyStatus']}"
    assert (got and datetime.fromisoformat(got)) == (want and datetime.fromisoformat(want)), \
        f"video {video_id}: publishAt {got!r}, meta.toml says {want!r}"


@then("the uploaded video is processed within {seconds:d} seconds")
def video_processed(context, seconds):
    client, video_id = context.e2e.client(context.api), context.vars["video_id"]

    def probe():
        response = expect_ok(client.call("GET", f"/videos?part=status&id={video_id}"), "videos.list")
        items = response.json().get("items")
        assert items, "the video is not listed yet"
        status = items[0]["status"]
        if status["uploadStatus"] in ("failed", "rejected", "deleted"):
            reason = status.get("failureReason") or status.get("rejectionReason")
            raise Fail(f"video {video_id}: upload {status['uploadStatus']} ({reason})")
        return status["uploadStatus"] == "processed"

    eventually(probe, timeout_s=seconds, poll_s=POLL_S, what=f"video {video_id} processed")


@then("the uploaded video is deleted")
def video_deleted(context):
    """A step, not only a cleanup: a cleanup's failure is a warning, this one turns the scenario red."""
    client, video_id = context.e2e.client(context.api), context.vars["video_id"]
    _delete(client, video_id)
    context.vars["video_id"] = None  # nothing left for the cleanup

    def gone():
        response = expect_ok(client.call("GET", f"/videos?part=id&id={video_id}"), "videos.list")
        return not response.json().get("items")

    eventually(gone, timeout_s=60, poll_s=POLL_S, what=f"video {video_id} no longer listed")


@then("the uploaded video is kept")
def video_kept(context):
    """Only a scenario that got this far keeps its video: an earlier failure still deletes it."""
    video_id = context.vars["video_id"]
    context.vars["video_id"] = None  # nothing left for the cleanup
    link = f"https://studio.youtube.com/video/{video_id}/edit"
    allure.attach(link, name="kept video (YouTube Studio)", attachment_type=allure.attachment_type.URI_LIST)
    logging.warning("kept video %s: %s", video_id, link)


@when('I add the caption track "{file:S}" in "{language:S}" named "{name:S}"')
def add_captions(context, file, language, name):
    _add_captions(context, ROOT / file, language, render(context, name))


@when("I add the content's caption tracks")
def add_content_captions(context):
    folder, meta = context.content
    for language, name in meta.get("captions", {}).items():
        response = _add_captions(context, _file(folder, f"captions.{language}.vtt"), language, name)
        assert response.ok, f"caption track '{language}': HTTP {response.status_code} {response.text[:300]}"


@then("the content's caption tracks are listed")
def content_captions_listed(context):
    wanted = context.content[1].get("captions", {})
    client = context.e2e.client(context.api)
    response = expect_ok(client.call("GET", f"/captions?part=snippet&videoId={context.vars['video_id']}"), "captions")
    # Once it has transcribed the video, YouTube lists its own speech track too (trackKind asr, no name): not ours.
    listed = {s["language"]: s["name"] for s in (item["snippet"] for item in response.json().get("items", []))
              if s.get("trackKind") != "asr"}
    assert listed == wanted, f"caption tracks: expected {wanted}, got {listed}"
