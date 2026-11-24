@api @live @requires.youtube.api_key
Feature: YouTube Data API, called with a key kept in OpenBao
  A real third-party API. The pipeline reads the key from OpenBao at run time: it never sits in the repo,
  the config or Azure DevOps, and the agent masks it in the logs. Without the key, these scenarios are skipped.

  Background:
    Given the "youtube" API

  @smoke
  Scenario: A public video reads back through the API key
    When I GET "/videos?part=snippet&id=jNQXAC9IVRw"
    Then the response status is 200
    And the response body matches the "youtube_video_list" schema
    And the response field "items" has 1 item
    And the response field "items[0].snippet.title" is "Me at the zoo"
    And the response time is below 5000 ms

  @regression
  Scenario: An unknown video id gives an empty list, not an error
    When I GET "/videos?part=id&id=E2Enovideo0"
    Then the response status is 200
    And the response body matches the "youtube_video_list" schema
    And the response field "items" has 0 items

  @regression @negative
  Scenario: A wrong API key is refused
    When I GET "/videos?part=id&id=jNQXAC9IVRw" with a wrong API key
    Then the response status is 400
    And the response mentions "API_KEY_INVALID"

  @regression @negative
  Scenario: A call with no key at all is refused
    When I GET "/videos?part=id&id=jNQXAC9IVRw" without credentials
    Then the response status is 403
