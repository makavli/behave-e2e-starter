"""SAP's sales order OData V2 service (API_SALES_ORDER_SRV) behind an Apigee-style API key check.

Modeled on the SAP Business Accelerator Hub sandbox, which sits behind Apigee: a call with no key gets Apigee's
VerifyAPIKey fault (seen live on 2026-11-24). Gateway (SAP OData V2) conventions: answers under "d", collections
in d.results, Edm.Decimal as a string, Edm.DateTime as /Date(ms)/, errors as {"error": {"code", "message"}}.
shortcut: $filter takes only "<Property> eq '<value>'", JSON only; widen when a scenario needs more.
"""
import hmac
import json
import re
from urllib.parse import parse_qs, unquote

SERVICE = "API_SALES_ORDER_SRV"
NO_KEY = {"fault": {"faultstring": "Failed to resolve API Key variable request.header.apikey",
                    "detail": {"errorcode": "steps.oauth.v2.FailedToResolveAPIKey"}}}
BAD_KEY = {"fault": {"faultstring": "Invalid ApiKey", "detail": {"errorcode": "oauth.v2.InvalidApiKey"}}}

ORDERS = [
    {"SalesOrder": str(n), "SalesOrderType": "OR", "SalesOrganization": "1710", "SoldToParty": "17100001",
     "TransactionCurrency": "USD", "TotalNetAmount": f"{52.65 * n:.2f}", "CreationDate": f"/Date({1700000000000 + n * 86400000})/",
     "items": [{"SalesOrder": str(n), "SalesOrderItem": str(10 * i), "Material": f"TG{10 + i}",
                "RequestedQuantity": str(i), "RequestedQuantityUnit": "PC", "NetAmount": f"{17.55 * i:.2f}"}
               for i in (1, 2)]}
    for n in (1, 2, 3)]

METADATA = f"""<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx Version="1.0" xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx">
 <edmx:DataServices m:DataServiceVersion="2.0" xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
  <Schema Namespace="{SERVICE}" xmlns="http://schemas.microsoft.com/ado/2008/09/edm">
   <EntityType Name="A_SalesOrderType"><Key><PropertyRef Name="SalesOrder"/></Key>
    <Property Name="SalesOrder" Type="Edm.String" Nullable="false" MaxLength="10"/></EntityType>
   <EntityType Name="A_SalesOrderItemType"><Key><PropertyRef Name="SalesOrder"/><PropertyRef Name="SalesOrderItem"/></Key>
    <Property Name="SalesOrder" Type="Edm.String" Nullable="false" MaxLength="10"/></EntityType>
   <EntityContainer Name="{SERVICE}_Entities" m:IsDefaultEntityContainer="true">
    <EntitySet Name="A_SalesOrder" EntityType="{SERVICE}.A_SalesOrderType"/>
    <EntitySet Name="A_SalesOrderItem" EntityType="{SERVICE}.A_SalesOrderItemType"/>
   </EntityContainer>
  </Schema>
 </edmx:DataServices>
</edmx:Edmx>
""".encode()

_KEY = re.compile(r"A_SalesOrder\('([^']*)'\)")
_FILTER = re.compile(r"(\w+) eq '([^']*)'")


def _error(status, code, text):
    return status, {"error": {"code": code, "message": {"lang": "en", "value": text}}}


def _entity(base, row, kind):
    data = {k: v for k, v in row.items() if k != "items"}
    if kind == "A_SalesOrderItemType":
        uri = f"{base}/A_SalesOrderItem(SalesOrder='{row['SalesOrder']}',SalesOrderItem='{row['SalesOrderItem']}')"
    else:
        uri = f"{base}/A_SalesOrder('{row['SalesOrder']}')"
    return {"__metadata": {"id": uri, "uri": uri, "type": f"{SERVICE}.{kind}"}, **data}


def _select(entity, select):
    keep = {s.strip() for s in select.split(",")}
    if not select or "*" in keep:
        return entity
    return {k: v for k, v in entity.items() if k == "__metadata" or k in keep}


def _collection(base, rows, kind, query):
    flt = query.get("$filter", [""])[0]
    if flt:
        match = _FILTER.fullmatch(flt)
        if not match:
            return _error(400, "/IWBEP/CM_MGW_RT/022", f"Invalid filter expression: '{flt}'.")
        prop, value = match.groups()
        if rows and prop not in rows[0]:
            return _error(400, "/IWBEP/CM_MGW_RT/022", f"Property '{prop}' not found in type '{kind}'.")
        rows = [r for r in rows if r.get(prop) == value]
    count = len(rows)
    top = query.get("$top", [""])[0]
    if top and not (top.isascii() and top.isdigit()):
        return _error(400, "/IWBEP/CM_MGW_RT/022", f"Invalid system query option value '$top={top}'.")
    rows = rows[: int(top)] if top else rows
    d = {"results": [_select(_entity(base, r, kind), query.get("$select", [""])[0]) for r in rows]}
    if query.get("$inlinecount", [""])[0] == "allpages":
        d["__count"] = str(count)
    return 200, {"d": d}


def answer(parts, raw_query, given_key, expected_key, base):
    """(status, content type, body) for GET <service>/<parts>?<raw_query>, after the gateway's key check."""
    if not given_key:
        return 401, "application/json", json.dumps(NO_KEY).encode()
    if not hmac.compare_digest(given_key.encode(), expected_key.encode()):
        return 401, "application/json", json.dumps(BAD_KEY).encode()
    parts = [unquote(p) for p in parts]
    if parts == ["$metadata"]:
        return 200, "application/xml", METADATA
    query = parse_qs(raw_query, keep_blank_values=True)
    status, body = _route(base, parts, query)
    return status, "application/json", json.dumps(body).encode()


def _route(base, parts, query):
    if parts == ["A_SalesOrder"]:
        return _collection(base, ORDERS, "A_SalesOrderType", query)
    if parts and (match := _KEY.fullmatch(parts[0])):
        order = next((o for o in ORDERS if o["SalesOrder"] == match.group(1)), None)
        if order is None:
            return _error(404, "/IWBEP/CM_MGW_RT/020", "Resource not found for segment 'A_SalesOrderType'.")
        if len(parts) == 1:
            return 200, {"d": _select(_entity(base, order, "A_SalesOrderType"), query.get("$select", [""])[0])}
        if parts[1:] == ["to_Item"]:
            return _collection(base, order["items"], "A_SalesOrderItemType", query)
    return _error(404, "/IWBEP/CM_MGW_RT/021", f"Resource not found for segment '{parts[-1] if parts else ''}'.")
