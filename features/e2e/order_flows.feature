@e2e
Feature: Cross-pattern order flows
  Each scenario follows one order from where it enters the landscape to where it lands.
  A landscape opts into a flow in the [flows] section of its config; each flow also needs the patterns it crosses.

  @smoke @requires.flows.idoc_to_api @requires.idoc.endpoint @requires.api.base_url
  Scenario: An SAP order IDoc becomes an order in the order API
    Given an ORDERS05 IDoc for this order with the items
      | material | quantity | unit |
      | MAT-300  | 4        | PCE  |
    When I send the IDoc
    Then the response status is 200
    And the order "{{cid}}" appears in the order API
    And the response body matches the "order" schema
    And the response field "items[0].material" is "MAT-300"
    And the response field "items[0].quantity" is 4

  @smoke @requires.flows.workato_to_api @requires.workato.webhook_url @requires.api.base_url
  Scenario: A webhook event becomes an order through the Workato recipe
    Given the Workato event
      """
      {"correlation_id": "{{cid}}", "external_ref": "{{cid}}", "currency": "USD",
       "items": [{"material": "MAT-400", "quantity": 1, "unit": "PCE"}]}
      """
    When I send the event to the recipe webhook
    Then the Workato job for this event succeeds
    And the order "{{cid}}" appears in the order API
    And the response body matches the "order" schema
    And the response field "currency" is "USD"

  @regression @requires.flows.idoc_over_mft @requires.mft.inbound @requires.mft.outbound
  Scenario: An IDoc file crosses MFT unchanged and still reads as this order
    Given an ORDERS05 IDoc for this order with the items
      | material | quantity | unit |
      | MAT-500  | 10       | PCE  |
    And the IDoc is saved as the file "ORDERS05_{{cid}}.xml"
    When I upload the file to the MFT inbound folder
    Then the file arrives in the outbound folder unchanged
    And the received file is a valid ORDERS05 IDoc for this order
