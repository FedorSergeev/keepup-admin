"""Shipping application logs to an external collector.

Logs are shipped in batches from a background thread when the application
names a collector. Shipping must never be able to hold up a request or bring
the process down: every failure here is swallowed, and repeated failures put
the collector aside for a while rather than retrying on the hot path.

Apart from local logging (`keepup/logging_setup.py`, keepup-24): the two share
only the word "log". The collector address, its token and the project name
logs are filed under belong to the application; it supplies them through
``keepup.logging_setup.configure()``, which hands them on here, or through
this module's ``configure()``.
"""

import asyncio
import atexit
import json
import logging
import queue
import threading
import time
from datetime import datetime
from typing import Any, Dict, List

import requests

from keepup.instance import get_instance_id

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "AsyncRemoteLogHandler",
    "RemoteLogHandler",
    "RemoteLoggerWrapper",
    "configure",
    "create_logger_token",
    "init_remote_logging",
    "setup_remote_logging",
]

logger = logging.getLogger(__name__)

#: Set by the application through configure(); the framework has no project of
#: its own and no collector to ship to.
PROJECT_NAME = "keepup"
REMOTE_LOG_URL = None
#: The credential the collector expects. It identifies the application to a
#: service the framework knows nothing about, so it arrives from the
#: application; the package carries no token of its own. Without it nothing is
#: shipped -- see flush().
REMOTE_LOG_TOKEN = None
REMOTE_FLUSH_INTERVAL = 600
REMOTE_BATCH_SIZE = 100_000
#: How many records wait for the collector at most. While it is away, or
#: before the application has given a token, the oldest are dropped rather
#: than held for ever (keepup-68): a queue without a bound grew for as long as
#: the collector stayed down, in the memory of every replica.
REMOTE_MAX_QUEUED = 50_000

_UNSET = object()


def configure(project_name=_UNSET, remote_url=_UNSET, flush_interval=_UNSET,
              batch_size=_UNSET, remote_token=_UNSET, max_queued=_UNSET):
    """Supply the collector's values before the handlers are built."""
    global PROJECT_NAME, REMOTE_LOG_URL, REMOTE_LOG_TOKEN
    global REMOTE_FLUSH_INTERVAL, REMOTE_BATCH_SIZE, REMOTE_MAX_QUEUED
    if max_queued is not _UNSET and max_queued is not None:
        REMOTE_MAX_QUEUED = max_queued
    if project_name is not _UNSET and project_name is not None:
        PROJECT_NAME = project_name
    if remote_url is not _UNSET:
        REMOTE_LOG_URL = remote_url
    if remote_token is not _UNSET:
        REMOTE_LOG_TOKEN = remote_token
    if flush_interval is not _UNSET and flush_interval is not None:
        REMOTE_FLUSH_INTERVAL = flush_interval
    if batch_size is not _UNSET and batch_size is not None:
        REMOTE_BATCH_SIZE = batch_size


# What the application already gave keepup.logging_setup.configure() before this
# module was loaded: that configure() is the one entry, and this module is
# loaded only when there is a collector.
from keepup import logging_setup as _local  # noqa: E402

configure(**_local.SHIPPING_VALUES)


class RemoteLoggerWrapper:
    """Buffers log records and ships them to a remote collector."""

    def __init__(self, remote_url: str = None, flush_interval: int = 120, batch_size: int = 100_000_000):
        # No default address. A package that ships to an address of its own
        # turns "nobody configured a collector" into "shipping quietly fails
        # against a port nothing listens on", which looks the same from the
        # outside and reads as working.
        self.remote_url = remote_url or REMOTE_LOG_URL
        self.flush_interval = flush_interval
        self.batch_size = batch_size
        self.max_queued = REMOTE_MAX_QUEUED
        self.log_queue = queue.Queue(maxsize=self.max_queued)
        #: Records dropped since the last delivery, reported with the next one.
        self.dropped = 0
        self.buffer = []
        self.lock = threading.Lock()
        #: Wakes the one shipping thread early. A full batch used to start a
        #: thread of its own per record -- and without a token nothing was ever
        #: sent, so every record past the batch size started one.
        self._wake = threading.Event()
        self.running = True
        self.flush_thread = None
        self.project = self._get_project_name()
        self.version = self._get_version()
        self.instance = get_instance_id()
        self.token = self._get_token()

        self.remote_available = True
        self.consecutive_failures = 0
        self.max_consecutive_failures = 3
        self.backoff_until = 0

        if not self.remote_url:
            self.running = False
            logger.info("Remote log shipping is off: the application named no collector.")
            return

        self._start_flush_thread()
        atexit.register(self.force_flush)
        logger.info(f"RemoteLoggerWrapper initialized. Flush interval: {flush_interval}s, Batch size: {batch_size}")

    def _get_project_name(self) -> str:
        """Return the project name."""
        try:
            with open('config/modules.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
                return config.get('project_name', PROJECT_NAME)
        except:
            return PROJECT_NAME

    def _get_version(self) -> str:
        """Return the application version."""
        try:
            with open('static/version.json', 'r', encoding='utf-8') as f:
                version_data = json.load(f)
                return version_data.get('version', '1.0.0')
        except:
            return '1.0.0'

    def _get_token(self) -> str:
        """Return the collector credential the application configured.

        Returns:
            The token, or None when the application named none -- in which
            case nothing is shipped.
        """
        return REMOTE_LOG_TOKEN

    def _start_flush_thread(self):
        """Start the background shipping thread."""
        self.flush_thread = threading.Thread(target=self._flush_worker, daemon=True)
        self.flush_thread.start()

    def _flush_worker(self):
        """Background task shipping logs periodically, or early on a full batch."""
        while self.running:
            self._wake.wait(self.flush_interval)
            self._wake.clear()
            try:
                self.flush()
            except Exception as e:
                print(f"Error in flush worker: {str(e)}")

    def emit(self, record: logging.LogRecord):
        """Queue a log record; the oldest is dropped when the queue is full."""
        if not self.token:
            # Nothing would ever be sent (flush() refuses without one), so
            # nothing is kept.
            return
        try:
            log_entry = self._format_record(record)
            with self.lock:
                try:
                    self.log_queue.put_nowait(log_entry)
                except queue.Full:
                    try:
                        self.log_queue.get_nowait()
                        self.dropped += 1
                        self.log_queue.put_nowait(log_entry)
                    except (queue.Empty, queue.Full):
                        self.dropped += 1

            if self.log_queue.qsize() >= self.batch_size:
                self._wake.set()

        except Exception as e:
            print(f"Error in RemoteLoggerWrapper.emit: {str(e)}")

    def _format_record(self, record: logging.LogRecord) -> Dict[str, Any]:
        """Format a log record."""
        return {
            "date": datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
            "level": record.levelname,
            "message": record.getMessage(),
            "class": record.name
        }

    def flush(self):
        """Ship the buffered logs to the collector."""
        if not self.remote_url:
            return

        if not self.remote_available and time.time() < self.backoff_until:
            return

        if not self.token:
            return

        with self.lock:
            logs_to_send = []
            batch_count = 0
            while not self.log_queue.empty() and batch_count < self.batch_size:
                try:
                    log = self.log_queue.get_nowait()
                    logs_to_send.append(log)
                    batch_count += 1
                except queue.Empty:
                    break
                except Exception:
                    break

        if not logs_to_send:
            return

        try:
            payload = {
                "project": self.project,
                "version": self.version,
                "instance": self.instance,
                "logs": logs_to_send
            }

            response = requests.post(
                self.remote_url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json"
                },
                timeout=(2, 5)
            )

            if response.status_code == 200:
                self.consecutive_failures = 0
                self.remote_available = True
                logger.debug(f"Successfully sent {len(logs_to_send)} logs to remote server")
                with self.lock:
                    dropped, self.dropped = self.dropped, 0
                if dropped:
                    logger.warning(f"{dropped} log records were dropped while they "
                                   f"could not be shipped")
            else:
                self._handle_failure(f"HTTP {response.status_code}")
                self._return_logs_to_queue(logs_to_send)

        except requests.exceptions.Timeout:
            self._handle_failure("timeout")
            logger.warning(f"Timeout sending logs to {self.remote_url}")
            self._return_logs_to_queue(logs_to_send)

        except requests.exceptions.ConnectionError as e:
            self._handle_failure("connection error")
            logger.warning(f"Connection error sending logs: {str(e)}")
            self._return_logs_to_queue(logs_to_send)

        except requests.exceptions.RequestException as e:
            self._handle_failure("request error")
            logger.warning(f"Request error sending logs: {str(e)}")
            self._return_logs_to_queue(logs_to_send)

        except Exception as e:
            self._handle_failure("unexpected error")
            logger.warning(f"Unexpected error while sending logs: {str(e)}")
            self._return_logs_to_queue(logs_to_send)

    def _handle_failure(self, error_type: str):
        """Handle a failed delivery."""
        self.consecutive_failures += 1

        if self.consecutive_failures >= self.max_consecutive_failures:
            self.remote_available = False
            # Exponential backoff: 30s, 1m, 2m, 4m, 8m, ...
            backoff_time = min(30 * (2 ** (self.consecutive_failures - self.max_consecutive_failures)), 480)
            self.backoff_until = time.time() + backoff_time
            logger.warning(f"Remote logging disabled for {backoff_time}s due to {self.consecutive_failures} failures")

    def _return_logs_to_queue(self, logs: List[Dict]):
        """Put logs back on the queue after a failed delivery.

        Returned to the front, so ordering is preserved; what does not fit
        under the bound is the oldest, and it is dropped.
        """
        try:
            with self.lock:
                waiting = list(logs)
                while True:
                    try:
                        waiting.append(self.log_queue.get_nowait())
                    except queue.Empty:
                        break
                overflow = max(0, len(waiting) - self.max_queued)
                self.dropped += overflow
                refilled = queue.Queue(maxsize=self.max_queued)
                for log in waiting[overflow:]:
                    refilled.put_nowait(log)
                self.log_queue = refilled
        except Exception as e:
            print(f"Error returning logs to queue: {str(e)}")

    def force_flush(self):
        """Ship every buffered log, called at shutdown."""
        logger.info("Force flushing remote logs before shutdown...")
        self.running = False

        # Short timeouts: shutdown must not hang on an unreachable collector.
        flush_attempts = 0
        max_attempts = 3

        while not self.log_queue.empty() and flush_attempts < max_attempts:
            try:
                self.flush()
                flush_attempts += 1
                time.sleep(1)
            except:
                break


class AsyncRemoteLogHandler(logging.Handler):
    """Asynchronous log handler for use inside FastAPI."""

    def __init__(self, remote_url: str = None, flush_interval: int = 120, batch_size: int = 100):
        super().__init__()
        # See RemoteLoggerWrapper.__init__ for why there is no default address.
        self.remote_url = remote_url or REMOTE_LOG_URL
        self.flush_interval = flush_interval
        self.batch_size = batch_size
        self.buffer = []
        self.lock = asyncio.Lock()
        self.project = self._get_project_name()
        self.version = self._get_version()
        self.instance = get_instance_id()
        self.token = self._get_token()
        self.flush_task = None
        self.running = True

        self.remote_available = True
        self.consecutive_failures = 0
        self.max_consecutive_failures = 3
        self.backoff_until = 0
        self.max_buffer_size = 10000  # maximum buffer size
        self._sending = False

    def _get_project_name(self) -> str:
        try:
            with open('config/modules.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
                return config.get('project_name', PROJECT_NAME)
        except:
            return PROJECT_NAME

    def _get_version(self) -> str:
        try:
            with open('static/version.json', 'r', encoding='utf-8') as f:
                version_data = json.load(f)
                return version_data.get('version', '1.0.0')
        except:
            return '1.0.0'

    def _get_token(self) -> str:
        return REMOTE_LOG_TOKEN

    async def start(self):
        """Start the background task."""
        self.flush_task = asyncio.create_task(self._flush_worker())

    async def stop(self):
        """Stop the background task."""
        self.running = False
        if self.flush_task:
            self.flush_task.cancel()
            try:
                await self.flush_task
            except asyncio.CancelledError:
                pass
        await self.flush()

    async def _flush_worker(self):
        """Background task shipping logs periodically."""
        while self.running:
            try:
                await asyncio.sleep(self.flush_interval)
                await self.flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Logged, but the loop keeps running.
                print(f"Error in flush worker: {str(e)}")

    def emit(self, record):
        """Add a record to the buffer."""
        if not self.token:
            return
        try:
            log_entry = {
                "date": datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
                "level": record.levelname,
                "message": record.getMessage(),
                "class": record.name
            }

            # Scheduled without awaiting: emit() must never block the caller.
            asyncio.create_task(self._safe_add_to_buffer(log_entry))

        except Exception as e:
            # Logging must never bring the application down.
            print(f"Error in AsyncRemoteLogHandler.emit: {str(e)}")

    async def _safe_add_to_buffer(self, log_entry: Dict[str, Any]):
        """Add a record to the buffer, keeping the buffer bounded."""
        try:
            async with self.lock:
                if len(self.buffer) >= self.max_buffer_size:
                    # A full buffer drops its oldest records rather than growing without bound.
                    self.buffer = self.buffer[-(self.max_buffer_size - 1):]

                self.buffer.append(log_entry)

                # One send at a time: a full buffer used to start a send per
                # record while the previous one was still on its way.
                if len(self.buffer) >= self.batch_size and not self._sending:
                    self._sending = True
                    asyncio.create_task(self._safe_send_buffer())

        except Exception as e:
            print(f"Error adding to buffer: {str(e)}")

    async def _safe_send_buffer(self):
        """Ship the buffer, swallowing any error."""
        try:
            await self._send_buffer()
        except Exception as e:
            print(f"Error in safe send buffer: {str(e)}")
        finally:
            self._sending = False

    async def _send_buffer(self):
        """Ship the buffer to the collector."""
        if not self.remote_available and time.time() < self.backoff_until:
            return

        async with self.lock:
            if not self.token or not self.buffer:
                return

            logs_to_send = self.buffer.copy()
            self.buffer.clear()

        try:
            import httpx

            # Short timeouts: log shipping must not stall request handling.
            async with httpx.AsyncClient(
                    timeout=httpx.Timeout(3.0, connect=2.0),
                    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
            ) as client:

                payload = {
                    "project": self.project,
                    "version": self.version,
                    "instance": self.instance,
                    "logs": logs_to_send
                }

                response = await client.post(
                    self.remote_url,
                    json=payload,
                    headers={"Authorization": f"Bearer {self.token}"}
                )

                if response.status_code == 200:
                    self.consecutive_failures = 0
                    self.remote_available = True
                    logger.debug(f"Successfully sent {len(logs_to_send)} logs")
                else:
                    self._handle_failure(f"HTTP {response.status_code}")
                    await self._safe_return_to_buffer(logs_to_send)

        except httpx.TimeoutException:
            self._handle_failure("timeout")
            logger.warning(f"Timeout sending logs to {self.remote_url}")
            await self._safe_return_to_buffer(logs_to_send)

        except httpx.ConnectError as e:
            self._handle_failure("connection error")
            logger.warning(f"Connection error sending logs: {str(e)}")
            await self._safe_return_to_buffer(logs_to_send)

        except httpx.HTTPError as e:
            self._handle_failure("http error")
            logger.warning(f"HTTP error sending logs: {str(e)}")
            await self._safe_return_to_buffer(logs_to_send)

        except Exception as e:
            self._handle_failure("unexpected error")
            logger.warning(f"Unexpected error sending logs: {str(e)}")
            # On an unexpected error the logs are dropped rather than requeued, which

    def _handle_failure(self, error_type: str):
        """Handle a failed delivery."""
        self.consecutive_failures += 1

        if self.consecutive_failures >= self.max_consecutive_failures:
            self.remote_available = False
            # Exponential backoff
            backoff_time = min(30 * (2 ** (self.consecutive_failures - self.max_consecutive_failures)), 480)
            self.backoff_until = time.time() + backoff_time
            logger.warning(f"Remote logging disabled for {backoff_time}s due to failures")

    async def _safe_return_to_buffer(self, logs: List[Dict]):
        """Put logs back into the buffer, keeping it bounded."""
        try:
            async with self.lock:
                # Returned to the front so ordering is preserved.
                self.buffer = logs + self.buffer
                if len(self.buffer) > self.max_buffer_size:
                    self.buffer = self.buffer[:self.max_buffer_size]
        except Exception as e:
            print(f"Error returning logs to buffer: {str(e)}")

    async def flush(self):
        """Ship every buffered log."""
        try:
            await self._send_buffer()
        except Exception as e:
            logger.error(f"Error in flush: {str(e)}")


def setup_remote_logging(remote_url: str = None, flush_interval: int = 120, batch_size: int = 100):
    """Configure remote logging so that it can never block the application."""
    try:
        remote_handler = RemoteLogHandler(remote_url, flush_interval, batch_size)

        root_logger = logging.getLogger()
        root_logger.addHandler(remote_handler)

        logger.info(f"Remote logging configured. URL: {remote_url or 'default'}")
        return remote_handler

    except Exception as e:
        logger.error(f"Failed to setup remote logging (non-critical): {str(e)}")
        return None


def init_remote_logging(remote_url=None, flush_interval=None, batch_size=None):
    """Initialise remote logging, tolerating any failure.

    Without a collector address -- neither passed here nor given to
    ``configure()`` -- there is nothing to ship to, and the function does
    nothing rather than inventing a destination.
    """
    try:
        remote_url = remote_url or REMOTE_LOG_URL
        if not remote_url:
            return None
        flush_interval = flush_interval or REMOTE_FLUSH_INTERVAL
        batch_size = batch_size or REMOTE_BATCH_SIZE

        remote_handler = RemoteLogHandler(remote_url, flush_interval, batch_size)
        logging.getLogger().addHandler(remote_handler)

        for name in ['uvicorn', 'uvicorn.error', 'fastapi']:
            try:
                logger = logging.getLogger(name)
                if logger and not any(isinstance(h, RemoteLogHandler) for h in logger.handlers):
                    logger.addHandler(remote_handler)
            except:
                pass

        logger.info(f"Remote logging initialized. URL: {remote_url}")
        return remote_handler

    except Exception as e:
        print(f"WARNING: Remote logging initialization failed (non-critical): {str(e)}")
        return None


class RemoteLogHandler(logging.Handler):
    """Log handler shipping records to a remote collector."""

    def __init__(self, remote_url: str = None, flush_interval: int = 1200, batch_size: int = 100_000):
        super().__init__()
        self.wrapper = RemoteLoggerWrapper(remote_url, flush_interval, batch_size)
        self.setLevel(logging.INFO)

    def emit(self, record):
        self.wrapper.emit(record)

    def close(self):
        self.wrapper.force_flush()
        super().close()


#: The path the shipping address ends with, and what registration lives under
#: instead. Both are the collector's own layout, so one address is enough for
#: the two calls and the application is not asked for a second one.
_SHIPPING_PATH = "/logs"
_REGISTRATION_PATH = "/admin/applications/register"


def _registration_url():
    """Derive the registration endpoint from the configured shipping address.

    Returns:
        The registration address, or None when the application named no
        collector.
    """
    if not REMOTE_LOG_URL:
        return None
    if REMOTE_LOG_URL.endswith(_SHIPPING_PATH):
        return REMOTE_LOG_URL[: -len(_SHIPPING_PATH)] + _REGISTRATION_PATH
    return REMOTE_LOG_URL.rstrip("/") + _REGISTRATION_PATH


def create_logger_token(admin_token: str, collector_url: str = None,
                        app_name: str = None, description: str = None,
                        contact_email: str = None):
    """Register this application with the log collector and keep its token.

    The collector is a service of the deployment, and the name and description
    it files the application under belong to the application. The framework
    supplies none of the three: it has no collector, no product name and
    nothing to say about itself.

    Args:
        admin_token: the collector's administrator token.
        collector_url: address of the collector's registration endpoint. When
            omitted, the address the application gave ``configure()`` is used.
        app_name: the name to register under; defaults to the configured
            project name.
        description: what to file the application as.
        contact_email: contact address for the registration.

    Returns:
        The issued API token, or None when no address is known or the
        collector refused.
    """
    import requests

    collector_url = collector_url or _registration_url()
    if not collector_url:
        logger.error("Cannot register with the log collector: no address was given.")
        return None

    response = requests.post(
        collector_url,
        json={
            "name": app_name or PROJECT_NAME,
            "description": description,
            "contact_email": contact_email
        },
        headers={
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json"
        }
    )

    if response.status_code == 200:
        token_data = response.json()
        with open('config/logger_token.json', 'w', encoding='utf-8') as f:
            json.dump({
                "token": token_data['api_token'],
                "app_id": token_data['id'],
                "app_name": token_data['name'],
                "created_at": datetime.utcnow().isoformat()
            }, f, indent=2)

        logger.info("Logger token created and saved to config/logger_token.json")
        return token_data['api_token']
    else:
        logger.error(f"Failed to create logger token: {response.text}")
        return None
