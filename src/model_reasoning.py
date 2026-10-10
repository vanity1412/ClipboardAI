"""Explicit effort settings shared by API Zoo, quick picker and requests."""
LEVELS = ('default', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')


def effort_for(profile, model):
    return profile.get('model_efforts', {}).get(model, profile.get('reasoning_effort', 'medium'))


def choices(profile, model):
    metadata = next((m for m in profile.get('models', []) if m['id'] == model), {})
    if 'reasoning_efforts' in metadata:
        return ('default',) + tuple(metadata['reasoning_efforts'])
    if profile.get('provider') == 'deepseek':
        return ('default', 'high', 'max')
    if 'claude' in model.lower() or profile.get('provider') == 'anthropic':
        return ('default', 'low', 'medium', 'high')
    return ('default', 'low', 'medium', 'high', 'xhigh', 'max')


def apply_effort(body, protocol, model, effort):
    if effort == 'default':
        return body
    if effort not in LEVELS:
        raise ValueError('Reasoning effort không hợp lệ')
    if protocol == 'anthropic':
        if effort not in ('low', 'medium', 'high', 'max'):
            raise ValueError('Claude Messages không hỗ trợ mức reasoning này')
        body['output_config'] = {'effort': effort}
    elif protocol == 'responses':
        body['reasoning'] = {'effort': effort}
    else:
        body['reasoning_effort'] = effort
    return body


def select_model(data, profile_id, model, effort, vision=False):
    import copy
    from api_zoo import validate
    data = copy.deepcopy(data)
    profile = next((p for p in data['profiles'] if p['id'] == profile_id and p['enabled']), None)
    if profile is None or effort not in choices(profile, model):
        raise ValueError('API/model hoặc reasoning không hợp lệ')
    catalog = {m['id']: m for m in profile.get('models', [])}
    existing = profile.get('vision_model' if vision else 'model')
    if model not in catalog and model != existing:
        raise ValueError('Model không có trong danh sách API')
    if vision and catalog.get(model, {}).get('vision') is False:
        raise ValueError('Model này không hỗ trợ ảnh')
    profile['vision_model' if vision else 'model'] = model
    profile.setdefault('model_efforts', {})[model] = effort
    data['primary'] = profile_id
    return validate(data)
