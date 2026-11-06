"""Local, content-addressed basemaps for the annotation workflow.

The supplied PDF is a single full-page raster. Extract its original image rather
than rasterizing a preview; vector/multipage PDFs need explicit preparation.
"""
from __future__ import annotations

from base64 import b64decode, b64encode
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import warnings

from PIL import Image
from toolbox_shared import section_topology as model


def asset_path(identity: str) -> Path:
    if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{64}', identity):
        raise ValueError('底图标识无效')
    return model.catalog_path().parent / 'basemaps' / (identity + '.img')


def store_image(raw: bytes, name: str) -> dict:
    if len(raw) > 100 * 1024 * 1024:
        raise ValueError('底图超过100MiB')
    if Path(name).suffix.lower() == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(raw))
        if len(reader.pages) != 1 or len(reader.pages[0].images) != 1:
            raise ValueError('请选择单页、整页为一张图片的PDF，或先导出为PNG/JPG底图')
        page = reader.pages[0]
        if page.get('/Rotate', 0) % 360:
            raise ValueError('请先将旋转PDF导出为正向PNG/JPG底图')
        embedded = page.images[0]
        image = embedded.image
        raw = embedded.data
        ratio = float(page.mediabox.width / page.mediabox.height)
        if abs(image.width/image.height/ratio - 1) > .02:
            raise ValueError('PDF图片与页面比例不一致，请先导出为PNG/JPG底图')
    else:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            image = Image.open(BytesIO(raw))
            image.load()
    if image.width * image.height > 80_000_000 or min(image.size) < 10:
        raise ValueError('底图尺寸无效或超过8000万像素')
    # Keep the original JPEG/PNG bytes: no resize, recompression or large PNG expansion.
    if image.format in ('JPEG', 'PNG'):
        content = raw
    else:
        stream = BytesIO()
        image.convert('RGB').save(stream, format='PNG')
        content = stream.getvalue()
    identity = sha256(content).hexdigest()
    path = asset_path(identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        from tempfile import NamedTemporaryFile
        import os
        temporary = None
        try:
            with NamedTemporaryFile(dir=path.parent, delete=False) as output:
                temporary = Path(output.name)
                output.write(content)
            os.replace(temporary, path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
    return dict(id=identity, name=Path(name).name, width=image.width, height=image.height)


def media_type(identity: str) -> str:
    with Image.open(asset_path(identity)) as image:
        return Image.MIME[image.format]


def default_map() -> dict | None:
    path = model.catalog_path().with_name('basemap-default.json')
    if not path.exists():
        return None
    metadata = json.loads(path.read_text())
    return metadata if asset_path(metadata['id']).exists() else None


def install_default(source: Path) -> dict:
    metadata = store_image(source.read_bytes(), source.name)
    path = model.catalog_path().with_name('basemap-default.json')
    path.write_text(json.dumps(metadata, ensure_ascii=False), encoding='utf-8')
    return metadata


def export_backup(data: dict) -> dict:
    from copy import deepcopy
    result = deepcopy(data)
    if data.get('base_map'):
        raw = asset_path(data['base_map']['id']).read_bytes()
        result['base_map_image'] = b64encode(raw).decode('ascii')
    return result


def import_backup(data: dict) -> dict:
    from copy import deepcopy
    result = deepcopy(data)
    encoded = result.pop('base_map_image', None)
    model.validate_catalog(result)
    if encoded:
        raw = b64decode(encoded, validate=True)
        if not result.get('base_map') or sha256(raw).hexdigest() != result['base_map']['id']:
            raise ValueError('备份底图校验失败')
        metadata = store_image(raw, 'backup.png')
        if any(metadata[k] != result['base_map'][k] for k in ('id', 'width', 'height')):
            raise ValueError('备份底图尺寸无效')
    return result
