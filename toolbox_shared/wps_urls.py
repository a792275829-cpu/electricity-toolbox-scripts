"""Thin binding for the shared Rust WPS URL-label operation."""
from __future__ import annotations


def url_choices(urls: list[str], titles: dict[str, str], selected: str = "") -> dict:
    from .native_excel import call, native_binary

    binary = native_binary()
    if binary is None:
        raise RuntimeError("缺少 Rust 核心，请安装完整版本或构建 native/price-xlsx。")
    return call(binary, "wps_url_choices", "", payload={
        "urls": urls, "titles": titles, "selected": selected,
    })
