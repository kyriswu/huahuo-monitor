"""Local-only correctness checks; never contact a database, cloud API or Loki."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from collector import (Collector, CollectorError, Config, Metrics, RDSClient, State,
                       normalize_record, push_loki, redact_sql)

UTC = timezone.utc


class RedactionTests(unittest.TestCase):
    def test_literals_and_bound_parameters(self):
        sql = "SELECT column12, $1, $22, 12, .45, 1.2e-3, 0xAB FROM table_2026 WHERE name='Alice' AND phone=N'12345'"
        result = redact_sql(sql)
        self.assertEqual(result, "SELECT column12, $1, $22, ?, ?, ?, ? FROM table_2026 WHERE name=? AND phone=?")
        for secret in ["Alice", "12345", "1.2e-3"]:
            self.assertNotIn(secret, result)

    def test_quotes_prefixes_and_dollar_quotes(self):
        sql = "SELECT 'O''Brien', E'private\\\'value', B'101', X'F00D', U&'pass\\0061word', $$hidden$$, $tag$more hidden$tag$, $1"
        self.assertEqual(redact_sql(sql), "SELECT ?, ?, ?, ?, ?, ?, ?, $1")

    def test_nested_comments_and_line_comments(self):
        sql = "/* secret /* nested */ value */ SELECT 1 -- token=password\n FROM public.example"
        self.assertEqual(redact_sql(sql), "SELECT ? FROM public.example")

    def test_standard_string_ending_in_backslash_does_not_leak_next_literal(self):
        # Under standard_conforming_strings=on the backslash does not escape '
        sql = "SELECT 'path\\', 'private-secret', 1"
        result = redact_sql(sql)
        self.assertEqual(result, "SELECT ?, ?, ?")
        self.assertNotIn("private-secret", result)

    def test_quoted_identifier_backslash_does_not_swallow_later_literal(self):
        sql = 'SELECT "column\\", \'private-secret\', "column""number2"'
        result = redact_sql(sql)
        self.assertEqual(result, 'SELECT "column\\", ?, "column""number2"')
        self.assertNotIn("private-secret", result)

    def test_unterminated_values_and_comments(self):
        for sql in ["SELECT 'private-secret", "SELECT $tag$private-secret", "SELECT /* private-secret"]:
            self.assertNotIn("private-secret", redact_sql(sql))

    def test_redaction_happens_before_truncation(self):
        result = redact_sql("SELECT '" + "secret" * 1000 + "' FROM sensitive_table", limit=20)
        self.assertNotIn("secret", result)
        self.assertTrue(result.startswith("SELECT ? FROM"))
        self.assertTrue(result.endswith("[truncated]"))


class FakeClient:
    def __init__(self, records):
        self.records = records
        self.calls = []
        self.fail_on_page = None

    def fetch_page(self, start, end, page):
        self.calls.append((start, end, page))
        if page == self.fail_on_page:
            raise CollectorError("TestReadFailure")
        records = self.records[page - 1] if page <= len(self.records) else []
        return {"TotalRecordCount": sum(len(records) for records in self.records),
                "Items": {"SQLSlowRecord": records}}


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.config = Config("cn-beijing", "pgm-test", "huahuo", "test",
                             "http://unused.invalid", self.path, self.path / "unused-secret")
        self.now = datetime.now(UTC).replace(second=0, microsecond=0)
        self.record = {"SQLText": "SELECT * FROM users WHERE phone='private-secret' AND id=$1",
                       "SQLHash": "cloud-query-id", "QueryTimeMS": 1202, "QueryTimes": 1,
                       "ExecutionStartTime": (self.now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "DBName": "huahuo", "HostAddress": "user@private-host",
                       "ReturnRowCounts": 2, "ParseRowCounts": 18}
        self.state = State(self.path / "state.sqlite3")
        self.addCleanup(self.state.db.close)

    def collector(self, client, push):
        return Collector(self.config, self.state, Metrics(), client=client, push=push)

    def test_normalization_preserves_metadata_and_redacts_all_persisted_text(self):
        record = dict(self.record, QueryTimes=3,
                      SQLText="/* monitoring_slow_log_probe */ SELECT pg_sleep(1.2), 'private-secret'")
        event_id, timestamp, text = normalize_record(record, self.config, 1, self.now.timestamp())
        event = json.loads(text)
        self.assertEqual(event["elapsed_ms"], 1202)
        self.assertEqual(event["execution_count"], 3)
        self.assertTrue(event["probe"])
        self.assertEqual(event["sql"], "SELECT pg_sleep(?), ?")
        self.assertEqual(event["returned_rows"], 2)
        self.assertEqual(event["scanned_rows"], 18)
        self.assertNotIn("private-secret", text)
        self.assertNotIn("private-host", text)
        self.assertEqual(len(event_id), 64)
        self.assertGreater(timestamp, 0)

    def test_identical_executions_remain_distinct_but_overlap_is_idempotent(self):
        sent = []
        collector = self.collector(FakeClient([[self.record, self.record]]),
                                   lambda _config, batch: sent.extend(batch))
        collector.poll_once(self.now)
        self.assertEqual(len(sent), 2)
        self.assertNotEqual(sent[0][0], sent[1][0])
        collector.poll_once(self.now + timedelta(minutes=1))
        self.assertEqual(len(sent), 2)
        self.assertEqual(self.state.get("records_ingested_total"), 2)
        self.assertEqual(self.state.pending_count(), 0)
        self.assertNotIn(b"private-secret", (self.path / "state.sqlite3").read_bytes())
        wal = self.path / "state.sqlite3-wal"
        if wal.exists():
            self.assertNotIn(b"private-secret", wal.read_bytes())

    def test_failed_push_keeps_durable_outbox_and_replays_same_payload(self):
        attempted = []
        def fail(_config, batch):
            attempted.extend(batch)
            raise CollectorError("LokiTestFailure")
        collector = self.collector(FakeClient([[self.record]]), fail)
        with self.assertRaises(CollectorError):
            collector.poll_once(self.now)
        self.assertEqual(self.state.pending_count(), 1)
        self.assertEqual(self.state.get("last_success_timestamp_seconds"), 0)
        self.assertEqual(self.state.get("last_full_scan_timestamp_seconds"), 0)
        replayed = []
        collector.push = lambda _config, batch: replayed.extend(batch)
        collector.poll_once(self.now + timedelta(minutes=1))
        self.assertEqual(attempted, replayed)
        self.assertEqual(self.state.pending_count(), 0)
        self.assertEqual(self.state.get("records_ingested_total"), 1)

    def test_partial_pagination_cannot_advance_checkpoint_or_duplicate_on_retry(self):
        second = dict(self.record, SQLHash="other-hash", SQLText="SELECT 123")
        client = FakeClient([[self.record], [second]])
        client.fail_on_page = 2
        sent = []
        collector = self.collector(client, lambda _config, batch: sent.extend(batch))
        with self.assertRaises(CollectorError):
            collector.poll_once(self.now)
        self.assertEqual(self.state.get("last_success_timestamp_seconds"), 0)
        self.assertEqual(self.state.pending_count(), 1)
        self.assertEqual(len(sent), 0)
        client.fail_on_page = None
        collector.poll_once(self.now + timedelta(minutes=1))
        self.assertEqual(len(sent), 2)
        self.assertEqual(len({event[0] for event in sent}), 2)
        self.assertGreater(self.state.get("last_success_timestamp_seconds"), 0)

    def test_full_hourly_window_then_overlapping_regular_window(self):
        client = FakeClient([[]])
        collector = self.collector(client, lambda *_: None)
        collector.poll_once(self.now)
        collector.poll_once(self.now + timedelta(minutes=1))
        collector.poll_once(self.now + timedelta(minutes=61))
        self.assertEqual([end - start for start, end, _ in client.calls],
                         [timedelta(hours=24), timedelta(hours=2), timedelta(hours=24)])

    def test_long_outage_resumes_from_checkpoint_with_retention_cap(self):
        client = FakeClient([[]])
        collector = self.collector(client, lambda *_: None)
        collector.poll_once(self.now)
        collector.poll_once(self.now + timedelta(hours=36))
        start, end, _ = client.calls[-1]
        self.assertEqual(end - start, timedelta(hours=38))
        collector.poll_once(self.now + timedelta(hours=36 + 90))
        start, end, _ = client.calls[-1]
        self.assertEqual(end - start, timedelta(hours=71))

    def test_old_records_and_invalid_duration(self):
        old = dict(self.record, ExecutionStartTime=(self.now - timedelta(hours=72)).isoformat())
        self.assertIsNone(normalize_record(old, self.config, 1, self.now.timestamp()))
        for invalid in [float("nan"), float("inf"), -1, None]:
            with self.assertRaises(CollectorError):
                normalize_record(dict(self.record, QueryTimeMS=invalid), self.config, 1, self.now.timestamp())

    def test_empty_response_is_a_success_not_a_fake_slow_query(self):
        sent = []
        collector = self.collector(FakeClient([[]]), lambda _config, batch: sent.extend(batch))
        collector.poll_once(self.now)
        self.assertEqual(sent, [])
        self.assertGreater(collector.metrics.snapshot()["last_success_timestamp_seconds"], 0)
        self.assertEqual(collector.metrics.snapshot()["records_ingested_total"], 0)

    def test_rds_requests_use_the_vpc_endpoint_only_when_enabled(self):
        self.config.credential_path.write_text(json.dumps({
            "access_key_id": "test-key", "access_key_secret": "test-secret",
        }))
        for use_vpc, hostname in [(False, "rds.cn-beijing.aliyuncs.com"),
                                  (True, "rds-vpc.cn-beijing.aliyuncs.com")]:
            with self.subTest(use_vpc=use_vpc):
                config = replace(self.config, use_vpc_endpoint=use_vpc)
                client = RDSClient(config, Metrics())
                with patch("collector.urllib.request.urlopen", side_effect=CollectorError("StopRequest")) as request:
                    with self.assertRaisesRegex(CollectorError, "StopRequest"):
                        client.fetch_page(self.now - timedelta(minutes=5), self.now, 1)
                self.assertEqual(urlsplit(request.call_args.args[0]).hostname, hostname)

    def test_push_splits_late_entries_into_bounded_hour_streams(self):
        captured = []
        class Response:
            status = 204
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return None
        def open_request(request, **_kwargs):
            captured.append(json.loads(request.data))
            return Response()
        base_ns = int(self.now.replace(minute=0).timestamp()) * 1_000_000_000
        records = [("new", base_ns + 3600 * 1_000_000_000, '{"a":2}'),
                   ("old", base_ns + 60 * 1_000_000_000, '{"a":1}'),
                   ("older", base_ns, '{"a":0}')]
        with patch("collector.urllib.request.urlopen", open_request):
            push_loki(self.config, records)
        streams = captured[0]["streams"]
        self.assertEqual(len(streams), 2)
        self.assertEqual([line for _, line in streams[0]["values"]], ['{"a":0}', '{"a":1}'])
        for stream in streams:
            self.assertIn("log_hour", stream["stream"])
            self.assertEqual(stream["stream"]["service"], "rds-slow-sql")
            self.assertNotIn("query_id", stream["stream"])
            self.assertNotIn("sql", stream["stream"])


if __name__ == "__main__":
    unittest.main()
