"""Workato steps: send an event to the recipe webhook, then follow the job it started."""
import json
import time

from behave import given, then, when

from e2e.session import remember_response, render
from e2e.workato import FINAL_STATUSES


@given("the Workato event")
def event(context):
    context.event = json.loads(render(context, context.text))
    if context.vars["cid"] not in json.dumps(context.event):
        raise ValueError("the event must carry {{cid}} (as correlation_id) so its job can be found")


def _send(context, forge):
    started = time.perf_counter()
    remember_response(context, context.e2e.workato.send(context.event, forge_signature=forge), started)


@when("I send the event to the recipe webhook")
def send(context):
    _send(context, forge=False)


@when("I send the event with a forged signature")
def send_forged(context):
    _send(context, forge=True)


def _finished_job(context):
    cid, client = context.vars["cid"], context.e2e.workato

    def probe():
        job = client.job_for(cid)
        assert job, f"no job mentioning {cid} yet"
        assert job.get("status") in FINAL_STATUSES, f"job {job.get('id')} is {job.get('status')}"
        return job

    return context.e2e.eventually(probe, f"Workato job for {cid}")


@then("the Workato job for this event succeeds")
def job_succeeds(context):
    job = _finished_job(context)
    assert job["status"] == "succeeded", f"job {job.get('id')} {job['status']}: {job.get('error')}"


@then('the Workato job for this event fails with "{text:S}"')
def job_fails(context, text):
    job = _finished_job(context)
    assert job["status"] == "failed", f"job {job.get('id')} {job['status']}, expected failed"
    assert text in str(job.get("error")), f"job error {job.get('error')!r} does not mention '{text}'"
