@idoc
Feature: SAP ORDERS05 IDoc
  Sales-order IDocs (IDoc-XML) are checked before they are sent, then posted to the IDoc endpoint:
  SAP's /sap/bc/idoc_xml, an SAP CPI iFlow, or the middleware's IDoc listener.
  The IDoc's order number (BELNR) is {{cid}} and its DOCNUM is a fresh 16-digit {{docnum}}.

  Background:
    Given an ORDERS05 IDoc for this order with the items
      | material | quantity | unit |
      | MAT-100  | 2        | PCE  |
      | MAT-200  | 1.5      | KGM  |

  @smoke
  Scenario: A well-formed ORDERS05 IDoc passes structural validation
    Then the IDoc passes structural validation

  @regression @negative
  Scenario Outline: A broken control record (<field>) is caught before sending
    Given the IDoc control field "<field>" is "<value>"
    Then the IDoc fails structural validation with "<field>"

    Examples:
      | field   | value    |
      | DOCNUM  | 12AB     |
      | DOCNUM  |          |
      | MESTYP  | INVOIC   |
      | IDOCTYP | DESADV01 |
      | SNDPRN  |          |
      | RCVPRN  |          |

  @regression @negative
  Scenario: An IDoc without item segments is caught before sending
    Given the IDoc has no item segments
    Then the IDoc fails structural validation with "E1EDP01"

  @smoke @requires.idoc.endpoint @requires.idoc.status_url
  Scenario: SAP accepts the IDoc and posts the order
    When I send the IDoc
    Then the response status is 200
    And the IDoc reaches status "53"

  @regression @negative @requires.idoc.endpoint @requires.idoc.status_url
  Scenario: An IDoc that cannot be posted ends in status 51
    Given the IDoc has no item segments
    When I send the IDoc
    Then the response status is 200
    And the IDoc reaches status "51"

  @regression @negative @requires.idoc.endpoint
  Scenario: A truncated IDoc is refused by the endpoint
    Given the IDoc XML is cut off halfway
    When I send the IDoc
    Then the response status is a client or server error
