"""Offline browser smoke checks using only a staged application's runtime."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def validate(payload: Path, *, headed: bool = False) -> dict:
    payload = payload.resolve()
    sys.path.insert(0, str(payload / "app"))
    from toolbox_shared.browser_runtime import chromium_launch_options
    from playwright.sync_api import sync_playwright

    env = {**os.environ, "TOOLBOX_PORTABLE": "1",
           "PLAYWRIGHT_BROWSERS_PATH": str(payload / "runtime/ms-playwright")}
    os.environ.update({key: env[key] for key in ("TOOLBOX_PORTABLE", "PLAYWRIGHT_BROWSERS_PATH")})
    import numpy as np
    import pandas as pd
    from scipy.sparse import csr_matrix
    from sklearn.linear_model import Ridge
    values = np.array([[0.0], [1.0], [2.0], [3.0]])
    prediction = Ridge(alpha=0).fit(csr_matrix(values), values[:, 0]).predict([[4.0]])
    if not np.allclose(prediction, [4.0]) or pd.DataFrame(values).sum().iloc[0] != 6:
        raise RuntimeError("裁剪后科学计算校验失败")
    with tempfile.TemporaryDirectory(prefix="toolbox-runtime-check-") as directory:
        temporary = Path(directory)
        with sync_playwright() as sdk:
            for headless in ([True, False] if headed else [True]):
                # Distinct disposable profiles; never load a user's login state.
                context = sdk.chromium.launch_persistent_context(
                    str(temporary / f"python-{headless}"), headless=headless,
                    **chromium_launch_options())
                try:
                    page = context.new_page()
                    page.set_content('<meta charset="utf-8"><h1>电力工具箱 offline</h1>')
                    assert page.locator("h1").inner_text() == "电力工具箱 offline"
                    page.screenshot(path=str(temporary / f"python-{headless}.png"))
                    if headless:
                        pdf = page.pdf()
                        assert pdf.startswith(b"%PDF") and len(pdf) > 1000
                finally:
                    context.close()
        module = payload / "app/电力工具脚本/private-data-uploader-tool/node_modules/playwright/index.mjs"
        script = '''
const {chromium} = await import(process.argv[1]);
const executablePath = chromium.executablePath();
const context = await chromium.launchPersistentContext(process.argv[2], {headless:true, executablePath});
try {
  const page = await context.newPage();
  await page.setContent('<h1>offline uploader</h1>');
  if (await page.locator('h1').innerText() !== 'offline uploader') throw Error('DOM mismatch');
  await page.screenshot({path: process.argv[3]});
} finally { await context.close(); }
'''
        subprocess.run([str(payload / "runtime/node/bin/node"), "--input-type=module", "-e", script,
                        module.as_uri(), str(temporary / "node"), str(temporary / "node.png")],
                       env=env, check=True, timeout=90)
    return {"python_persistent": True, "headed": headed, "screenshot": True,
            "pdf": True, "node_persistent": True, "scientific_compute": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    print(json.dumps(validate(args.payload, headed=args.headed)))
