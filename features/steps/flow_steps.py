"""Cross-pattern steps: wait for an order that entered through another pattern to show up in the order API."""
from urllib.parse import quote

from behave import then

from e2e.rest import expect_ok
from e2e.session import render


@then('the order "{ref:S}" appears in the order API')
def order_appears(context, ref):
    ref, api = render(context, ref), context.e2e.api
    lookup = api.cfg.get("order_lookup", "/orders?external_ref={ref}").replace("{ref}", quote(ref))

    def probe():
        body = expect_ok(api.call("GET", lookup), f"order lookup {lookup}").json()
        items = body.get("items", []) if isinstance(body, dict) else body
        assert items, f"order {ref} not there yet"
        return items[0]

    context.body = context.e2e.eventually(probe, f"order {ref} in the order API")
