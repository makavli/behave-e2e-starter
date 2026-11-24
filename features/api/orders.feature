@api
Feature: Order API
  The order API takes orders over HTTP(S), checks them and serves them back.
  Every scenario creates its own order under a fresh correlation id, {{cid}}.

  Background:
    Given the JSON payload
      """
      {
        "external_ref": "{{cid}}",
        "currency": "EUR",
        "items": [{"material": "MAT-100", "quantity": 2, "unit": "PCE"}]
      }
      """

  @smoke
  Scenario: Create an order and read it back
    When I POST the payload to "/orders"
    Then the response status is 201
    And the response body matches the "order" schema
    And the response field "external_ref" is "{{cid}}"
    And I remember the response field "id" as "order_id"
    When I GET "/orders/{{order_id}}"
    Then the response status is 200
    And the response body matches the "order" schema
    And the response field "items[0].quantity" is 2

  @smoke
  Scenario: The API answers within its response-time budget
    When I POST the payload to "/orders"
    Then the response status is 201
    And the response time is below 2000 ms

  @regression
  Scenario: An order can be found by its external reference
    When I POST the payload to "/orders"
    Then the response status is 201
    When I GET "/orders?external_ref={{cid}}"
    Then the response status is 200
    And the response field "items" has 1 item
    And the response field "items[0].external_ref" is "{{cid}}"

  @regression @requires.api.idempotency_header
  Scenario: Replaying a create with the same idempotency key returns the same order
    When I POST the payload to "/orders" with idempotency key "{{cid}}"
    Then the response status is 201
    And I remember the response field "id" as "order_id"
    When I POST the payload to "/orders" with idempotency key "{{cid}}"
    Then the response status is 200
    And the response field "id" is "{{order_id}}"

  @regression @negative
  Scenario: A call without credentials is refused
    When I POST the payload to "/orders" without credentials
    Then the response status is 401

  @regression @negative
  Scenario Outline: An invalid <field> is rejected with a reason
    Given the payload field "<field>" is set to <value>
    When I POST the payload to "/orders"
    Then the response status is 400
    And the response mentions "<field>"

    Examples:
      | field             | value  |
      | currency          | "EURO" |
      | external_ref      | null   |
      | items             | []     |
      | items[0].quantity | 0      |
      | items[0].quantity | "two"  |
      | items[0].material | ""     |

  @regression @negative
  Scenario: An order without a currency is rejected
    Given the payload field "currency" is removed
    When I POST the payload to "/orders"
    Then the response status is 400
    And the response mentions "currency"

  @regression @negative
  Scenario: An unknown order is not found
    When I GET "/orders/does-not-exist-{{cid}}"
    Then the response status is 404

  @regression
  Scenario: Response has JSON Content-Type
    When I POST the payload to "/orders"
    Then the response status is 201
    And the response header "Content-Type" is "application/json"
