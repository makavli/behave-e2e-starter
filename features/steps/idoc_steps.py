"""IDoc steps: build an ORDERS05, break it on purpose, check it, send it, follow its status."""
import time
import xml.etree.ElementTree as ET

from behave import given, then, when

from e2e import idoc
from e2e.data import get_path
from e2e.poll import Fail
from e2e.session import remember_response, render, set_file


def _root(context):
    return idoc.parse(context.idoc_xml)


def _store(context, root):
    context.idoc_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)


@given("an ORDERS05 IDoc for this order with the items")
def orders05(context):
    cfg = context.e2e.cfg.section("idoc", required=False)
    items = [{"material": row["material"], "quantity": row["quantity"], "unit": row["unit"]} for row in context.table]
    context.idoc_xml = idoc.build_orders05(
        docnum=context.vars["docnum"], order_ref=context.vars["cid"], items=items,
        sender=cfg.get("sender_partner", "E2ETEST"), receiver=cfg.get("receiver_partner", "SAPCLNT100"),
        sender_port=cfg.get("sender_port", "E2E"), receiver_port=cfg.get("receiver_port", "SAP"))


@given('the IDoc control field "{field:S}" is "{value:S}"')
def control_field(context, field, value):
    root = _root(context)
    control = root.find("IDOC/EDI_DC40")
    element = control.find(field)
    if element is None:
        element = ET.SubElement(control, field)
    element.text = render(context, value)
    _store(context, root)


@given("the IDoc has no item segments")
def no_items(context):
    root = _root(context)
    body = root.find("IDOC")
    for position in body.findall("E1EDP01"):
        body.remove(position)
    _store(context, root)


@given("the IDoc XML is cut off halfway")
def cut_off(context):
    context.idoc_xml = context.idoc_xml[: len(context.idoc_xml) // 2]


@given('the IDoc is saved as the file "{name:S}"')
def as_file(context, name):
    set_file(context, name, context.idoc_xml)


@then("the IDoc passes structural validation")
def passes_validation(context):
    errors = idoc.validate(_root(context))
    assert not errors, "IDoc errors: " + "; ".join(errors)


@then('the IDoc fails structural validation with "{text:S}"')
def fails_validation(context, text):
    errors = idoc.validate(_root(context))
    assert any(text in e for e in errors), f"expected an error about '{text}', got: {errors or 'none'}"


@when("I send the IDoc")
def send(context):
    started = time.perf_counter()
    remember_response(context, context.e2e.idoc.send(context.idoc_xml), started)
    # SAP numbers an inbound IDoc itself; docnum_field reads that number from the endpoint's answer.
    field = context.e2e.cfg.section("idoc").get("docnum_field")
    if field and context.response.ok and context.body is not None:
        context.vars["assigned_docnum"] = str(get_path(context.body, field))


@then('the IDoc reaches status "{status:S}"')
def reaches_status(context, status):
    docnum = context.vars.get("assigned_docnum") or idoc.control(_root(context))["DOCNUM"]
    client = context.e2e.idoc

    def probe():
        current = client.status(docnum, context.vars["cid"])
        if current == status:
            return True
        if current in idoc.FINAL_STATUSES:
            raise Fail(f"IDoc {docnum} ended in status {current}, expected {status}")
        raise AssertionError(f"IDoc {docnum} is in status {current}")

    context.e2e.eventually(probe, f"IDoc {docnum} status {status}")


@then("the received file is a valid ORDERS05 IDoc for this order")
def received_file_is_valid(context):
    name, _ = context.file
    root = idoc.parse(context.e2e.mft.get("outbound", name))
    errors = idoc.validate(root)
    assert not errors, f"{name}: " + "; ".join(errors)
    ref = idoc.to_order(root)["external_ref"]
    assert ref == context.vars["cid"], f"{name} carries order {ref}, expected {context.vars['cid']}"
