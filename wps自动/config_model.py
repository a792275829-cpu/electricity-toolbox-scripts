"""WPS writer: conversion between exported config dicts and runtime objects."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from toolbox_shared.wps_service import DocumentConfig, MappingRow, RegionRow
from toolbox_shared.wps_values import range_shape, split_range, target_range_from_start


def document_config_from_config(row: dict[str, Any]) -> DocumentConfig:
    regions = []
    for region in row.get("regions", []):
        if not isinstance(region, dict):
            continue
        regions.append(
            RegionRow(
                source_sheet=region.get("source_sheet", ""),
                source_start=region.get("source_start", ""),
                source_end=region.get("source_end", ""),
                target_sheet=region.get("target_sheet", ""),
                target_start=region.get("target_start", ""),
                target_end=region.get("target_end", ""),
            )
        )
    return DocumentConfig(
        name=row.get("name", "") or Path(row.get("local_file", "")).stem or "未命名配置",
        kdocs_url=row.get("kdocs_url", ""),
        local_file=row.get("local_file", ""),
        regions=regions,
        source_type=row.get("source_type", "excel"),
        source_url=row.get("source_url", ""),
    )

def mapping_from_config(row: dict[str, Any], default_url: str = "", default_file: str = "") -> MappingRow:
    if "source_range" in row and ("source_start" not in row or "source_end" not in row):
        source_start, source_end = split_range(row.get("source_range", ""))
    else:
        source_start = row.get("source_start", "")
        source_end = row.get("source_end", "")

    target_start = row.get("target_start", "")
    target_end = row.get("target_end", "")
    if not target_end and target_start and "source_range" in row:
        source_start_for_shape, source_end_for_shape = split_range(row.get("source_range", ""))
        rows, cols = range_shape(source_start_for_shape, source_end_for_shape)
        target_range = target_range_from_start(target_start, rows, cols)
        _target_start, target_end = split_range(target_range)

    return MappingRow(
        kdocs_url=row.get("kdocs_url", "") or default_url,
        local_file=row.get("local_file", "") or default_file,
        source_sheet=row.get("source_sheet", ""),
        source_start=source_start,
        source_end=source_end,
        target_sheet=row.get("target_sheet", ""),
        target_start=target_start,
        target_end=target_end,
        source_type=row.get("source_type", "excel"),
        source_url=row.get("source_url", ""),
    )

def group_mappings_into_configs(mappings: list[MappingRow]) -> list[DocumentConfig]:
    grouped: dict[tuple[str, str], list[RegionRow]] = {}
    for mapping in mappings:
        key = (mapping.kdocs_url, mapping.local_file)
        grouped.setdefault(key, []).append(
            RegionRow(
                source_sheet=mapping.source_sheet,
                source_start=mapping.source_start,
                source_end=mapping.source_end,
                target_sheet=mapping.target_sheet,
                target_start=mapping.target_start,
                target_end=mapping.target_end,
            )
        )
    configs = []
    for index, ((kdocs_url, local_file), regions) in enumerate(grouped.items(), start=1):
        name = Path(local_file).stem or f"配置 {index}"
        configs.append(DocumentConfig(name=name, kdocs_url=kdocs_url, local_file=local_file, regions=regions))
    return configs

def flatten_configs(configs: list[DocumentConfig] | None = None) -> list[MappingRow]:
    mappings: list[MappingRow] = []
    for config in (configs if configs is not None else configs):
        for region in config.regions:
            mappings.append(
                MappingRow(
                    kdocs_url=config.kdocs_url,
                    local_file=config.local_file,
                    source_sheet=region.source_sheet,
                    source_start=region.source_start,
                    source_end=region.source_end,
                    target_sheet=region.target_sheet,
                    target_start=region.target_start,
                    target_end=region.target_end,
                    source_type=config.source_type,
                    source_url=config.source_url,
                )
            )
    return mappings
