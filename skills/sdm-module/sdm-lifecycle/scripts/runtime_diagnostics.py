"""Stdlib diagnostics for project-owned launchers; never dispatches compute."""
import datetime
import errno
import json
from pathlib import Path
import re
import sys
import time
import traceback


class PreflightError(RuntimeError):
    def __init__(self, check_id):
        self.check_id = check_id
        super().__init__('preflight_failed:' + check_id)


class RuntimeDiagnostics:
    """Log named checks to stdout before mounts exist, plus optional owned sinks.

    Supply only intentionally selected, non-sensitive observations. Never pass
    environments, credentials, arbitrary command output or model/user payloads.
    Exception frames omit locals, source text and exception messages by design.
    """
    def __init__(self, stream=None):
        self.stream = stream if stream is not None else sys.stdout
        self.records = []
        self.sinks = []
        self.active_stage = 'bootstrap'
        self.stage_started = time.monotonic()
        self.failed_check = None

    @staticmethod
    def safe(value):
        if isinstance(value, dict):
            return {str(k): ('[REDACTED]' if re.search(
                r'password|secret|token|credential|api.?key|authorization', str(k), re.I)
                else RuntimeDiagnostics.safe(v)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [RuntimeDiagnostics.safe(v) for v in value]
        if isinstance(value, str):
            value = re.sub(r'(?i)(https?://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', value)
            return re.sub(r'(?i)((?:password|secret|token|api[_-]?key|authorization)\s*[=:]\s*)[^\s,;&]+',
                          r'\1[REDACTED]', value)[:2000]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return type(value).__name__

    def event(self, event, **fields):
        row = self.safe({'schema': 'sdm-runtime-diagnostic/v1', 'event': event,
                         'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                         'stage': self.active_stage, **fields})
        self.records.append(row)
        self._stdout(row)
        for sink in tuple(self.sinks):
            try:
                with sink.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            except OSError as error:
                self.sinks.remove(sink)
                self.event('diagnostic_sink_unavailable', sink_name=sink.name,
                           exception_type=type(error).__name__, errno=error.errno)
        return row

    def _stdout(self, row):
        try:
            print('BMAD_NATIVE_EVENT ' + json.dumps(row, ensure_ascii=False), file=self.stream, flush=True)
        except (OSError, ValueError):
            # Local sink failure must not mask the original computation failure.
            pass

    def attach(self, directory):
        """Attach a caller-owned directory; preserve any pre-existing journal."""
        sink = Path(directory) / 'runtime-diagnostics.jsonl'
        try:
            if sink.is_symlink():
                raise OSError(errno.EEXIST, 'existing diagnostic link')
            with sink.open('x', encoding='utf-8') as stream:
                for row in self.records:
                    stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            self.sinks.append(sink)
            self.event('diagnostic_sink_attached', sink_name=sink.name)
            return True
        except OSError as error:
            self.event('diagnostic_sink_unavailable', sink_name=sink.name,
                       exception_type=type(error).__name__, errno=error.errno)
            return False

    def stage(self, stage):
        self.active_stage = stage
        self.stage_started = time.monotonic()
        self.event('stage_started')

    def exception(self, error, *, operation='execution'):
        frames = [{'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name}
                  for f in traceback.extract_tb(error.__traceback__)]
        self.event('exception', operation=operation, check_id=self.failed_check,
                   exception_type=type(error).__name__, errno=getattr(error, 'errno', None),
                   elapsed_stage_seconds=round(time.monotonic() - self.stage_started, 6), traceback=frames)

    def check(self, check_id, expected, observe, matches=None):
        started = time.monotonic()
        self.event('check_started', check_id=check_id, expected=expected)
        try:
            actual = observe()
            passed = matches(actual) if matches is not None else actual == expected
        except Exception as error:
            self.failed_check = check_id
            self.event('check_failed', check_id=check_id, expected=expected,
                       actual=None, elapsed_seconds=round(time.monotonic() - started, 6))
            self.exception(error, operation='check')
            raise PreflightError(check_id) from error
        self.event('check_passed' if passed else 'check_failed', check_id=check_id,
                   expected=expected, actual=actual,
                   elapsed_seconds=round(time.monotonic() - started, 6))
        if not passed:
            self.failed_check = check_id
            raise PreflightError(check_id)
        return actual

    def finish(self, status, *, training_started=False, reason=None):
        return self.event('terminal', status=status, reason=reason,
                          failed_check=self.failed_check, training_started=training_started,
                          automatic_retries=0, platform_release_verified=False)
