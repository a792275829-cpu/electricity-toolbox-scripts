from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolAdapter:
    tool_id: str
    destructive: bool = False
    retryable_read: bool = False

    def confirmation_required(self) -> bool:
        return self.destructive


def default_adapters() -> dict[str, ToolAdapter]:
    rows = (
        ToolAdapter("online-energy", retryable_read=True),
        ToolAdapter("market-table", destructive=True),
        ToolAdapter("guangdong-price", retryable_read=True),
        ToolAdapter("trade-analysis"),
        ToolAdapter("section-analysis"),
        ToolAdapter("summary"),
        ToolAdapter("report"),
        ToolAdapter("private-upload", destructive=True),
        ToolAdapter("group-upload", destructive=True),
        ToolAdapter("wps-writer", destructive=True),
    )
    return {item.tool_id: item for item in rows}


__all__ = ["ToolAdapter", "default_adapters"]
