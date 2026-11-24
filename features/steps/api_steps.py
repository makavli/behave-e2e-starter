"""HTTP steps: payloads, calls, and checks on the response (also used after IDoc and Workato calls)."""
import json
import time
from pathlib import Path

import jsonschema
from behave import given, step, then, when

from e2e.data import del_path, get_path, set_path
from e2e.session import remember_response, render

SCHEMAS = Path(__file__).resolve().parents[2] / "testdata" / "schemas"
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def json_value(context, text):
    return json.loads(render(context, text))


def _call(context, method, path, body=None, headers=None, **auth):
    if method not in METHODS:
        raise ValueError(f"unsupported HTTP method {method}")
    client = context.e2e.client(getattr(context, "api", "api"))
    started = time.perf_counter()
    response = client.call(method, render(context, path), json=body, headers=headers, **auth)
    remember_response(context, response, started)


@given('the "{name:w}" API')
def use_api(context, name):
    context.api = name  # this scenario's calls now go to the [name] section's base_url, with its auth


@when('I {method:w} "{path:S}" without credentials')
def call_without_body_anonymous(context, method, path):
    _call(context, method, path, anonymous=True)


@when('I {method:w} "{path:S}" with a wrong API key')
def call_with_wrong_key(context, method, path):
    _call(context, method, path, api_key="E2E-not-a-key-" + context.vars["cid"])


@given("the JSON payload")
def payload(context):
    context.payload = json_value(context, context.text)


@given('the payload field "{path:S}" is set to {value}')
def payload_set(context, path, value):
    set_path(context.payload, path, json_value(context, value))


@given('the payload field "{path:S}" is removed')
def payload_remove(context, path):
    del_path(context.payload, path)


@when('I {method:w} "{path:S}"')
def call_without_body(context, method, path):
    _call(context, method, path)


@when('I {method:w} the payload to "{path:S}"')
def call_with_payload(context, method, path):
    _call(context, method, path, body=context.payload)


@when('I {method:w} the payload to "{path:S}" without credentials')
def call_anonymous(context, method, path):
    _call(context, method, path, body=context.payload, anonymous=True)


@when('I {method:w} the payload to "{path:S}" with idempotency key "{key:S}"')
def call_idempotent(context, method, path, key):
    header = context.e2e.cfg.section("api")["idempotency_header"]
    _call(context, method, path, body=context.payload, headers={header: render(context, key)})


@then("the response status is {code:d}")
def status_is(context, code):
    r = context.response
    assert r.status_code == code, f"expected HTTP {code}, got {r.status_code}: {r.text[:500]}"


@then("the response status is a client or server error")
def status_is_error(context):
    r = context.response
    assert 400 <= r.status_code < 600, f"expected HTTP 4xx/5xx, got {r.status_code}: {r.text[:500]}"


@then('the response body matches the "{name:S}" schema')
def body_matches_schema(context, name):
    schema = json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))
    try:
        jsonschema.validate(context.body, schema)
    except jsonschema.ValidationError as exc:
        where = "/".join(map(str, exc.absolute_path))
        raise AssertionError(f"'{name}' schema: {exc.message} at /{where}") from None


# Before "is {value}": behave tries steps in the order they are defined.
@then('the response field "{path:S}" is there')
def field_there(context, path):
    get_path(context.body, path)  # raises, naming the path, when it is missing


@then('the response field "{path:S}" is absent')
def field_absent(context, path):
    try:
        get_path(context.body, path)
    except AssertionError:
        return
    raise AssertionError(f"{path} is in the response, expected it absent")


@then('the response field "{path:S}" is at most {limit:d}')
def field_at_most(context, path, limit):
    actual = get_path(context.body, path)
    assert float(actual) <= limit, f"{path}: expected at most {limit}, got {actual!r}"  # Google sends int64 as text


@then('the response field "{path:S}" is {value}')
def field_is(context, path, value):
    expected, actual = json_value(context, value), get_path(context.body, path)
    assert actual == expected, f"{path}: expected {expected!r}, got {actual!r}"


@then('the response field "{path:S}" has {count:d} items')
@then('the response field "{path:S}" has {count:d} item')
def field_has_items(context, path, count):
    actual = get_path(context.body, path)
    assert isinstance(actual, list) and len(actual) == count, f"{path}: expected {count} items, got {actual!r}"


@then('the response mentions "{text:S}"')
def response_mentions(context, text):
    text = render(context, text)
    assert text in context.response.text, f"'{text}' not in the response: {context.response.text[:500]}"


@then("the response time is below {ms:d} ms")
def response_time(context, ms):
    assert context.elapsed_ms < ms, f"took {context.elapsed_ms:.0f} ms, budget {ms} ms"


@step('I remember the response field "{path:S}" as "{name:S}"')
def remember_field(context, path, name):
    context.vars[name] = get_path(context.body, path)

@then('the response header \"{header}\" is \"{value}\"')
def header_is(context, header, value):
    actual = context.response.headers.get(header)
    assert actual == value, f'{header}: expected {value!r}, got {actual!r}'
