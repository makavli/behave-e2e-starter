@api @live @publish @requires.youtube_owner.refresh_token @requires.publish.content
Feature: Publish a content folder on your own channel
  The pipeline's "publish" input names a folder under content/: video.mp4, meta.toml (title, description, tags,
  category_id, language, [captions]) and one captions.<language>.vtt per [captions] entry. The video is uploaded
  private (pick the visibility in YouTube Studio afterwards), checked and kept; its YouTube Studio link is in the
  report. An optional publish_at in meta.toml (unquoted, with its offset: publish_at = 2026-12-07T18:00:00+01:00)
  schedules it: YouTube itself makes it public then. A failure before the last step deletes it. Without a folder
  named, this feature is skipped.

  Background:
    Given the "youtube_owner" API

  @regression
  Scenario: The named content is uploaded, processed, captioned and kept
    When I upload the content to publish
    Then the response status is 200
    And the uploaded video is processed within 600 seconds
    And the uploaded video's publish date is the content's
    When I add the content's caption tracks
    Then the content's caption tracks are listed
    And the uploaded video is kept
