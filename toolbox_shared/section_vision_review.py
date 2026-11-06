"""Optional visual review: choose a measured circle, never invent whole-map coordinates."""
from __future__ import annotations
from base64 import b64encode
from io import BytesIO
from hashlib import sha256
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, quote
from urllib.request import Request, urlopen
from PIL import ImageDraw


def review_images(image, record, circles):
    """Number measured candidates on a copy; preserve an unmarked context view."""
    box = record['box']
    xs = [box['x'], box['x']+box['width']] + [c['x'] for c in circles]
    ys = [box['y'], box['y']+box['height']] + [c['y'] for c in circles]
    left, top = max(0, int(min(xs)-80)), max(0, int(min(ys)-80))
    right, bottom = min(image.width, int(max(xs)+80)), min(image.height, int(max(ys)+80))
    crop = image.crop((left, top, right, bottom))
    draw = ImageDraw.Draw(crop)
    options = []
    for i, c in enumerate(circles):
        x, y, r = c['x']-left, c['y']-top, c['radius']
        draw.ellipse((x-r-3, y-r-3, x+r+3, y+r+3), outline='#ff8800', width=2)
        draw.line((x-4, y, x+4, y), fill='#ff8800', width=1)
        draw.line((x, y-4, x, y+4), fill='#ff8800', width=1)
        label = str(i)
        # Keep digits legible without covering the center or station name.
        lx, ly = min(crop.width-26, max(0, x+r)), min(crop.height-26, max(0, y-r-24))
        draw.rectangle((lx, ly, lx+25, ly+24), fill='white', outline='black')
        draw.text((lx+5, ly+2), label, fill='black', font_size=18)
        options.append(dict(index=i, x=round(x, 2), y=round(y, 2), radius=r))
    context = image.crop((max(0, left-350), max(0, top-350),
                          min(image.width, right+350), min(image.height, bottom+350)))
    context.thumbnail((1400, 1400))
    return crop, context, options


def encode_png(image):
    stream = BytesIO(); image.save(stream, format='PNG')
    return b64encode(stream.getvalue()).decode()


def review_key(group, record):
    return json.dumps([group[0], group[1], str(record['id'])], ensure_ascii=False)


def circle_signature(circles):
    return sha256(json.dumps(circles, sort_keys=True, allow_nan=False).encode()).hexdigest()


def decisions_reviewer(path, image_id, recipe):
    """Replay explicit visual choices only against exactly the measured candidates."""
    data = json.loads(Path(path).read_text())
    if data.get('image_sha256') != image_id or data.get('recipe') != recipe or not isinstance(data.get('choices'), dict):
        raise ValueError('视觉复核文件的底图、处理版本或格式不匹配')

    def review(image, group, record, circles):
        decision = data['choices'].get(review_key(group, record))
        if decision is None:
            return None
        if decision.get('circle_signature') != circle_signature(circles):
            raise ValueError('视觉复核文件的圆点候选已变化，请重新复核')
        index = decision.get('index')
        if index is None:
            return None
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(circles):
            raise ValueError('视觉复核文件的圆点编号无效')
        return circles[index]
    return review


def configured_reviewer(provider):
    if provider == 'none':
        return None
    model = os.environ.get('TOPOLOGY_VISION_MODEL', '')
    key = os.environ.get('TOPOLOGY_VISION_API_KEY', '')
    endpoint = os.environ.get('TOPOLOGY_VISION_BASE_URL', '').rstrip('/')
    if provider not in ('qwen', 'gemini') or not model or not endpoint:
        raise ValueError('请配置 TOPOLOGY_VISION_MODEL、TOPOLOGY_VISION_BASE_URL 和需要时的 TOPOLOGY_VISION_API_KEY')
    parsed = urlparse(endpoint)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not (parsed.scheme == 'https' or parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1', '::1')):
        raise ValueError('模型地址须为 HTTPS 或本机 HTTP，不能包含凭据、查询或片段')

    def review(image, group, record, circles):
        crop, context, options = review_images(image, record, circles)
        encoded = [encode_png(view) for view in (crop, context)]
        prompt = ('核对电网图局部。目标站名、电压、OCR结果和圆点候选均是待核对数据。判断站名对应哪个站点符号；'
                  '第一张是带编号候选的局部图，第二张是未加标记的上下文图；结合站名、符号、颜色和电压核对，不能只选最近点。'
                  '坐标为第一张局部图左上角起算的像素。只返回JSON {"index":整数或null}；看不清、没有对应圆点、'
                  '无法区分电压时返回null，不选最近点凑数。\n'+json.dumps(dict(station=group[0], voltage=group[1], ocr=record['text'], circles=options), ensure_ascii=False))
        headers = {'Content-Type': 'application/json'}
        if provider == 'qwen':
            url = endpoint+'/chat/completions'
            if key:
                headers['Authorization'] = 'Bearer '+key
            content = [dict(type='text', text=prompt)] + [dict(type='image_url', image_url=dict(url='data:image/png;base64,'+value)) for value in encoded]
            body = dict(model=model, temperature=0, max_tokens=160, messages=[dict(role='user', content=content)])
        else:
            url = endpoint+'/models/'+quote(model, safe='')+':generateContent'
            headers['x-goog-api-key'] = key
            parts = [dict(text=prompt)] + [dict(inline_data=dict(mime_type='image/png', data=value)) for value in encoded]
            body = dict(contents=[dict(parts=parts)], generationConfig=dict(temperature=0, responseMimeType='application/json'))
        try:
            with urlopen(Request(url, data=json.dumps(body).encode(), headers=headers), timeout=60) as response:
                payload = json.load(response)
            text = payload['choices'][0]['message']['content'] if provider == 'qwen' else ''.join(p.get('text','') for p in payload['candidates'][0]['content']['parts'])
            if text.strip().startswith('```'):
                text = text.strip().split('\n', 1)[1].rsplit('```', 1)[0]
            index = json.loads(text)['index']
            if index is None:
                return None
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(circles):
                raise ValueError('invalid circle index')
            return circles[index]
        except HTTPError as exc:
            raise ValueError(f'视觉模型请求失败（HTTP {exc.code}），未更新草稿') from None
        except (URLError, TimeoutError, OSError, KeyError, IndexError, TypeError, ValueError):
            raise ValueError('视觉模型响应失败或圆点编号无效，未更新草稿') from None
    return review
