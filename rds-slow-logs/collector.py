"""Read RDS PostgreSQL slow records and durably forward redacted events to Loki.

No third-party dependencies. Credentials and raw statements are never logged or
written to state. Only DescribeSlowLogRecords is called against the cloud API.
"""
from __future__ import annotations

import base64
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import signal
import sqlite3
import threading
import time
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request
import uuid

UTC = timezone.utc
MAX_BODY = 8 * 1024 * 1024
MAX_SQL_CHARS = 8192
MAX_PAGES = 100
RETENTION_SECONDS = 71 * 3600


class CollectorError(Exception):
    """Only the deliberately restricted code is safe to report."""

    def __init__(self, code: str):
        self.code = re.sub(r"[^A-Za-z0-9_.-]", "_", code)[:100]
        super().__init__(self.code)


def redact_sql(sql: str, limit: int = MAX_SQL_CHARS) -> str:
    """Lex SQL, replacing literals and comments without touching bind variables.

    PostgreSQL standard strings use doubled quotes; E-prefixed strings also
    support backslash escapes. Unterminated strings are redacted to the end, so
    a truncated cloud log cannot expose the tail of a parameter.
    """
    out: list[str] = []
    i, length = 0, len(sql)

    def quoted(start: int, quote: str, escape_backslash: bool = False) -> int:
        pos = start + 1
        while pos < length:
            if escape_backslash and sql[pos] == "\\":
                pos += 2
            elif sql[pos] == quote:
                if pos + 1 < length and sql[pos + 1] == quote:
                    pos += 2
                else:
                    return pos + 1
            else:
                pos += 1
        return length

    while i < length:
        if sql.startswith("--", i):
            end = sql.find("\n", i + 2)
            i = length if end < 0 else end + 1
            out.append(" ")
        elif sql.startswith("/*", i):
            depth, i = 1, i + 2
            while i < length and depth:
                if sql.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif sql.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            out.append(" ")
        elif sql[i] == "'":
            i = quoted(i, "'")
            out.append("?")
        elif sql[i] == '"':
            end = quoted(i, '"')
            out.append(sql[i:end])
            i = end
        elif sql[i] == "$":
            match = re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", sql[i:])
            if match:
                delimiter = match.group()
                end = sql.find(delimiter, i + len(delimiter))
                i = length if end < 0 else end + len(delimiter)
                out.append("?")
            else:
                match = re.match(r"\$[0-9]+", sql[i:])
                if match:
                    out.append(match.group())
                    i += len(match.group())
                else:
                    out.append(sql[i])
                    i += 1
        elif sql[i].isalpha() or sql[i] == "_":
            end = i + 1
            while end < length and (sql[end].isalnum() or sql[end] in "_$"):
                end += 1
            word = sql[i:end]
            if word.upper() in ("E", "N", "B", "X") and end < length and sql[end] == "'":
                i = quoted(end, "'", escape_backslash=word.upper() == "E")
                out.append("?")
            elif word.upper() == "U" and sql[end:end + 2] == "&'":
                i = quoted(end + 1, "'")
                out.append("?")
            else:
                out.append(word)
                i = end
        elif sql[i].isdigit() or (sql[i] == "." and i + 1 < length and sql[i + 1].isdigit()):
            match = re.match(r"(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|(?:[0-9][0-9_]*(?:\.[0-9_]*)?|\.[0-9_]+)(?:[eE][+-]?[0-9_]+)?)", sql[i:])
            if match:
                out.append("?")
                i += len(match.group())
            else:
                out.append("?")
                i += 1
        else:
            out.append(sql[i])
            i += 1
    redacted = re.sub(r"\s+", " ", "".join(out)).strip()
    return redacted if len(redacted) <= limit else redacted[:limit] + " … [truncated]"


def timestamp_ns(value: str) -> int:
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        dt = dt.astimezone(UTC)
        return int(dt.timestamp()) * 1_000_000_000 + dt.microsecond * 1000
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise CollectorError("InvalidRecordTimestamp") from None


@dataclass(frozen=True)
class Config:
    region: str
    instance: str
    database: str
    environment: str
    loki_url: str
    state_dir: Path
    credential_path: Path
    poll_seconds: int = 60
    port: int = 9129

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            region=os.getenv("RDS_REGION_ID", "cn-beijing"),
            instance=os.environ["RDS_INSTANCE_ID"],
            database=os.getenv("RDS_DATABASE", "huahuo"),
            environment=os.getenv("RDS_ENV", "test"),
            loki_url=os.getenv("LOKI_PUSH_URL", "http://loki:3100/loki/api/v1/push"),
            state_dir=Path(os.getenv("STATE_DIR", "/var/lib/rds-slow-logs")),
            credential_path=Path(os.getenv("RDS_CREDENTIAL_FILE", "/run/secrets/rds-api.json")),
            poll_seconds=max(30, int(os.getenv("POLL_SECONDS", "60"))),
            port=int(os.getenv("METRICS_PORT", "9129")),
        )

    def labels(self) -> dict[str, str]:
        return {"service": "rds-slow-sql", "env": self.environment,
                "rds_instance": self.instance, "database": self.database,
                "source": "aliyun-rds"}


class Metrics:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.values: dict[str, float] = {
            "last_success_timestamp_seconds": 0, "records_ingested_total": 0,
            "api_requests_total": 0, "poll_errors_total": 0,
            "pending_records": 0, "last_records_total": 0,
            "expired_records_total": 0,
        }

    def set(self, key: str, value: float) -> None:
        with self.lock:
            self.values[key] = value

    def add(self, key: str, value: float = 1) -> None:
        with self.lock:
            self.values[key] = self.values.get(key, 0) + value

    def snapshot(self) -> dict[str, float]:
        with self.lock:
            return dict(self.values)

    def text(self) -> bytes:
        values = self.snapshot()
        output = []
        for key, value in values.items():
            kind = "counter" if key.endswith("_total") and key != "last_records_total" else "gauge"
            name = "rds_slow_logs_" + key
            output.extend([f"# TYPE {name} {kind}", f"{name} {value}"])
        return ("\n".join(output) + "\n").encode()


def safe_json_response(response) -> dict:
    body = response.read(MAX_BODY + 1)
    if len(body) > MAX_BODY:
        raise CollectorError("ResponseTooLarge")
    try:
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError):
        raise CollectorError("InvalidJSONResponse") from None


class RDSClient:
    def __init__(self, config: Config, metrics: Metrics) -> None:
        self.config, self.metrics = config, metrics

    def fetch_page(self, start: datetime, end: datetime, page: int) -> dict:
        try:
            credentials = json.loads(self.config.credential_path.read_text())
            access = credentials["access_key_id"]
            secret = credentials["access_key_secret"]
        except (OSError, ValueError, KeyError, TypeError):
            raise CollectorError("InvalidCredentialFile") from None
        params = {
            "Action": "DescribeSlowLogRecords", "Version": "2014-08-15", "Format": "JSON",
            "RegionId": self.config.region, "DBInstanceId": self.config.instance,
            "DBName": self.config.database, "AccessKeyId": access,
            "StartTime": start.strftime("%Y-%m-%dT%H:%MZ"),
            "EndTime": end.strftime("%Y-%m-%dT%H:%MZ"),
            "PageSize": 100, "PageNumber": page,
            "SignatureMethod": "HMAC-SHA1", "SignatureVersion": "1.0",
            "SignatureNonce": str(uuid.uuid4()),
            "Timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if credentials.get("security_token"):
            params["SecurityToken"] = credentials["security_token"]
        enc = lambda value: urllib.parse.quote(str(value), safe="~")
        canonical = "&".join(enc(key) + "=" + enc(params[key]) for key in sorted(params))
        to_sign = "GET&%2F&" + enc(canonical)
        params["Signature"] = base64.b64encode(hmac.new(
            (secret + "&").encode(), to_sign.encode(), hashlib.sha1).digest()).decode()
        url = "https://rds." + self.config.region + ".aliyuncs.com/?" + urllib.parse.urlencode(params)
        self.metrics.add("api_requests_total")
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                result = safe_json_response(response)
        except urllib.error.HTTPError as error:
            try:
                result = safe_json_response(error)
            except CollectorError:
                raise CollectorError("RDSHTTP" + str(error.code)) from None
            raise CollectorError("RDS_" + str(result.get("Code", "HTTP" + str(error.code)))) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise CollectorError("RDSNetworkError") from None
        if "Code" in result:
            raise CollectorError("RDS_" + str(result["Code"]))
        return result


class State:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA temp_store=MEMORY")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY, timestamp_ns INTEGER NOT NULL,
                line TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS events_pending ON events(sent, timestamp_ns);
            CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)

    def get(self, name: str, default: float = 0) -> float:
        row = self.db.execute("SELECT value FROM metadata WHERE name=?", (name,)).fetchone()
        return float(row[0]) if row else default

    def set_many(self, values: dict[str, float]) -> None:
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO metadata(name,value) VALUES (?,?)",
                                [(name, str(value)) for name, value in values.items()])

    def stage(self, records: list[tuple[str, int, str]]) -> None:
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO events(event_id,timestamp_ns,line) VALUES (?,?,?)", records)

    def pending_count(self) -> int:
        return self.db.execute("SELECT count(*) FROM events WHERE sent=0").fetchone()[0]

    def pending(self, limit: int = 100) -> list[tuple[str, int, str]]:
        return self.db.execute("SELECT event_id,timestamp_ns,line FROM events WHERE sent=0 ORDER BY timestamp_ns,event_id LIMIT ?",
                               (limit,)).fetchall()

    def acknowledge(self, records: list[tuple[str, int, str]]) -> int:
        with self.db:
            count = 0
            for event_id, _, _ in records:
                count += self.db.execute("UPDATE events SET sent=1 WHERE event_id=? AND sent=0", (event_id,)).rowcount
            total = int(self.get("records_ingested_total")) + count
            self.db.execute("INSERT OR REPLACE INTO metadata(name,value) VALUES ('records_ingested_total',?)", (str(total),))
        return count

    def prune(self, now: float) -> int:
        with self.db:
            expired = self.db.execute("DELETE FROM events WHERE sent=0 AND timestamp_ns < ?",
                                      (int((now - RETENTION_SECONDS) * 1e9),)).rowcount
            self.db.execute("DELETE FROM events WHERE sent=1 AND timestamp_ns < ?", (int((now - 96 * 3600) * 1e9),))
        return expired


def event_base_key(record: dict, config: Config) -> str:
    # This fingerprint, rather than SQL or bound values, is the only raw-data
    # derivative persisted. Keep occurrences separate for identical same-second
    # executions returned by the API; do not deduplicate by SQLHash alone.
    parts = [config.instance, record.get("DBName", config.database),
             record.get("SQLHash"), record.get("SQLText"), record.get("HostAddress"),
             record.get("ExecutionStartTime"), str(record.get("QueryTimeMS"))]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def normalize_record(record: dict, config: Config, occurrence: int, now: float) -> tuple[str, int, str] | None:
    ts = timestamp_ns(record.get("ExecutionStartTime", ""))
    if ts < int((now - RETENTION_SECONDS) * 1e9):
        return None
    if ts > int((now + 300) * 1e9):
        raise CollectorError("FutureRecordTimestamp")
    try:
        elapsed = float(record["QueryTimeMS"])
        if not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise CollectorError("InvalidRecordDuration") from None
    raw_sql = str(record.get("SQLText") or "[SQL unavailable]")
    redacted = redact_sql(raw_sql)
    query_id = str(record.get("SQLHash") or hashlib.sha256(redacted.encode()).hexdigest()[:24])
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", query_id):
        query_id = hashlib.sha256(query_id.encode()).hexdigest()[:24]
    base = event_base_key(record, config)
    event_id = hashlib.sha256((base + ":" + str(occurrence)).encode()).hexdigest()
    line = {
        "event_id": event_id, "query_id": query_id, "sql_summary": redacted[:256],
        "sql": redacted, "started_at": datetime.fromtimestamp(ts / 1e9, UTC).isoformat(),
        "started_at_ms": ts // 1_000_000, "elapsed_ms": elapsed,
        "database": str(record.get("DBName") or config.database), "rds_instance": config.instance,
        "probe": bool(re.search(r"/\*\s*monitoring_slow_log_probe\b", raw_sql, re.IGNORECASE)),
    }
    try:
        line["execution_count"] = max(1, int(record.get("QueryTimes") or 1))
    except (ValueError, TypeError):
        raise CollectorError("InvalidRecordExecutionCount") from None
    for source, target in [("ReturnRowCounts", "returned_rows"), ("ParseRowCounts", "scanned_rows")]:
        try:
            if record.get(source) is not None:
                line[target] = max(0, int(record[source]))
        except (ValueError, TypeError):
            pass
    return event_id, ts, json.dumps(line, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def push_loki(config: Config, records: list[tuple[str, int, str]]) -> None:
    # A late RDS record can be many hours older than the newest execution. Loki
    # normally accepts out-of-order entries only within half max_chunk_age.
    # Bound each stream to one UTC event hour instead of changing Loki's global
    # ordering limit. At 72h retention this adds at most 72 values per database;
    # SQL IDs and statements still never become indexed stream labels.
    groups: dict[str, list[list[str]]] = {}
    for _, ts, line in sorted(records, key=lambda event: (event[1], event[0])):
        hour = datetime.fromtimestamp(ts / 1e9, UTC).strftime("%Y-%m-%dT%H")
        groups.setdefault(hour, []).append([str(ts), line])
    payload = {"streams": [{"stream": {**config.labels(), "log_hour": hour}, "values": values}
                            for hour, values in sorted(groups.items())]}
    request = urllib.request.Request(config.loki_url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status != 204:
                raise CollectorError("LokiUnexpectedStatus" + str(response.status))
    except urllib.error.HTTPError as error:
        # Loki may echo the rejected line in an error body, so never log it.
        raise CollectorError("LokiHTTP" + str(error.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise CollectorError("LokiNetworkError") from None


class Collector:
    def __init__(self, config: Config, state: State, metrics: Metrics,
                 client=None, push: Callable = push_loki) -> None:
        self.config, self.state, self.metrics = config, state, metrics
        self.client = client if client is not None else RDSClient(config, metrics)
        self.push = push
        self.metrics.set("last_success_timestamp_seconds", state.get("last_success_timestamp_seconds"))
        self.metrics.set("records_ingested_total", state.get("records_ingested_total"))
        self.metrics.set("pending_records", state.pending_count())

    def flush(self) -> None:
        self.metrics.add("expired_records_total", self.state.prune(time.time()))
        while records := self.state.pending():
            self.push(self.config, records)
            self.metrics.add("records_ingested_total", self.state.acknowledge(records))
            self.metrics.set("pending_records", self.state.pending_count())

    def poll_once(self, now: datetime | None = None) -> dict[str, int]:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        end = now.replace(second=0, microsecond=0)
        full_scan = now.timestamp() - self.state.get("last_full_scan_timestamp_seconds") >= 3600
        start = end - timedelta(hours=24 if full_scan else 2)
        last_success = self.state.get("last_success_timestamp_seconds")
        if last_success and now.timestamp() - last_success > 24 * 3600:
            # Recover a longer outage instead of silently losing everything
            # older than the normal daily rescan. Loki retains only 72h.
            previous = datetime.fromtimestamp(last_success, UTC).replace(second=0, microsecond=0)
            start = max(end - timedelta(hours=71), previous - timedelta(hours=2))
        self.flush()
        occurrences: Counter[str] = Counter()
        count, total = 0, 0
        for page in range(1, MAX_PAGES + 1):
            result = self.client.fetch_page(start, end, page)
            try:
                total = int(result["TotalRecordCount"])
                records = result["Items"]["SQLSlowRecord"]
                if not isinstance(records, list) or total < 0:
                    raise ValueError()
            except (KeyError, ValueError, TypeError):
                raise CollectorError("InvalidRDSRecordPage") from None
            staged = []
            for record in records:
                if not isinstance(record, dict):
                    raise CollectorError("InvalidRDSRecord")
                base = event_base_key(record, self.config)
                occurrences[base] += 1
                event = normalize_record(record, self.config, occurrences[base], now.timestamp())
                if event is not None:
                    staged.append(event)
                else:
                    self.metrics.add("expired_records_total")
            self.state.stage(staged)
            count += len(records)
            self.metrics.set("pending_records", self.state.pending_count())
            if count >= total:
                break
            if not records:
                raise CollectorError("IncompleteRDSRecordPage")
        else:
            raise CollectorError("RDSPaginationLimitExceeded")
        self.flush()
        checkpoint = {"last_success_timestamp_seconds": now.timestamp(),
                      "last_completed_window_end_timestamp_seconds": end.timestamp()}
        if full_scan:
            checkpoint["last_full_scan_timestamp_seconds"] = now.timestamp()
        self.state.set_many(checkpoint)
        self.metrics.set("last_success_timestamp_seconds", now.timestamp())
        self.metrics.set("last_records_total", count)
        return {"queried_records": count, "pending_records": self.state.pending_count()}


def serve_metrics(config: Config, metrics: Metrics) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/metrics":
                body = metrics.text()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
            elif self.path == "/health":
                values = metrics.snapshot()
                healthy = time.time() - values["last_success_timestamp_seconds"] < max(300, config.poll_seconds * 3)
                body = json.dumps({"healthy": healthy, "pending_records": values["pending_records"]}).encode()
                self.send_response(200 if healthy else 503)
                self.send_header("Content-Type", "application/json")
            else:
                body = b"not found\n"
                self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", config.port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> None:
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    config = Config.from_env()
    metrics = Metrics()
    state = State(config.state_dir / "state.sqlite3")
    collector = Collector(config, state, metrics)
    server = serve_metrics(config, metrics)
    while not stop.is_set():
        try:
            result = collector.poll_once()
            print(json.dumps({"event": "poll_success", **result}), flush=True)
        except Exception as error:
            metrics.add("poll_errors_total")
            code = error.code if isinstance(error, CollectorError) else type(error).__name__
            print(json.dumps({"event": "poll_error", "code": code}), flush=True)
        stop.wait(config.poll_seconds)
    server.shutdown()
    state.db.close()


if __name__ == "__main__":
    main()
