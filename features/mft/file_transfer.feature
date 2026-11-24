@mft
Feature: Managed file transfer route
  Files dropped in the inbound folder are checked, then delivered to the outbound folder.
  Uploads are atomic (written as <name>.part, then renamed), so the route never picks up half a file.
  File names carry {{cid}}: runs never collide, and each scenario removes its own files afterwards.

  @smoke
  Scenario: A CSV file is delivered unchanged
    Given a file "orders_{{cid}}.csv" with the content
      """
      order_ref;material;quantity
      {{cid}};MAT-100;2
      """
    When I upload the file to the MFT inbound folder
    Then the file arrives in the outbound folder unchanged

  @regression @requires.mft.archive
  Scenario: The original file is archived after delivery
    Given a file "orders_{{cid}}.csv" with the content
      """
      order_ref;material;quantity
      {{cid}};MAT-100;2
      """
    When I upload the file to the MFT inbound folder
    Then the file arrives in the outbound folder unchanged
    And the file arrives in the archive folder unchanged

  @regression
  Scenario: Non-ASCII text survives the transfer byte for byte
    Given a file "customers_{{cid}}.txt" with the content
      """
      Müller GmbH;Zürich;Ελληνικά;日本語;€
      """
    When I upload the file to the MFT inbound folder
    Then the file arrives in the outbound folder unchanged

  @regression
  Scenario Outline: A binary file of <size> bytes is delivered intact
    Given a binary file "blob_{{cid}}.bin" of <size> bytes
    When I upload the file to the MFT inbound folder
    Then the file arrives in the outbound folder unchanged

    Examples:
      | size    |
      | 1       |
      | 65536   |
      | 1048576 |

  @regression
  Scenario: A file with a matching checksum is delivered
    Given a file "orders_{{cid}}.csv" with the content
      """
      order_ref;material;quantity
      {{cid}};MAT-100;2
      """
    And the file has a matching checksum file
    When I upload the file to the MFT inbound folder
    Then the file arrives in the outbound folder unchanged

  @regression @negative @requires.mft.error
  Scenario: A file with a wrong checksum goes to the error folder
    Given a file "orders_{{cid}}.csv" with the content
      """
      order_ref;material;quantity
      {{cid}};MAT-100;2
      """
    And the file has a wrong checksum file
    When I upload the file to the MFT inbound folder
    Then the file is moved to the error folder
    And the file is not in the outbound folder

  @regression @negative @requires.mft.error
  Scenario: An empty file goes to the error folder
    Given a binary file "orders_{{cid}}.csv" of 0 bytes
    When I upload the file to the MFT inbound folder
    Then the file is moved to the error folder
    And the file is not in the outbound folder

  @regression @negative @requires.mft.error
  Scenario: A file type the route does not accept goes to the error folder
    Given a file "orders_{{cid}}.exe" with the content
      """
      not a business file
      """
    When I upload the file to the MFT inbound folder
    Then the file is moved to the error folder
    And the file is not in the outbound folder
