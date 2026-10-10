"""Opt-in live provider checks with synthetic text/image only; never log keys."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from api_zoo import ZooStore, apply_config
from cloud_client import CloudClient
from model_reasoning import effort_for


def check(profile, vision, detail, effort=None, agent=False):
    import copy
    profile = copy.deepcopy(profile)
    model = profile['vision_model'] if vision else profile['model']
    if effort is not None:
        profile['reasoning_effort'] = effort
        profile['model_efforts'] = {}
    row = dict(api=profile['name'], model=model, vision=vision,
               effort=effort_for(profile, model), detail=detail if vision else None)
    config = {}
    profile.update(timeout=60, max_tokens=2048)
    apply_config(config, {'profiles': [profile], 'primary': profile['id'], 'auto': False}, select_primary=True)
    client = CloudClient(config)
    client.cancel_event = threading.Event()
    client.deadline = time.monotonic() + 65
    content = 'Reply with only OK.'
    if vision:
        from PIL import Image
        image = Image.new('RGB', (64, 64), (0, 200, 0))
        output = BytesIO()
        image.save(output, format='PNG')
        image.close()
        content = [{'type': 'text', 'text': 'This is a synthetic test image. Reply only with the main color.'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode(), 'detail': detail}}]
    instruction = None
    if agent:
        from desktop_agent import INSTRUCTION
        instruction = INSTRUCTION
        content[0]['text'] = ('QUAN SÁT MỚI NHẤT: Synthetic test window. All questions have been answered. '
            'The full page and its bottom are visible; no scrolling is needed. There is no Submit button. '
            'TIẾN ĐỘ: {"known_groups":1,"known_answered_groups":1,"known_unanswered_count":0,"end_visible":true}. '
            'CÔNG CỤ: []. Return done:true with a short synthetic-test status, using the instructed JSON format.')
        row['agent_format'] = True
    started = time.monotonic()
    try:
        answer, provider = client.ask(content, instruction=instruction, json_output=agent)
        row.update(ok=True, answer_characters=len(answer))
        if agent:
            from desktop_agent import decision
            parsed = decision(answer)
            row['valid_agent_decision'] = parsed.get('done') is True
    except Exception as exc:
        chain = []
        while exc is not None and len(chain) < 5:
            chain.append({'type': type(exc).__name__, 'status': getattr(exc, 'status', None),
                          'code': getattr(exc, 'code', None)})
            exc = exc.__cause__
        row.update(ok=False, errors=chain)
    row['seconds'] = round(time.monotonic() - started, 2)
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root')
    parser.add_argument('--output', default='build/provider-diagnostics.json')
    parser.add_argument('--detail', default='original')
    parser.add_argument('--effort')
    parser.add_argument('--agent', action='store_true')
    parser.add_argument('--profile')
    args = parser.parse_args()
    profiles = ZooStore(args.root).load()['profiles']
    tasks = [(p, vision) for p in profiles if p['enabled'] and (not args.profile or p['name'] == args.profile)
             for vision in ((True,) if args.agent else (False, True))
             if (p['vision_model'] if vision else p['model'])]
    rows = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(check, p, vision, args.detail, args.effort, args.agent) for p, vision in tasks]
        for future in futures:
            row = future.result()
            rows.append(row)
            print(json.dumps(row, ensure_ascii=True), flush=True)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
