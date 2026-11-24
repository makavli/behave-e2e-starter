"""SAP IDoc-XML: build, parse, check and map IDocs, and post them to an IDoc endpoint.

shortcut: IDoc-XML only (SAP XML/HTTP ports, CPI, most middleware); add a fixed-width parser
(EDI_DC40 / EDI_DD40 records) when a landscape sends flat-file IDocs.
"""
import datetime as dt
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

from .config import need
from .data import get_path
from .rest import Http, expect_ok

CONTROL_REQUIRED = ("TABNAM", "DOCNUM", "IDOCTYP", "MESTYP", "SNDPRT", "SNDPRN", "RCVPRT", "RCVPRN")
# Basic types this framework checks: their message types and the segments that must be there.
TYPES = {
    "ORDERS05": {"messages": {"ORDERS", "ORDCHG", "ORDRSP"}, "mandatory": ("E1EDK01", "E1EDP01")},
}
# Inbound statuses after which waiting longer changes nothing in a test run.
FINAL_STATUSES = {"51", "53", "56", "60", "68"}


def _seg(parent, name, **fields):
    el = ET.SubElement(parent, name, SEGMENT="1")
    for tag, value in fields.items():
        ET.SubElement(el, tag).text = str(value)
    return el


def build_orders05(*, docnum, order_ref, sender, receiver, items, currency="EUR",
                   sender_port="E2E", receiver_port="SAP", now=None):
    now = now or dt.datetime.now()
    root = ET.Element("ORDERS05")
    idoc = ET.SubElement(root, "IDOC", BEGIN="1")
    _seg(idoc, "EDI_DC40", TABNAM="EDI_DC40", DOCNUM=docnum, DIRECT="2", IDOCTYP="ORDERS05", MESTYP="ORDERS",
         SNDPOR=sender_port, SNDPRT="LS", SNDPRN=sender, RCVPOR=receiver_port, RCVPRT="LS", RCVPRN=receiver,
         CREDAT=now.strftime("%Y%m%d"), CRETIM=now.strftime("%H%M%S"))
    _seg(idoc, "E1EDK01", CURCY=currency, BELNR=order_ref)
    _seg(idoc, "E1EDK02", QUALF="001", BELNR=order_ref)  # 001 = customer purchase order
    for n, item in enumerate(items, 1):
        position = _seg(idoc, "E1EDP01", POSEX=f"{n * 10:06d}", MENGE=item["quantity"], MENEE=item["unit"])
        _seg(position, "E1EDP19", QUALF="002", IDTNR=item["material"])  # 002 = vendor material number
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def parse(xml):
    try:
        return ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError(f"not well-formed XML: {exc}") from None


def control(root):
    dc = root.find("IDOC/EDI_DC40")
    return {} if dc is None else {el.tag: (el.text or "").strip() for el in dc}


def validate(root):
    """Structural errors as readable strings; an empty list means the IDoc is sound."""
    idoc = root.find("IDOC")
    if idoc is None:
        return ["IDOC: element missing under the root"]
    if idoc.find("EDI_DC40") is None:
        return ["EDI_DC40: control record missing"]
    ctl = control(root)
    errors = [f"{field}: required in the control record" for field in CONTROL_REQUIRED if not ctl.get(field)]
    docnum = ctl.get("DOCNUM", "")
    if docnum and not re.fullmatch(r"[0-9]{1,16}", docnum):
        errors.append(f"DOCNUM: must be up to 16 digits, got {docnum!r}")
    idoctyp = ctl.get("IDOCTYP", "")
    spec = TYPES.get(idoctyp)
    if idoctyp and spec is None:
        errors.append(f"IDOCTYP: {idoctyp} is not a supported basic type ({', '.join(TYPES)})")
    if idoctyp and root.tag not in (idoctyp, ctl.get("CIMTYP")):
        errors.append(f"IDOCTYP: {idoctyp} does not match the root element {root.tag}")
    if spec:
        mestyp = ctl.get("MESTYP")
        if mestyp and mestyp not in spec["messages"]:
            errors.append(f"MESTYP: {mestyp} is not a message type of {idoctyp}")
        errors += [f"{seg}: mandatory segment missing" for seg in spec["mandatory"] if idoc.find(seg) is None]
        if idoctyp == "ORDERS05":
            errors += _orders05_items(idoc)
    return errors


def _orders05_items(idoc):
    errors = []
    for position in idoc.findall("E1EDP01"):
        posex = position.findtext("POSEX", "?")
        try:
            quantity_ok = float(position.findtext("MENGE", "")) > 0
        except ValueError:
            quantity_ok = False
        if not quantity_ok:
            errors.append(f"E1EDP01 {posex}: MENGE must be a quantity above 0")
        if not (position.findtext("E1EDP19/IDTNR") or "").strip():
            errors.append(f"E1EDP01 {posex}: E1EDP19 IDTNR (material) missing")
    return errors


def to_order(root):
    """Map a valid ORDERS05 IDoc to the canonical order the API speaks."""
    idoc = root.find("IDOC")
    items = []
    for position in idoc.findall("E1EDP01"):
        quantity = float(position.findtext("MENGE"))
        items.append({"material": position.findtext("E1EDP19/IDTNR").strip(),
                      "quantity": int(quantity) if quantity.is_integer() else quantity,
                      "unit": position.findtext("MENEE", "").strip()})
    return {"external_ref": idoc.findtext("E1EDK01/BELNR", "").strip(),
            "currency": idoc.findtext("E1EDK01/CURCY", "").strip(), "items": items}


class IdocClient:
    """Posts IDoc-XML (SAP ICF /sap/bc/idoc_xml, a CPI iFlow, a middleware listener) and reads IDoc statuses."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.http = Http(cfg)

    def send(self, xml):
        return self.http.post(need(self.cfg, "idoc", "endpoint"), data=xml,
                              headers={"Content-Type": "application/xml; charset=utf-8"})

    def status(self, docnum, ref):
        """Status from status_url, which may name the IDoc by {docnum} or by the order reference {ref}."""
        url = need(self.cfg, "idoc", "status_url").replace("{docnum}", quote(docnum)).replace("{ref}", quote(ref))
        body = expect_ok(self.http.get(url), f"IDoc {docnum} status").json()
        return str(get_path(body, self.cfg.get("status_field", "status")))

    def close(self):
        self.http.close()
