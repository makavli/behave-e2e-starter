@api @sap @requires.sap.api_key
Feature: SAP S/4HANA sales order API (API_SALES_ORDER_SRV, OData V2) behind an API gateway
  The [sap] section names the service: the local simulator, or a landscape's own S/4HANA behind Apigee. The gateway checks the API key header.
  No sales order number is hard-coded: each scenario reads one from the service first.

  Background:
    Given the "sap" API

  @smoke
  Scenario: The service metadata names the sales order entity types
    When I GET "/$metadata"
    Then the response status is 200
    And the response mentions "A_SalesOrderType"
    And the response mentions "A_SalesOrderItemType"

  @smoke
  Scenario: Sales orders read as an OData V2 collection with an inline count
    When I GET "/A_SalesOrder?$top=2&$inlinecount=allpages&$format=json"
    Then the response status is 200
    And the response body matches the "odata_v2_sales_order" schema
    And the response field "d.results" has 2 items
    And the response time is below 5000 ms

  @regression
  Scenario: A sales order reads back by its key
    When I GET "/A_SalesOrder?$top=1&$format=json"
    And I remember the response field "d.results[0].SalesOrder" as "so"
    And I GET "/A_SalesOrder('{{so}}')?$format=json"
    Then the response status is 200
    And the response body matches the "odata_v2_sales_order" schema
    And the response field "d.SalesOrder" is "{{so}}"

  @regression
  Scenario: A sales order's items read through its navigation property
    When I GET "/A_SalesOrder?$top=1&$format=json"
    And I remember the response field "d.results[0].SalesOrder" as "so"
    And I GET "/A_SalesOrder('{{so}}')/to_Item?$format=json"
    Then the response status is 200
    And the response field "d.results[0].SalesOrder" is "{{so}}"

  @regression
  Scenario: $filter finds a sales order by its number
    When I GET "/A_SalesOrder?$top=1&$format=json"
    And I remember the response field "d.results[0].SalesOrder" as "so"
    And I GET "/A_SalesOrder?$filter=SalesOrder%20eq%20'{{so}}'&$format=json"
    Then the response status is 200
    And the response field "d.results" has 1 item
    And the response field "d.results[0].SalesOrder" is "{{so}}"

  @regression
  Scenario: $select returns only the chosen properties
    When I GET "/A_SalesOrder?$top=1&$select=SalesOrder,SalesOrderType&$format=json"
    Then the response status is 200
    And the response body matches the "odata_v2_sales_order" schema
    And the response field "d.results[0].SalesOrderType" is there
    And the response field "d.results[0].TotalNetAmount" is absent

  @regression @negative
  Scenario: An unknown sales order is not found, with an OData error body
    When I GET "/A_SalesOrder('9999999999')?$format=json"
    Then the response status is 404
    And the response body matches the "odata_v2_error" schema

  @regression @negative
  Scenario: An unknown property in $filter is a client error, with an OData error body
    When I GET "/A_SalesOrder?$filter=E2ENoSuchProperty%20eq%20'x'&$format=json"
    Then the response status is 400
    And the response body matches the "odata_v2_error" schema

  @regression @negative
  Scenario: A wrong API key is refused by the gateway
    When I GET "/A_SalesOrder?$top=1&$format=json" with a wrong API key
    Then the response status is 401
    And the response body matches the "apigee_fault" schema

  @regression @negative
  Scenario: A call with no API key is refused by the gateway's key check
    When I GET "/A_SalesOrder?$top=1&$format=json" without credentials
    Then the response status is 401
    And the response body matches the "apigee_fault" schema
    And the response field "fault.detail.errorcode" is "steps.oauth.v2.FailedToResolveAPIKey"
