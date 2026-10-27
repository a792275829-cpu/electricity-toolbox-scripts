"""Batch orchestration shared by independent GUI, toolbox, service and CLI."""
from pathlib import Path
import unicodedata

from toolbox_native_excel import excel_session


def plan_batch_outputs(xlsx_paths, output_dir):
    """Validate the complete batch before creating any output."""
    output_dir = Path(output_dir)
    targets, planned = {}, []
    for source in xlsx_paths:
        source = Path(source)
        output = output_dir / f"{source.stem}_分析报告.html"
        key = unicodedata.normalize("NFC", output.name).casefold()
        previous = targets.get(key)
        if previous is not None and previous.resolve() != source.resolve():
            raise ValueError(f"分析报告输出文件名冲突：{previous} 与 {source} 都将写入 {output.name}。请更改输入文件名或分批选择不同输出目录。")
        targets[key] = source
        planned.append((source, output))
    return planned


def iter_batch_reports(xlsx_paths, output_dir, render, *, continue_on_error=False):
    """Yield (source, output, result, error); callers only handle presentation.

    Fail-fast is the CLI/service contract. The independent GUI explicitly opts
    into partial completion. GeneratorExit/cancellation is never swallowed.
    """
    planned = plan_batch_outputs(xlsx_paths, output_dir)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    repeated = len({source.resolve() for source, _ in planned}) < len(planned)
    with excel_session(cache=repeated, enabled=repeated):
        for source, output in planned:
            try:
                result = render(source, out_path=output)
            except Exception as exc:
                if not continue_on_error:
                    raise
                yield source, output, None, exc
            else:
                yield source, output, result, None
