"""Portable conversation state, shared by the native control panel and worker."""
import json
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from chat_modes import CHAT, MODE_ORDER, VALID_MODES, is_chat, normalize_mode, clean_legacy_text

ANALYZE = "Phân tích và giải thích theo yêu cầu."
GENERATE = "Tiếp tục theo yêu cầu của người dùng."

VIETNAM_TIMEZONE = timezone(timedelta(hours=7))


def session_time(value):
    """Metadata must never break history navigation or use Windows mktime."""
    if not isinstance(value, str) or not value or len(value) > 128:
        return None
    try:
        # Python 3.8 does not accept the ISO UTC suffix Z.
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
        # A valid ISO date can still overflow when displayed in UTC+7.
        parsed.astimezone(VIETNAM_TIMEZONE)
        return parsed
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def session_time_key(value):
    return session_time(value) or datetime.min.replace(tzinfo=timezone.utc)


def format_session_time(value):
    parsed = session_time(value)
    if parsed is None:
        return "Không rõ thời gian"
    local = parsed.astimezone(VIETNAM_TIMEZONE)
    # Avoid platform-specific strftime restrictions on very early years.
    return (f"{local.day:02d}/{local.month:02d}/{local.year:04d} "
            f"{local.hour:02d}:{local.minute:02d}:{local.second:02d}")


class Session:
    def __init__(self, path):
        self.private = path is None
        self.path = Path(path) if path is not None else None
        self.messages = []
        self.mode = CHAT
        self.auto_copy = True
        self.copy_modes = {}
        self.summary = ""
        self.summary_count = 0
        self.history_full = True
        self.last_answer = ""
        self.problem = ""
        self.error = ""
        self._load_failed = False
        self.last_request = ""
        self.last_action = "problem"
        self.last_request_state = "pending"
        self.capture_text = ""
        self.capture_source = ""
        self.capture_pages = 0
        self.capture_missing = []
        self.active_id = uuid4().hex
        self.updated_at = ""
        self._sessions = {}
        self._saved_active_id = None
        self._saved_file_state = None
        self._current_format = False
        self._save_lock = threading.RLock()
        self._save_sequence = 0
        self._writer = None
        self._pending_saves = []
        self._queued_save = None
        self.needs_chat_start = False
        try:
            if self.path is not None and self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "sessions" in data:
                    records = data["sessions"]
                    if not isinstance(records, list):
                        raise ValueError("Invalid sessions")
                    validated = {}
                    for record in records:
                        if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not record["id"] or record["id"] in validated:
                            raise ValueError("Invalid session id")
                        record["id"].encode("utf-8")
                        validated[record["id"]] = self._validate(record)
                    if data.get("active_id") not in validated:
                        raise ValueError("Invalid active session")
                    active_id = data["active_id"]
                    active = validated[active_id]
                else:
                    active_id = self.active_id
                    active = self._validate(data)
                    if not active["updated_at"]:
                        active["updated_at"] = datetime.fromtimestamp(self.path.stat().st_mtime, timezone.utc).isoformat(timespec="microseconds")
                    validated = {active_id: active}
                # One-time upgrade for Q&A/analysis sessions. Later explicit
                # opt-outs are retained in version 4, including across restart.
                if data.get('version', 0) < 4:
                    for record in validated.values():
                        record['copy_modes'].update({'3': True, '4': True})
                        if record['mode'] in (3, 4):
                            record['auto_copy'] = True
                self._load(active)
                self.active_id, self._sessions = active_id, validated
                self._saved_active_id = active_id
                self._saved_file_state = self._disk_signature()
                self._current_format = data.get('version') == 5 and 'sessions' in data
                self.needs_chat_start = data.get('version', 0) in (0, 1, 2) and self.mode == 0
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            self._load_failed = True
            self.error = "Không đọc được session.json; lịch sử cũ chưa bị ghi đè."

    @staticmethod
    def _validate(data):
        if not isinstance(data, dict):
            raise ValueError("Invalid session object")
        messages = data.get("messages", [])
        if not isinstance(messages, list) or not all(isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) for m in messages):
            raise ValueError("Invalid history")
        values = {name: data.get(name, default) for name, default in (
            ("title", ""),
            ("problem", ""), ("last_answer", ""), ("last_request", ""),
            ("last_action", "problem"), ("capture_text", ""), ("capture_source", ""))}
        if not all(isinstance(value, str) for value in values.values()):
            raise ValueError("Invalid session text")
        for text in list(values.values()) + [m["content"] for m in messages]:
            text.encode("utf-8")
        mode = data.get("mode", 0)
        auto_copy = data.get("auto_copy", True)
        pages = data.get("capture_pages", 0)
        if type(mode) is not int or mode not in VALID_MODES or type(auto_copy) is not bool or type(pages) is not int or pages < 0:
            raise ValueError("Invalid session settings")
        summary, count = data.get("summary", ""), data.get("summary_count", 0)
        copies = data.get("copy_modes", {})
        if (not isinstance(summary, str) or type(count) is not int or not 0 <= count <= len(messages)
                or not isinstance(copies, dict) or any(k not in {str(m) for m in VALID_MODES} or type(v) is not bool for k, v in copies.items())):
            raise ValueError("Invalid conversation memory")
        summary.encode("utf-8")
        missing = data.get("capture_missing", [])
        if not isinstance(missing, list) or not all(isinstance(x, str) for x in missing):
            raise ValueError("Invalid capture metadata")
        for text in missing:
            text.encode("utf-8")
        updated = data.get("updated_at", "")
        parsed_time = session_time(updated)
        # Bad optional metadata must not invalidate otherwise healthy sessions.
        updated = parsed_time.isoformat(timespec="microseconds") if parsed_time else ""
        # Preserve the original problem when importing a longer legacy history.
        history_full = data.get('history_full', is_chat(mode))
        if type(history_full) is not bool:
            raise ValueError('Invalid history policy')
        history = [dict(m, content=clean_legacy_text(m['content'])) if m['role'] == 'user' else dict(m) for m in messages]
        values['last_request'] = clean_legacy_text(values['last_request'])
        # Older archives recorded only the text, so retain their completed-turn
        # regeneration behavior when the final pair matches. New requests carry
        # explicit provenance: equal text alone never proves a turn completed.
        request_state = data.get('last_request_state')
        if request_state is None:
            completed = (bool(values['last_request']) and len(history) >= 2
                and history[-2]['role'] == 'user' and history[-1]['role'] == 'assistant'
                and history[-2]['content'] in (values['last_request'], values['last_request'] + '\n[Ảnh đính kèm]'))
            request_state = 'completed' if completed else 'pending'
        if request_state not in ('pending', 'completed', 'retrying_completed'):
            raise ValueError('Invalid last request state')
        return dict(values, messages=deepcopy(history), mode=normalize_mode(mode), auto_copy=auto_copy,
                    copy_modes={str(normalize_mode(int(k))): v for k, v in copies.items()}, summary=summary, summary_count=count,
                    history_full=True,
                    capture_pages=pages, capture_missing=list(missing), updated_at=updated,
                    last_request_state=request_state)

    def _load(self, record):
        for name, value in record.items():
            setattr(self, name, deepcopy(value))

    def _snapshot(self):
        return dict(title=getattr(self, 'title', ''), messages=self.messages, problem=self.problem, last_answer=self.last_answer,
                    last_request=self.last_request, last_action=self.last_action,
                    last_request_state=self.last_request_state,
                    mode=self.mode, auto_copy=self.auto_copy, capture_text=self.capture_text,
                    copy_modes=self.copy_modes, summary=self.summary, summary_count=self.summary_count,
                    history_full=self.history_full,
                    capture_source=self.capture_source, capture_pages=self.capture_pages,
                    capture_missing=self.capture_missing, updated_at=self.updated_at)

    def _prepare_save(self):
        """Freeze a validated snapshot; the writer never reads live messages."""
        if self.private:
            self._sessions[self.active_id] = deepcopy(self._validate(self._snapshot()))
            return True
        if self._load_failed:
            return None
        record = self._validate(self._snapshot())
        queued = self._queued_save
        if queued and queued[0] == self.active_id and queued[1] == record and not queued[2].done():
            return queued[2]
        if ((not queued or queued[2].done()) and self._current_format and self.active_id == self._saved_active_id
                and record == self._sessions.get(self.active_id)
                and self._disk_signature() == self._saved_file_state and self._saved_file_state is not None):
            self.error = ''
            return True
        if record != self._sessions.get(self.active_id):
            self.updated_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            record['updated_at'] = self.updated_at
        records = dict(self._sessions)
        records[self.active_id] = record
        self._save_sequence += 1
        return self.active_id, record, records, self._save_sequence

    def _disk_signature(self):
        try:
            stat = self.path.stat()
            return stat.st_size, stat.st_mtime_ns
        except FileNotFoundError:
            return None

    def _write_archive(self, ident, record, records, sequence):
        try:
            temporary = self.path.with_suffix(".tmp")
            # Keep the active fields for compatibility with existing utilities.
            data = dict(record, version=5, active_id=ident,
                sessions=[dict(value, id=session_id) for session_id, value in records.items()])
            with temporary.open('w', encoding='utf-8') as output:
                json.dump(data, output, ensure_ascii=False, separators=(',', ':'))
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self.path)
            with self._save_lock:
                self._sessions = records
                self._saved_active_id = ident
                self._saved_file_state = self._disk_signature()
                self._current_format = True
                if sequence == self._save_sequence:
                    self.error = ""
            return True
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            with self._save_lock:
                if sequence == self._save_sequence:
                    self.error = "Chưa lưu được lịch sử; kết quả chỉ ở bộ nhớ. Kiểm tra ổ đĩa/quyền ghi, đừng thoát app."
            return False

    def flush(self):
        """Wait for ordered background writes and report any failure."""
        with self._save_lock:
            pending, self._pending_saves = self._pending_saves, []
        results = [future.result() for future in pending]
        return all(results)

    def close(self):
        # A failed background write may be recoverable by the time the user
        # exits. Retry the current snapshot before releasing the writer.
        saved = self.save()
        if self._writer is not None:
            self._writer.shutdown(wait=True)
            self._writer = None
        return saved

    def save(self):
        # Session switching/deletion cannot outrun pending writes. A prior
        # failure must retry the current snapshot before any transition.
        self.flush()
        try:
            with self._save_lock:
                prepared = self._prepare_save()
            if prepared is None or prepared is True:
                return bool(prepared)
            if isinstance(prepared, Future):
                return prepared.result()
            return self._write_archive(*prepared)
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            self.error = "Chưa lưu được lịch sử; kết quả chỉ ở bộ nhớ. Kiểm tra ổ đĩa/quyền ghi, đừng thoát app."
            return False

    def save_async(self):
        """Return a Future[bool]; success means replace and fsync completed."""
        try:
            with self._save_lock:
                prepared = self._prepare_save()
                if isinstance(prepared, Future):
                    return prepared
                if isinstance(prepared, tuple):
                    if self._writer is None:
                        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix='session-save')
                    future = self._writer.submit(self._write_archive, *prepared)
                    self._pending_saves.append(future)
                    self._queued_save = (prepared[0], prepared[1], future)
                    return future
                future = Future()
                future.set_result(bool(prepared))
                return future
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            self.error = "Chưa lưu được lịch sử; kết quả chỉ ở bộ nhớ. Kiểm tra ổ đĩa/quyền ghi, đừng thoát app."
            future = Future()
            future.set_result(False)
            return future

    def new_problem(self, problem="", mode=None):
        if not self.save():
            return False
        previous_id, previous_records = self.active_id, dict(self._sessions)
        if not any(entry["id"] == self.active_id for entry in self.entries()):
            self._sessions.pop(self.active_id, None)
        mode, auto_copy = (self.mode, self.auto_copy) if mode is None else (mode, self.copy_modes.get(str(mode), True))
        self.active_id = uuid4().hex
        self._load(self._validate(dict(problem=problem, last_request=problem,
                                      mode=mode, auto_copy=auto_copy, copy_modes=self.copy_modes, history_full=True)))
        if self.save():
            return True
        # A failed second write must not leave a new empty session selected.
        self._sessions, self.active_id = previous_records, previous_id
        self._load(previous_records[previous_id])
        return False

    def select(self, ident):
        if ident not in self._sessions or not self.save():
            return False
        previous_id = self.active_id
        self.active_id = ident
        self._load(self._sessions[ident])
        if self.save():
            return True
        self.active_id = previous_id
        self._load(self._sessions[previous_id])
        return False

    def entries(self):
        records = dict(self._sessions)
        records[self.active_id] = self._snapshot()
        entries = []
        for ident, record in records.items():
            text = record["problem"] or record["capture_text"] or record["last_request"]
            if not text and not record["messages"] and not record["last_answer"]:
                continue
            title = record.get('title') or next((line.strip() for line in text.splitlines() if line.strip()), "Bài chưa có tên")
            title = title if len(title) <= 72 else title[:69] + "…"
            entries.append(dict(id=ident, title=title, updated_at=record["updated_at"]))
        return sorted(entries, key=lambda entry: session_time_key(entry["updated_at"]), reverse=True)

    def reset(self):
        if not self._load_failed and not self.save():
            return False
        previous_id, previous_records = self.active_id, dict(self._sessions)
        previous = deepcopy(self._snapshot())
        previous_load_failed = self._load_failed
        self._sessions.pop(self.active_id, None)
        self.active_id = uuid4().hex
        self.updated_at = ""
        self.messages, self.problem, self.last_answer = [], "", ""
        self.title = ''
        self.summary, self.summary_count = "", 0
        self.last_request = ""
        self.last_action = "problem"
        self.last_request_state = "pending"
        self.clear_capture(save=False)
        if self._load_failed and self.path is not None and self.path.exists():
            try:
                backup = self.path.with_suffix(".unreadable.json")
                number = 1
                while backup.exists():
                    backup = self.path.with_suffix(f".unreadable.{number}.json")
                    number += 1
                self.path.replace(backup)
            except OSError:
                self._sessions, self.active_id = previous_records, previous_id
                self._load(previous)
                return False
        self.error = ""
        self._load_failed = False
        if self.save():
            return True
        self._sessions, self.active_id = previous_records, previous_id
        self._load(previous)
        self._load_failed = previous_load_failed
        return False

    def rename(self, ident, title):
        title = title.strip()
        if not title or len(title) > 120:
            raise ValueError('Tên hội thoại phải có 1–120 ký tự')
        title.encode('utf-8')
        if not self.save() or ident not in self._sessions:
            return False
        previous = deepcopy(self._sessions[ident])
        self._sessions[ident]['title'] = title
        self._current_format = False
        if ident == self.active_id:
            self.title = title
        if self.save():
            return True
        self._sessions[ident] = previous
        if ident == self.active_id:
            self.title = previous.get('title', '')
        return False

    def archive_snapshot(self):
        with self._save_lock:
            records = deepcopy(self._sessions)
            records[self.active_id] = deepcopy(self._validate(self._snapshot()))
            return dict(version=5, active_id=self.active_id,
                        sessions=[dict(record, id=ident) for ident, record in records.items()])

    def clear_capture(self, save=True):
        self.capture_text, self.capture_source = "", ""
        self.capture_pages, self.capture_missing = 0, []
        if save:
            self.save()

    def context(self, num_ctx=4096):
        if is_chat(self.mode):
            history = self.messages[self.summary_count:]
            return ([dict(role="assistant", content="Tóm tắt hội thoại trước:\n" + self.summary)] if self.summary else []) + deepcopy(history)
        # Conservative character budget. Reserve room for system/current request;
        # keep original problem and newest answer as the essential repair context.
        budget = max(1000, int(num_ctx) * 2)
        picked = []
        for message in reversed(self.messages[1:]):
            if sum(len(m["content"]) for m in picked) + len(message["content"]) > budget:
                if not picked:
                    raise ValueError("Lời giải gần nhất quá dài cho context; tăng context để sửa bài.")
                break
            picked.insert(0, dict(message))
        if self.messages:
            picked.insert(0, dict(self.messages[0]))
        return picked

    def commit(self, turns, answer, problem=None, asynchronous=False):
        self.messages.extend(turns)
        if not self.history_full and len(self.messages) > 20:
            self.messages = [self.messages[0]] + self.messages[-19:]
            self.summary, self.summary_count = "", 0
        if problem is not None:
            self.problem = problem
        self.last_answer = answer
        self.last_request_state = 'completed'
        return self.save_async() if asynchronous else self.save()

    def commit_async(self, turns, answer, problem=None):
        return self.commit(turns, answer, problem, asynchronous=True)

    def set_mode(self, mode):
        mode = normalize_mode(mode)
        if mode not in MODE_ORDER:
            return False
        self.copy_modes[str(self.mode)] = self.auto_copy
        self.mode = mode
        if is_chat(mode):
            self.history_full = True
        self.auto_copy = self.copy_modes.get(str(mode), True)
        return self.save()
