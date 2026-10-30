"""Аудит изображений и текстов canonical-архива Valentino."""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections import Counter, defaultdict
from io import BytesIO
from pathlib import Path
from statistics import median, pstdev

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps


IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp'}


def _light_background_metrics(image):
    analysis = ImageOps.exif_transpose(image).convert('RGB')
    analysis.thumbnail((320, 320), Image.Resampling.LANCZOS)
    width, height = analysis.size
    step = max(1, min(width, height) // 80)
    edges = []
    for x in range(0, width, step):
        edges.extend((analysis.getpixel((x, 0)), analysis.getpixel((x, height - 1))))
    for y in range(0, height, step):
        edges.extend((analysis.getpixel((0, y)), analysis.getpixel((width - 1, y))))
    background = tuple(int(median(pixel[channel] for pixel in edges)) for channel in range(3))
    spread = max(pstdev(pixel[channel] for pixel in edges) for channel in range(3))
    light_flat = min(background) >= 205 and spread < 7
    result = {
        'edge_background': list(background),
        'edge_spread': round(spread, 2),
        'flat_light_background': light_flat,
        'subject_bbox_ratio': None,
        'subject_width_ratio': None,
        'subject_height_ratio': None,
        'minimum_margin_ratio': None,
    }
    if not light_flat:
        return result
    difference = ImageChops.difference(
        analysis, Image.new('RGB', analysis.size, background),
    ).convert('L')
    mask = difference.point(lambda value: 255 if value > 12 else 0)
    mask = mask.filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.MaxFilter(5))
    box = mask.getbbox()
    if not box:
        return result
    box_width = box[2] - box[0]
    box_height = box[3] - box[1]
    margins = (box[0], box[1], width - box[2], height - box[3])
    result.update({
        'subject_bbox_ratio': round(box_width * box_height / (width * height), 4),
        'subject_width_ratio': round(box_width / width, 4),
        'subject_height_ratio': round(box_height / height, 4),
        'minimum_margin_ratio': round(min(margins) / min(width, height), 4),
    })
    return result


def _contact_sheet(samples, destination, title):
    if not samples:
        return
    cell_width, cell_height = 280, 350
    columns = 4
    rows = (len(samples) + columns - 1) // columns
    canvas = Image.new('RGB', (columns * cell_width, rows * cell_height), 'white')
    draw = ImageDraw.Draw(canvas)
    for offset, sample in enumerate(samples):
        image = sample['image'].copy().convert('RGB')
        image.thumbnail((250, 280), Image.Resampling.LANCZOS)
        x = (offset % columns) * cell_width + (cell_width - image.width) // 2
        y = (offset // columns) * cell_height + 10
        canvas.paste(image, (x, y))
        label = f"{sample['sku'].split(':')[-1]} · #{sample['index'] + 1}\n{image.width}×{image.height}"
        draw.multiline_text(
            ((offset % columns) * cell_width + 8, (offset // columns) * cell_height + 296),
            label[:70], fill='black', spacing=3,
        )
    draw.text((8, canvas.height - 16), title, fill='#555555')
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, format='JPEG', quality=88)


def audit_valentino_archive(archive_path, report_directory):
    archive_path = Path(archive_path).resolve()
    report_directory = Path(report_directory).resolve()
    report_directory.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        json_entries = [item for item in archive.infolist() if item.filename.lower().endswith('.json')]
        if len(json_entries) != 1:
            raise ValueError(f'ожидался один JSON, найдено {len(json_entries)}')
        cards = json.loads(archive.read(json_entries[0]).decode('utf-8-sig'))
        if not isinstance(cards, list):
            raise ValueError('в catalog.json ожидается массив карточек')
        image_infos = {
            item.filename: item for item in archive.infolist()
            if Path(item.filename).suffix.lower() in IMAGE_SUFFIXES
        }
        references = {}
        for card in cards:
            for index, image in enumerate((card.get('media') or {}).get('images') or []):
                references[image.get('file')] = (str(card.get('id') or ''), index)

        dimensions = Counter()
        orientations = Counter()
        per_index = defaultdict(Counter)
        exact_hashes = defaultdict(list)
        rows = []
        contact_candidates = defaultdict(list)
        invalid = []
        for number, (filename, info) in enumerate(image_infos.items(), start=1):
            sku, index = references.get(filename, ('', -1))
            try:
                content = archive.read(info)
                digest = hashlib.sha256(content).hexdigest()
                with Image.open(BytesIO(content)) as opened:
                    opened.verify()
                with Image.open(BytesIO(content)) as opened:
                    image = ImageOps.exif_transpose(opened).convert('RGB')
                    width, height = image.size
                    metrics = _light_background_metrics(image)
                    sample_image = image.copy()
            except (OSError, ValueError) as exc:
                invalid.append({'file': filename, 'error': str(exc)[:500]})
                continue
            orientation = 'portrait' if height > width else 'landscape' if width > height else 'square'
            dimensions[f'{width}x{height}'] += 1
            orientations[orientation] += 1
            per_index[index][f'{width}x{height}'] += 1
            exact_hashes[digest].append(filename)
            row = {
                'file': filename,
                'sku': sku,
                'index': index,
                'width': width,
                'height': height,
                'orientation': orientation,
                'bytes': info.file_size,
                'sha256': digest,
                **metrics,
            }
            rows.append(row)
            if len(contact_candidates[index]) < 12:
                contact_candidates[index].append({
                    'sku': sku, 'index': index, 'image': sample_image,
                })

        duplicate_groups = [files for files in exact_hashes.values() if len(files) > 1]
        low_resolution = [row for row in rows if min(row['width'], row['height']) < 600]
        tiny = [row for row in rows if min(row['width'], row['height']) < 300]
        light_background = [row for row in rows if row['flat_light_background']]
        tight = [
            row for row in light_background
            if row['minimum_margin_ratio'] is not None and row['minimum_margin_ratio'] < .015
        ]
        excessive_whitespace = [
            row for row in light_background
            if row['subject_bbox_ratio'] is not None and row['subject_bbox_ratio'] < .08
        ]
        summary = {
            'archive': str(archive_path),
            'products': len(cards),
            'images': len(rows),
            'invalid_images': len(invalid),
            'dimensions': dict(dimensions.most_common()),
            'orientations': dict(orientations),
            'dimensions_by_index': {
                str(index): dict(counts.most_common()) for index, counts in sorted(per_index.items())
            },
            'low_resolution_below_600': len(low_resolution),
            'tiny_below_300': len(tiny),
            'flat_light_background': len(light_background),
            'tight_light_background_frames': len(tight),
            'excessive_whitespace_frames': len(excessive_whitespace),
            'exact_duplicate_groups': len(duplicate_groups),
            'exact_duplicate_files': sum(len(group) for group in duplicate_groups),
        }
        details = {
            'summary': summary,
            'invalid': invalid,
            'low_resolution': low_resolution,
            'tight_light_background_frames': tight,
            'excessive_whitespace_frames': excessive_whitespace,
            'exact_duplicate_groups': duplicate_groups,
            'images': rows,
        }
        (report_directory / 'image-audit.json').write_text(
            json.dumps(details, ensure_ascii=False, indent=2), encoding='utf-8',
        )
        for index, samples in sorted(contact_candidates.items()):
            _contact_sheet(
                samples,
                report_directory / f'contact-sheet-index-{index + 1}.jpg',
                f'Valentino: исходный кадр #{index + 1}',
            )
        return summary
