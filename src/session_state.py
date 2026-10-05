"""Portable conversation state, shared by the native control panel and worker."""
import json
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
        self.path = Path(path)
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
        self.capture_text = ""
        self.capture_source = ""
        self.capture_pages = 0
        self.capture_missing = []
        self.active_id = uuid4().hex
        self.updated_at = ""
        self._sessions = {}
        self.needs_chat_start = False
        try:
            if self.path.exists():
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
        return dict(values, messages=deepcopy(history), mode=normalize_mode(mode), auto_copy=auto_copy,
                    copy_modes={str(normalize_mode(int(k))): v for k, v in copies.items()}, summary=summary, summary_count=count,
                    history_full=True,
                    capture_pages=pages, capture_missing=list(missing), updated_at=updated)

    def _load(self, record):
        for name, value in record.items():
            setattr(self, name, deepcopy(value))

    def _snapshot(self):
        return dict(messages=self.messages, problem=self.problem, last_answer=self.last_answer,
                    last_request=self.last_request, last_action=self.last_action,
                    mode=self.mode, auto_copy=self.auto_copy, capture_text=self.capture_text,
                    copy_modes=self.copy_modes, summary=self.summary, summary_count=self.summary_count,
                    history_full=self.history_full,
                    capture_source=self.capture_source, capture_pages=self.capture_pages,
                    capture_missing=self.capture_missing, updated_at=self.updated_at)

    def save(self):
        try:
            # Corrupt input stays protected. Transient write failures may retry
            # with the in-memory answer once the storage becomes writable.
            if self._load_failed:
                return False
            record = self._validate(self._snapshot())
            if record != self._sessions.get(self.active_id):
                self.updated_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
                record["updated_at"] = self.updated_at
            records = dict(self._sessions)
            records[self.active_id] = record
            temporary = self.path.with_suffix(".tmp")
            # Keep the active fields for compatibility with existing utilities.
            temporary.write_text(json.dumps(dict(record, version=4, active_id=self.active_id,
                sessions=[dict(value, id=ident) for ident, value in records.items()]), ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)
            self._sessions = records
            self.error = ""
            return True
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            self.error = "Chưa lưu được lịch sử; kết quả chỉ ở bộ nhớ. Kiểm tra ổ đĩa/quyền ghi, đừng thoát app."
            return False

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
            title = next((line.strip() for line in text.splitlines() if line.strip()), "Bài chưa có tên")
            title = title if len(title) <= 72 else title[:69] + "…"
            entries.append(dict(id=ident, title=title, updated_at=record["updated_at"]))
        return sorted(entries, key=lambda entry: session_time_key(entry["updated_at"]), reverse=True)

    def reset(self):
        self._sessions.pop(self.active_id, None)
        self.active_id = uuid4().hex
        self.updated_at = ""
        self.messages, self.problem, self.last_answer = [], "", ""
        self.summary, self.summary_count = "", 0
        self.last_request = ""
        self.last_action = "problem"
        self.clear_capture(save=False)
        if self._load_failed and self.path.exists():
            try:
                backup = self.path.with_suffix(".unreadable.json")
                number = 1
                while backup.exists():
                    backup = self.path.with_suffix(f".unreadable.{number}.json")
                    number += 1
                self.path.replace(backup)
            except OSError:
                return
        self.error = ""
        self._load_failed = False
        return self.save()

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

    def commit(self, turns, answer, problem=None):
        self.messages.extend(turns)
        if not self.history_full and len(self.messages) > 20:
            self.messages = [self.messages[0]] + self.messages[-19:]
            self.summary, self.summary_count = "", 0
        if problem is not None:
            self.problem = problem
        self.last_answer = answer
        return self.save()

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
