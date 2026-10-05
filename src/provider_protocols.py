"""Translate messages and completed responses without accepting partial answers."""
import re


def check_answer_message(message):
    """Completion flags never override refusal or tool metadata."""
    if not isinstance(message, dict) or any(message.get(field) for field in ('refusal', 'tool_calls', 'function_call')):
        raise RuntimeError('API từ chối hoặc yêu cầu công cụ; clipboard giữ nguyên')


def text_message(message):
    """Accept completed answer text only."""
    check_answer_message(message)
    answer = message.get('content')
    if not isinstance(answer, str) or not answer.strip():
        raise RuntimeError('API trả nội dung trống hoặc sai định dạng; clipboard giữ nguyên')
    return answer


def stream_error(protocol, item):
    """Map only documented retryable API errors, without exposing their text."""
    error = item.get('error')
    if item.get('type') == 'response.failed':
        error = item.get('response', {}).get('error')
    if not error and item.get('type') == 'error':
        error = item
    if not error:
        return
    from api_zoo import APIHTTPError
    error = error if isinstance(error, dict) else {}
    if protocol == 'anthropic':
        statuses = {'overloaded_error': 529, 'api_error': 500, 'timeout_error': 504,
                    'rate_limit_error': 429, 'authentication_error': 401,
                    'permission_error': 403, 'not_found_error': 404,
                    'invalid_request_error': 400}
        status = statuses.get(error.get('type'))
    else:
        status = {'server_error': 500, 'rate_limit_exceeded': 429}.get(error.get('code'))
    if status is not None:
        raise APIHTTPError(status)
    raise RuntimeError('API báo lỗi xử lý; clipboard giữ nguyên')


def parts(content, protocol):
    if isinstance(content, str):
        content = [{'type': 'text', 'text': content}]
    result = []
    for block in content:
        if block['type'] == 'text':
            result.append({'type': 'text' if protocol == 'anthropic' else 'input_text', 'text': block['text']})
        elif block['type'] == 'image_url':
            url = block['image_url']['url']
            if protocol == 'anthropic':
                match = re.fullmatch(r'data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=]+)', url)
                if not match:
                    raise ValueError('Anthropic cần ảnh PNG/JPEG/GIF/WebP dạng base64')
                result.append({'type': 'image', 'source': {'type': 'base64', 'media_type': match[1], 'data': match[2]}})
            else:
                result.append({'type': 'input_image', 'image_url': url})
        else:
            raise ValueError('Nội dung gửi API không được hỗ trợ')
    return result


def request_body(protocol, model, messages, tokens, stream):
    body = dict(model=model, stream=stream)
    if protocol == 'anthropic':
        body['max_tokens'] = tokens or 8192  # Mandatory in the Messages API.
        body['system'] = '\n\n'.join(m['content'] for m in messages if m['role'] == 'system')
        body['messages'] = [dict(role=m['role'], content=parts(m['content'], protocol)) for m in messages if m['role'] != 'system']
    elif protocol == 'responses':
        body['store'] = False
        body['input'] = []
        for m in messages:
            if m['role'] == 'assistant':
                body['input'].append(dict(role='assistant', content=m['content']))
            else:
                body['input'].append(dict(role=m['role'], content=parts(m['content'], protocol)))
        if tokens:
            body['max_output_tokens'] = tokens
    else:
        body['messages'] = messages
        if tokens:
            body['max_tokens'] = tokens
    return body


def completed_response(protocol, data):
    if not isinstance(data, dict):
        raise RuntimeError('API trả phản hồi sai định dạng; clipboard giữ nguyên')
    if protocol == 'anthropic':
        if data.get('stop_reason') != 'end_turn' or any(b.get('type') not in ('text', 'thinking', 'redacted_thinking') for b in data.get('content', [])):
            raise RuntimeError('Anthropic chưa trả câu trả lời hoàn chỉnh; clipboard giữ nguyên')
        answer = ''.join(b['text'] for b in data.get('content', []) if b.get('type') == 'text')
    elif protocol == 'responses':
        if data.get('status') != 'completed' or data.get('error') or data.get('incomplete_details'):
            raise RuntimeError('Responses API chưa hoàn tất; clipboard giữ nguyên')
        output = data.get('output', [])
        if any(item.get('type') not in ('message', 'reasoning') for item in output):
            raise RuntimeError('API yêu cầu công cụ; clipboard giữ nguyên')
        messages = [item for item in output if item.get('type') == 'message']
        if any(item.get('status') not in (None, 'completed') or
               item.get('role') not in (None, 'assistant') or
               item.get('phase') not in (None, 'commentary', 'final_answer') for item in messages):
            raise RuntimeError('API chưa trả câu trả lời hoàn chỉnh; clipboard giữ nguyên')
        # Preserve compatibility with endpoints that omit phase. Explicit
        # commentary is never a final answer, including commentary-only turns.
        all_blocks = [b for item in messages for b in item.get('content', [])]
        if any(b.get('type') != 'output_text' for b in all_blocks):
            raise RuntimeError('API từ chối hoặc trả nội dung không hỗ trợ; clipboard giữ nguyên')
        final = [item for item in messages if item.get('phase') == 'final_answer']
        messages = final or [item for item in messages if item.get('phase') is None]
        blocks = [b for item in messages for b in item.get('content', [])]
        answer = ''.join(b['text'] for b in blocks)
    else:
        choice = data['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise RuntimeError('API chưa trả câu trả lời hoàn chỉnh; clipboard giữ nguyên')
        answer = text_message(choice['message'])
    if not isinstance(answer, str) or not answer.strip():
        raise RuntimeError('API trả nội dung trống; clipboard giữ nguyên')
    return answer.strip()


class EventStream:
    """One SSE JSON event at a time; return raw provider data only at completion."""
    def __init__(self, protocol):
        self.protocol = protocol
        self.blocks = {}
        self.stop_reason = None
        self.started = False
        self.open_blocks = set()

    def feed(self, item):
        kind = item.get('type')
        text = ''
        if self.protocol == 'anthropic':
            if kind == 'message_start':
                if self.started:
                    raise RuntimeError('Anthropic trả stream sai thứ tự; clipboard giữ nguyên')
                self.started = True
            elif kind == 'content_block_start':
                if not self.started or self.stop_reason is not None or item['index'] in self.blocks:
                    raise RuntimeError('Anthropic trả stream sai thứ tự; clipboard giữ nguyên')
                self.blocks[item['index']] = dict(item['content_block'])
                self.open_blocks.add(item['index'])
                if item['content_block'].get('type') == 'text':
                    text = item['content_block'].get('text', '')
            elif kind == 'content_block_delta' and item.get('delta', {}).get('type') == 'text_delta':
                if item['index'] not in self.open_blocks or self.blocks[item['index']].get('type') != 'text':
                    raise RuntimeError('Anthropic trả stream sai thứ tự; clipboard giữ nguyên')
                text = item['delta']['text']
                self.blocks[item['index']]['text'] += text
            elif kind == 'content_block_stop':
                if item['index'] not in self.open_blocks:
                    raise RuntimeError('Anthropic trả stream sai thứ tự; clipboard giữ nguyên')
                self.open_blocks.remove(item['index'])
            elif kind == 'message_delta':
                if not self.started or self.open_blocks:
                    raise RuntimeError('Anthropic trả stream sai thứ tự; clipboard giữ nguyên')
                reason = item.get('delta', {}).get('stop_reason')
                if reason is not None:
                    self.stop_reason = reason
            elif kind == 'message_stop':
                if not self.started or self.open_blocks or self.stop_reason is None:
                    raise RuntimeError('Anthropic chưa hoàn tất stream; clipboard giữ nguyên')
                return '', dict(stop_reason=self.stop_reason, content=[self.blocks[k] for k in sorted(self.blocks)])
        elif kind == 'response.output_text.delta':
            text = item['delta']
        elif kind == 'response.completed':
            return '', item['response']
        elif kind in ('response.failed', 'response.incomplete', 'error'):
            raise RuntimeError('API chưa hoàn tất câu trả lời; clipboard giữ nguyên')
        return text, None
