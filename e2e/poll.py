"""Wait for eventual results: jobs finishing, files arriving, IDoc statuses changing."""
import time


class Fail(AssertionError):
    """Raised by a probe when waiting longer cannot help (a wrong final state, refused credentials)."""


def eventually(probe, *, timeout_s, poll_s, what):
    """Return probe()'s first truthy result.

    AssertionError and OSError (network, SFTP and HTTP errors) mean "not yet"; Fail stops at once.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            result = probe()
            if result:
                return result
            last = f"got {result!r}"
        except Fail:
            raise
        except (AssertionError, OSError) as exc:
            last = f"{type(exc).__name__}: {exc}"
        if time.monotonic() >= deadline:
            raise AssertionError(f"{what}: still not true after {timeout_s:g}s (last check: {last})")
        time.sleep(poll_s)
