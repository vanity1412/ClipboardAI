"""Explicit exports and backups, using frozen conversation snapshots."""
import json
import sqlite3
import tempfile
import zipfile
from pathlib import Path


def search_records(archive, query=''):
    query = query.casefold().strip()
    return [r for r in archive['sessions'] if not query or query in
            '\n'.join([r.get('title', ''), r.get('problem', ''), r.get('capture_text', '')] +
                      [m['content'] for m in r.get('messages', [])]).casefold()]


def export_record(record, path):
    path = Path(path)
    if path.suffix.lower() == '.json':
        content = json.dumps(record, ensure_ascii=False, indent=2)
    else:
        content = '# ' + (record.get('title') or 'Hội thoại') + '\n\n'
        content += '\n\n'.join(('Bạn' if m['role'] == 'user' else 'AI') + ':\n' + m['content']
                               for m in record.get('messages', []))
        if not record.get('messages'):
            content += record.get('problem', '') + '\n\n' + record.get('last_answer', '')
    path.write_text(content, encoding='utf-8')


def backup_archive(archive, image_cache, path):
    # SQLite backup produces a consistent database even if SQLite uses a journal.
    # API keys and login credentials are deliberately excluded.
    with tempfile.TemporaryDirectory(prefix='clipboardai-backup-') as temporary:
        database = Path(temporary) / 'session-images.sqlite3'
        with image_cache.lock:
            if image_cache.storage is not None:
                source = sqlite3.connect(image_cache.storage.path)
                target = sqlite3.connect(database)
                try:
                    source.backup(target)
                finally:
                    target.close()
                    source.close()
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as output:
            output.writestr('session.json', json.dumps(archive, ensure_ascii=False))
            if database.exists():
                output.write(database, database.name)
            output.writestr('HUONG_DAN.txt', 'Đóng ClipboardAI trước khi khôi phục. Giải nén session.json và session-images.sqlite3 cạnh EXE. Bản sao lưu chứa nội dung và ảnh chưa mã hóa; không gồm API key.\n')
