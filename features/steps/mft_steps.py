"""MFT steps: make a file, upload it to inbound, follow it to outbound, archive or error."""
import hashlib
import os

from behave import given, then, when

from e2e.session import render, set_file


def _sha(data):
    return hashlib.sha256(data).hexdigest()


@given('a file "{name:S}" with the content')
def text_file(context, name):
    set_file(context, name, render(context, context.text).encode("utf-8"))


@given('a binary file "{name:S}" of {size:d} bytes')
def binary_file(context, name, size):
    set_file(context, name, os.urandom(size))


@given("the file has a matching checksum file")
def matching_checksum(context):
    name, data = context.file
    context.sidecar = f"{_sha(data)}  {name}\n".encode()


@given("the file has a wrong checksum file")
def wrong_checksum(context):
    name, _ = context.file
    context.sidecar = f"{'0' * 64}  {name}\n".encode()


@when("I upload the file to the MFT inbound folder")
def upload(context):
    mft, (name, data), cid = context.e2e.mft, context.file, context.vars["cid"]
    if mft.cleanup:
        context.cleanups.append(lambda: [mft.delete(area, n) for area in mft.dirs for n in mft.list(area) if cid in n])
    if context.sidecar is not None:  # the checksum goes first, so the route never sees the file without it
        mft.put("inbound", name + ".sha256", context.sidecar)
    mft.put("inbound", name, data)


@then("the file arrives in the {area:w} folder unchanged")
def arrives_unchanged(context, area):
    mft, (name, data) = context.e2e.mft, context.file

    def probe():
        if name not in mft.list(area):
            return None
        got = mft.get(area, name)
        assert got == data, (f"{area}/{name}: {len(got)} bytes, sha256 {_sha(got)[:12]}; "
                             f"sent {len(data)} bytes, sha256 {_sha(data)[:12]}")
        return True

    context.e2e.eventually(probe, f"{name} in {area}")


@then("the file is moved to the error folder")
def moved_to_error(context):
    mft, (name, _) = context.e2e.mft, context.file
    context.e2e.eventually(lambda: name in mft.list("error"), f"{name} in error")


@then("the file is not in the outbound folder")
def not_in_outbound(context):
    mft, (name, _) = context.e2e.mft, context.file
    assert name not in mft.list("outbound"), f"{name} was delivered to outbound"
