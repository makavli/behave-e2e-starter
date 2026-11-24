@workato
Feature: Workato order-sync recipe
  A webhook event starts the recipe; the Workato jobs API says how its job ended.
  The job shows the event's correlation_id (in its title or a job-report column), so each scenario finds its own job.

  @smoke
  Scenario: A valid order event is processed
    Given the Workato event
      """
      {"correlation_id": "{{cid}}", "external_ref": "{{cid}}", "currency": "EUR",
       "items": [{"material": "MAT-100", "quantity": 3, "unit": "PCE"}]}
      """
    When I send the event to the recipe webhook
    Then the response status is 200
    And the Workato job for this event succeeds

  @regression @negative
  Scenario: An event without items fails its job with a reason
    Given the Workato event
      """
      {"correlation_id": "{{cid}}", "external_ref": "{{cid}}", "currency": "EUR", "items": []}
      """
    When I send the event to the recipe webhook
    Then the response status is 200
    And the Workato job for this event fails with "items"

  @regression @negative
  Scenario: An event with a bad currency fails its job with a reason
    Given the Workato event
      """
      {"correlation_id": "{{cid}}", "external_ref": "{{cid}}", "currency": "euro",
       "items": [{"material": "MAT-100", "quantity": 1, "unit": "PCE"}]}
      """
    When I send the event to the recipe webhook
    Then the Workato job for this event fails with "currency"

  @regression @negative @requires.workato.webhook_secret
  Scenario: An event with a forged signature is refused
    Given the Workato event
      """
      {"correlation_id": "{{cid}}", "external_ref": "{{cid}}", "currency": "EUR",
       "items": [{"material": "MAT-100", "quantity": 1, "unit": "PCE"}]}
      """
    When I send the event with a forged signature
    Then the response status is 401
