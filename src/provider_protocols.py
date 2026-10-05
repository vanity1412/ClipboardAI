"""Translate messages and completed responses without accepting partial answers."""
import re


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
    if protocol == 'anthropic':
        if data.get('stop_reason') != 'end_turn' or any(b.get('type') not in ('text', 'thinking', 'redacted_thinking') for b in data.get('content', [])):
            raise RuntimeError('Anthropic chưa trả câu trả lời hoàn chỉnh; clipboard giữ nguyên')
        answer = ''.join(b['text'] for b in data.get('content', []) if b.get('type') == 'text')
    elif protocol == 'responses':
        if data.get('status') != 'completed':
            raise RuntimeError('Responses API chưa hoàn tất; clipboard giữ nguyên')
        output = data.get('output', [])
        if any(item.get('type') not in ('message', 'reasoning') for item in output):
            raise RuntimeError('API yêu cầu công cụ; clipboard giữ nguyên')
        blocks = [b for item in output if item.get('type') == 'message' for b in item.get('content', [])]
        if any(b.get('type') != 'output_text' for b in blocks):
            raise RuntimeError('API từ chối hoặc trả nội dung không hỗ trợ; clipboard giữ nguyên')
        answer = ''.join(b['text'] for b in blocks)
    else:
        choice = data['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise RuntimeError('API chưa trả câu trả lời hoàn chỉnh; clipboard giữ nguyên')
        answer = choice['message']['content']
    if not isinstance(answer, str) or not answer.strip():
        raise RuntimeError('API trả nội dung trống; clipboard giữ nguyên')
    return answer.strip()


class EventStream:
    """One SSE JSON event at a time; return raw provider data only at completion."""
    def __init__(self, protocol):
        self.protocol = protocol
        self.blocks = {}
        self.stop_reason = None

    def feed(self, item):
        kind = item.get('type')
        text = ''
        if self.protocol == 'anthropic':
            if kind == 'content_block_start':
                self.blocks[item['index']] = dict(item['content_block'])
                if item['content_block'].get('type') == 'text':
                    text = item['content_block'].get('text', '')
            elif kind == 'content_block_delta' and item.get('delta', {}).get('type') == 'text_delta':
                text = item['delta']['text']
                self.blocks[item['index']]['text'] += text
            elif kind == 'message_delta':
                self.stop_reason = item.get('delta', {}).get('stop_reason')
            elif kind == 'message_stop':
                return '', dict(stop_reason=self.stop_reason, content=[self.blocks[k] for k in sorted(self.blocks)])
        elif kind == 'response.output_text.delta':
            text = item['delta']
        elif kind == 'response.completed':
            return '', item['response']
        elif kind in ('response.failed', 'response.incomplete', 'error'):
            raise RuntimeError('API chưa hoàn tất câu trả lời; clipboard giữ nguyên')
        return text, None
