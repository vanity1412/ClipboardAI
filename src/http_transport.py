"""Bounded, cancellable HTTP with the same system proxy routing for every call."""
import asyncio
import base64
import json
import ssl
import time
from urllib.parse import unquote, urlsplit
from urllib.request import getproxies, proxy_bypass

from text_safety import validate_unicode

MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_HEADER_BYTES = 64 * 1024


class HTTPStatusError(RuntimeError):
    def __init__(self, status, retry_after=None):
        self.status = int(status)
        self.retry_after = retry_after
        super().__init__('AI HTTP ' + str(self.status))


def authority(address):
    hostname = address.hostname
    if not hostname:
        raise ValueError('Endpoint không hợp lệ')
    if ':' in hostname:
        hostname = '[' + hostname + ']'
    default = 443 if address.scheme == 'https' else 80
    return hostname + (':' + str(address.port) if address.port and address.port != default else '')


async def response_headers(reader):
    total = 0
    status = await reader.readline()
    total += len(status)
    fields = status.split()
    if len(fields) < 2 or not fields[0].startswith(b'HTTP/') or not fields[1].isdigit():
        raise ConnectionError('API trả HTTP không hợp lệ')
    headers = {}
    while True:
        line = await reader.readline()
        total += len(line)
        if total > MAX_HEADER_BYTES:
            raise ValueError('HTTP header vượt giới hạn an toàn')
        if line in (b'\r\n', b'\n'):
            return int(fields[1]), headers
        if not line or b':' not in line:
            raise ConnectionError('Kết nối ngắt trước khi nhận đủ HTTP header')
        name, value = line.decode('iso-8859-1').split(':', 1)
        headers[name.lower()] = value.strip()


async def close_writer(writer):
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), .5)
    except (Exception, asyncio.CancelledError):
        writer.transport.abort()
        await asyncio.sleep(0)


async def open_http_connection(url):
    """Return reader, writer, request target and headers needed by an HTTP proxy.

    Proxy credentials go only to the proxy. CONNECT never contains the API key.
    TLS still validates the origin hostname after a proxy tunnel is established.
    """
    address = urlsplit(url)
    if address.scheme not in ('http', 'https') or not address.hostname or address.username or address.password:
        raise ValueError('Endpoint HTTP không hợp lệ')
    host = authority(address)
    proxy_url = None if proxy_bypass(host) else getproxies().get(address.scheme)
    route = urlsplit(proxy_url if proxy_url and '://' in proxy_url else 'http://' + proxy_url) if proxy_url else address
    if route.scheme not in ('http', 'https') or not route.hostname:
        raise ValueError('Proxy cần dùng HTTP hoặc HTTPS')
    port = route.port or (443 if route.scheme == 'https' else 80)
    reader, writer = await asyncio.open_connection(route.hostname, port,
        ssl=ssl.create_default_context() if route.scheme == 'https' else None)
    headers = {}
    path = address.path or '/'
    if address.query:
        path += '?' + address.query
    if proxy_url:
        proxy_auth = ''
        if route.username is not None:
            credentials = unquote(route.username) + ':' + unquote(route.password or '')
            proxy_auth = 'Basic ' + base64.b64encode(credentials.encode('utf-8')).decode('ascii')
        if address.scheme == 'https':
            tunnel_host = ('[' + address.hostname + ']' if ':' in address.hostname else address.hostname) + ':' + str(address.port or 443)
            connect = 'CONNECT ' + tunnel_host + ' HTTP/1.1\r\nHost: ' + tunnel_host + '\r\n'
            if proxy_auth:
                connect += 'Proxy-Authorization: ' + proxy_auth + '\r\n'
            try:
                writer.write((connect + '\r\n').encode('ascii'))
                await writer.drain()
                status, _ = await response_headers(reader)
                if status != 200:
                    raise ConnectionError('Proxy từ chối kết nối HTTPS (' + str(status) + ')')
                await writer.start_tls(ssl.create_default_context(), server_hostname=address.hostname)
            except BaseException:
                await close_writer(writer)
                raise
        else:
            path = url
            if proxy_auth:
                headers['Proxy-Authorization'] = proxy_auth
    return reader, writer, path, headers


async def bounded_body(reader, headers, max_bytes):
    body = bytearray()
    if 'chunked' in headers.get('transfer-encoding', '').lower():
        while True:
            line = await reader.readline()
            if not line:
                raise ConnectionError('Kết nối ngắt trước khi hoàn tất')
            size = int(line.split(b';', 1)[0].strip(), 16)
            if size < 0 or len(body) + size > max_bytes:
                raise ValueError('Phản hồi vượt giới hạn an toàn')
            if size == 0:
                return bytes(body)
            body.extend(await reader.readexactly(size))
            if await reader.readexactly(2) != b'\r\n':
                raise ConnectionError('HTTP chunk không hợp lệ')
    size = headers.get('content-length')
    remaining = int(size) if size is not None else None
    if remaining is not None and (remaining < 0 or remaining > max_bytes):
        raise ValueError('Phản hồi vượt giới hạn an toàn')
    while remaining is None or remaining:
        data = await reader.read(min(65536, remaining) if remaining is not None else 65536)
        if not data:
            if remaining:
                raise ConnectionError('Kết nối ngắt trước khi hoàn tất')
            break
        body.extend(data)
        if len(body) > max_bytes:
            raise ValueError('Phản hồi vượt giới hạn an toàn')
        if remaining is not None:
            remaining -= len(data)
    return bytes(body)


def request_json(url, headers=None, timeout=20, cancel=None, max_bytes=MAX_RESPONSE_BYTES,
                 method='GET', payload=None, return_size=False):
    """Run one request with an overall deadline and cooperative socket cancellation."""
    if cancel is not None and cancel.is_set():
        raise InterruptedError('Đã hủy yêu cầu')
    async def request():
        reader, writer, target, proxy_headers = await open_http_connection(url)
        try:
            selected = dict(proxy_headers, Host=authority(urlsplit(url)),
                            Connection='close', **{'User-Agent': 'ClipboardAI/2.0'})
            selected.update(headers or {})
            if payload is not None:
                selected['Content-Length'] = str(len(payload))
            if any('\r' in str(value) or '\n' in str(value) for value in selected.values()):
                raise ValueError('HTTP header không hợp lệ')
            head = method + ' ' + target + ' HTTP/1.1\r\n' + ''.join(
                name + ': ' + str(value) + '\r\n' for name, value in selected.items()) + '\r\n'
            writer.write(head.encode('ascii') + (payload or b''))
            await writer.drain()
            status, response = await response_headers(reader)
            if status != 200:
                raise HTTPStatusError(status, response.get('retry-after'))
            raw = await bounded_body(reader, response, max_bytes)
            result = validate_unicode(json.loads(raw.decode('utf-8')))
            return (result, len(raw)) if return_size else result
        finally:
            await close_writer(writer)
    async def run():
        task = asyncio.create_task(request())
        started = time.monotonic()
        try:
            while not task.done():
                if cancel is not None and cancel.is_set():
                    raise InterruptedError('Đã hủy yêu cầu')
                if timeout and time.monotonic() - started >= timeout:
                    raise TimeoutError()
                await asyncio.wait((task,), timeout=.05)
            return await task
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (Exception, asyncio.CancelledError):
                    pass
    try:
        return asyncio.run(run())
    except asyncio.IncompleteReadError:
        raise ConnectionError('Kết nối ngắt trước khi hoàn tất') from None
