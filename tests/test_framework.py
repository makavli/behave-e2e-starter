"""Self-tests for the framework code the scenarios lean on; ci/run.py runs them before behave."""
import email
import importlib.util
import json
import os
import tempfile
import textwrap
import tomllib
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from e2e import api, config, data, idoc, inventory, poll
from e2e.config import ConfigError
from e2e.mft import LocalMft


class RelatedTest(unittest.TestCase):
    def test_metadata_then_the_file_as_a_mail_parser_reads_it(self):
        body, content_type = api.related({"snippet": {"title": "t"}}, b"\x00\x01video\xff", "video/mp4")
        message = email.message_from_bytes(f"Content-Type: {content_type}\r\n\r\n".encode() + body)
        meta, media = message.get_payload()
        self.assertEqual((message.get_content_type(), meta.get_content_type()), ("multipart/related", "application/json"))
        self.assertEqual(json.loads(meta.get_payload()), {"snippet": {"title": "t"}})
        self.assertEqual((media.get_content_type(), media.get_payload(decode=True)), ("video/mp4", b"\x00\x01video\xff"))


class ConfigTest(unittest.TestCase):
    def test_variables_defaults_and_missing(self):
        with mock.patch.dict(os.environ, {"E2E_T_SET": "x"}):
            os.environ.pop("E2E_T_UNSET", None)
            self.assertEqual(config.resolve({"a": ["${E2E_T_SET}/y", "${E2E_T_UNSET:-d}", 3]}), {"a": ["x/y", "d", 3]})
            with self.assertRaisesRegex(ConfigError, "E2E_T_UNSET"):
                config.resolve("${E2E_T_UNSET}")

    def test_has_and_lazy_values(self):
        os.environ.pop("E2E_T_UNSET", None)
        cfg = config.Config({"mft": {"archive": "/a", "error": "", "spare": "${E2E_T_UNSET:-}",
                                     "password": "${E2E_T_UNSET}"}}, "t")
        self.assertTrue(cfg.has("mft.archive"))
        self.assertFalse(cfg.has("mft.error"))
        self.assertFalse(cfg.has("mft.spare"))  # an empty default means "not set"
        self.assertFalse(cfg.has("idoc.status_url"))
        mft = cfg.section("mft")
        self.assertEqual(mft["archive"], "/a")  # the unused password's missing variable does not matter...
        with self.assertRaises(ConfigError):
            mft["password"]  # ...until it is read
        with self.assertRaises(ConfigError):
            cfg.has("mft.password")  # a requires-tag on a broken value fails loudly instead of skipping
        self.assertEqual(dict(cfg.section("idoc", required=False)), {})


class PollTest(unittest.TestCase):
    def test_returns_first_truthy_result(self):
        answers = [None, AssertionError("not yet"), OSError("refused"), "done"]

        def probe():
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        self.assertEqual(poll.eventually(probe, timeout_s=5, poll_s=0, what="x"), "done")

    def test_timeout_reports_the_last_check(self):
        def probe():
            raise OSError("connection refused")

        with self.assertRaisesRegex(AssertionError, "connection refused"):
            poll.eventually(probe, timeout_s=0, poll_s=0, what="x")

    def test_fail_stops_at_once(self):
        calls = []

        def probe():
            calls.append(1)
            raise poll.Fail("final state")

        with self.assertRaises(poll.Fail):
            poll.eventually(probe, timeout_s=5, poll_s=0, what="x")
        self.assertEqual(len(calls), 1)


class DataTest(unittest.TestCase):
    def test_get_set_remove(self):
        doc = {"items": [{"quantity": 2}]}
        self.assertEqual(data.get_path(doc, "items[0].quantity"), 2)
        data.set_path(doc, "items.0.quantity", 5)
        self.assertEqual(doc["items"][0]["quantity"], 5)
        data.del_path(doc, "items[0].quantity")
        self.assertEqual(doc, {"items": [{}]})
        with self.assertRaises(AssertionError):
            data.get_path(doc, "items[3]")


class IdocTest(unittest.TestCase):
    def setUp(self):
        self.xml = idoc.build_orders05(
            docnum="0000000000123456", order_ref="REF1", sender="S", receiver="R",
            items=[{"material": "M1", "quantity": "2", "unit": "PCE"}, {"material": "M2", "quantity": "1.5", "unit": "KGM"}])

    def test_valid_idoc_maps_to_an_order(self):
        root = idoc.parse(self.xml)
        self.assertEqual(idoc.validate(root), [])
        self.assertEqual(idoc.to_order(root), {"external_ref": "REF1", "currency": "EUR", "items": [
            {"material": "M1", "quantity": 2, "unit": "PCE"}, {"material": "M2", "quantity": 1.5, "unit": "KGM"}]})

    def test_errors_are_named(self):
        root = idoc.parse(self.xml)
        root.find("IDOC/EDI_DC40/DOCNUM").text = "12AB"
        root.find("IDOC/E1EDP01/MENGE").text = "0"
        body = root.find("IDOC")
        body.remove(body.find("E1EDK01"))
        errors = " | ".join(idoc.validate(root))
        for expected in ("DOCNUM", "MENGE", "E1EDK01"):
            self.assertIn(expected, errors)

    def test_malformed_xml(self):
        with self.assertRaises(ValueError):
            idoc.parse(self.xml[:40])


class LocalMftTest(unittest.TestCase):
    def test_atomic_put_list_get_delete(self):
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, "inbound"))
            mft = LocalMft({"inbound": os.path.join(d, "inbound")})
            mft.put("inbound", "a.csv", b"x")
            self.assertEqual(mft.list("inbound"), ["a.csv"])  # no .part left behind
            self.assertEqual(mft.get("inbound", "a.csv"), b"x")
            mft.delete("inbound", "a.csv")
            mft.delete("inbound", "a.csv")  # deleting twice is fine
            self.assertEqual(mft.list("inbound"), [])
            with self.assertRaises(ConfigError):
                mft.list("outbound")


class InventoryTest(unittest.TestCase):
    def test_unclassified_scenarios_are_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "features").mkdir()
            Path(d, "features", "x.feature").write_text(textwrap.dedent("""\
                @api
                Feature: X
                  @smoke
                  Scenario: classified
                    Given a step

                  Scenario: unclassified
                    Given a step
                """), encoding="utf-8")
            rows, problems = inventory.scan(Path(d, "features"))
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(problems), 1)
        self.assertIn("unclassified", problems[0])


class PublishAtTest(unittest.TestCase):
    """meta.toml's publish_at: unquoted, with its offset, at least an hour ahead; sent to YouTube in UTC."""

    def test_publish_at_rules(self):
        spec = importlib.util.spec_from_file_location("youtube_steps", config.ROOT / "features/steps/youtube_steps.py")
        steps = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(steps)
        now = datetime(2026, 11, 30, 12, 0, tzinfo=timezone.utc)
        self.assertIsNone(steps._publish_at({}, now))
        self.assertEqual(steps._publish_at(tomllib.loads("publish_at = 2026-12-01T18:00:00+01:00"), now),
                         "2026-12-01T17:00:00Z")
        for bad in ('"2026-12-01T18:00:00+01:00"', "2026-12-01T18:00:00", "2026-11-30T12:30:00Z"):  # quoted, no offset, soon
            with self.subTest(bad), self.assertRaises(AssertionError):
                steps._publish_at(tomllib.loads(f"publish_at = {bad}"), now)
        with self.assertRaises(AssertionError):  # appended below [captions]: inside that table, not ignored
            steps._publish_at(tomllib.loads('[captions]\nen = "English"\npublish_at = 2026-12-07T18:00:00+01:00'), now)

    def test_channel_guard(self):
        """Uploads go only to [youtube_owner] channel_id: another channel, none or several are refused."""
        spec = importlib.util.spec_from_file_location("youtube_steps", config.ROOT / "features/steps/youtube_steps.py")
        steps = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(steps)
        steps._channel_guard({"items": [{"id": "UCright"}]}, "UCright")
        for body in ({"items": [{"id": "UCwrong"}]}, {"items": []}, {}, {"items": [{"id": "UCright"}, {"id": "UCx"}]}):
            with self.subTest(body), self.assertRaises(AssertionError):
                steps._channel_guard(body, "UCright")


if __name__ == "__main__":
    unittest.main()


class RunTest(unittest.TestCase):
    """ci/run.py --publish NAME: the secrets and the @publish tag in one word, and red when nothing was uploaded."""

    def test_publish_option(self):
        spec = importlib.util.spec_from_file_location("ci_run", config.ROOT / "ci/run.py")
        run = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(run)
        with mock.patch.dict(os.environ, {"E2E_TAGS": "", "E2E_SECRETS": ""}):
            args = run.options(["--publish", "my-short"])
            self.assertEqual((args.publish, args.secrets, args.tags), ("my-short", True, "@publish"))
            self.assertEqual(run.options(["--publish", "x", "--tags", "@publish and @smoke"]).tags, "@publish and @smoke")
            self.assertEqual((run.options([]).publish, run.options([]).secrets, run.options([]).tags), (None, False, ""))
        self.assertTrue((config.ROOT / "features/api/youtube_publish.feature").is_file())  # uploaded() reads its result
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(run.uploaded(Path(tmp)))  # no result at all
            for skipped, want in (("1", False), ("0", True)):
                (Path(tmp) / "TESTS-api.youtube_publish.xml").write_text(
                    f'<testsuite name="x" tests="1" skipped="{skipped}">', encoding="utf-8")
                self.assertEqual(run.uploaded(Path(tmp)), want)
