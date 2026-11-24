@api @live @requires.youtube_owner.refresh_token
Feature: YouTube uploads on your own channel, through OAuth
  The Google account that gave consent (ops/openbao/google_oauth.py) owns the uploads; its refresh token comes from
  OpenBao at run time. Every video is private, carries the scenario's {{cid}} in its title and is deleted when the
  scenario ends, passed or failed. Without the refresh token, these scenarios are skipped.

  Background:
    Given the "youtube_owner" API

  @smoke
  Scenario: An uploaded video is processed and gets an English caption track
    When I upload the video "testdata/media/e2e-landscape.mp4" titled "behave-e2e {{cid}}"
    Then the response status is 200
    And the response field "status.privacyStatus" is "private"
    And the uploaded video is processed within 300 seconds
    When I add the caption track "testdata/media/e2e.en.vtt" in "en" named "E2E captions {{cid}}"
    Then the response status is 200
    When I GET "/captions?part=snippet&videoId={{video_id}}"
    Then the response status is 200
    And the response field "items" has 1 item
    And the response field "items[0].snippet.language" is "en"
    And the response field "items[0].snippet.name" is "E2E captions {{cid}}"
    And the uploaded video is deleted

  @regression
  Scenario: A vertical clip under 3 minutes is uploaded with the shape YouTube makes a Short
    # The API has no Shorts flag: YouTube makes a Short of a square or vertical video of 3 minutes at most.
    When I upload the video "testdata/media/e2e-vertical.mp4" titled "behave-e2e {{cid}} #Shorts"
    Then the response status is 200
    And the uploaded video is processed within 300 seconds
    When I GET "/videos?part=fileDetails&id={{video_id}}"
    Then the response status is 200
    And the response field "items[0].fileDetails.videoStreams[0].widthPixels" is 360
    And the response field "items[0].fileDetails.videoStreams[0].heightPixels" is 640
    And the response field "items[0].fileDetails.durationMs" is at most 180000
    And the uploaded video is deleted

  @regression @keep
  Scenario: A captioned video is uploaded and kept on the channel
    # Left out of normal runs: queue it with tags "@keep". The video stays, private; its YouTube Studio link is in the
    # report. Delete it there when done.
    When I upload the video "testdata/media/e2e-landscape.mp4" titled "behave-e2e kept {{cid}}"
    Then the response status is 200
    And the uploaded video is processed within 300 seconds
    When I add the caption track "testdata/media/e2e.en.vtt" in "en" named "E2E captions {{cid}}"
    Then the response status is 200
    And the uploaded video is kept
