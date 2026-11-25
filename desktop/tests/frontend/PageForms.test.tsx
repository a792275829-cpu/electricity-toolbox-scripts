import { describe, it, expect, vi } from "vitest";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { PageForms } from "../../src/toolbox/PageForms";
import { WpsEditor } from "../../src/toolbox/WpsEditor";
import type { Tool } from "../../src/toolbox/types";
vi.mock("../../src/api", () => ({ call: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/plugin-dialog", () => ({
  message: vi.fn(),
  ask: vi.fn(),
  open: vi.fn(),
  save: vi.fn(),
}));
const root = fileURLToPath(new URL("../../../", import.meta.url));
const tools: Tool[] = JSON.parse(
  execFileSync(
    join(root, ".venv/bin/python"),
    [
      "-c",
      `import sys,json;from pathlib import Path;sys.path[:0]=[str(Path.cwd()),str(Path.cwd()/'电力工具箱')];from toolbox.runtime import ToolPaths;from toolbox.service_catalog import desktop_catalog;print(json.dumps(desktop_catalog(ToolPaths(Path.cwd()))))`,
    ],
    { cwd: root, encoding: "utf8" },
  ),
);
const noop = () => {};
function render(tool: Tool) {
  const values = Object.fromEntries(
    tool.fields.map((field) => [field.key, field.default]),
  );
  return tool.id === "wps-writer"
    ? renderToStaticMarkup(
        <WpsEditor
          config={{
            configs: [],
            browser_mode: "managed_cdp",
            cdp_url: "http://127.0.0.1:9222",
          }}
          onConfig={async () => {}}
          execute={async () => ({})}
        />,
      )
    : renderToStaticMarkup(
        <PageForms
          tool={tool}
          values={values}
          setValues={noop}
          busy={false}
          run={noop}
          openOutput={noop}
          autoMatch={noop}
          manual={noop}
          editor={noop}
          reportDate={noop}
        />,
      );
}
describe("all original business routes are reachable in React", () => {
  it("uses the live 16-tool catalog", () => expect(tools).toHaveLength(16));
  it("does not show an overwrite checkbox in the WPS editor", () => {
    const tool = tools.find((tool) => tool.id === "wps-writer")!;
    expect(render(tool)).not.toContain("覆盖非空单元格");
  });
  for (const tool of tools) {
    it(`${tool.id}: exposes business operations and the appropriate login entrance`, () => {
      const html = render(tool);
      for (const action of tool.actions) {
        // Qingneng login is offered in the shared account panel when needed.
        if (
          action.id === "login" &&
          !["wps-writer", "section-analysis"].includes(tool.id)
        ) {
          expect(html, tool.id).not.toContain('data-action="login"');
          continue;
        }
        // Online inspection is inside the WPS document editor, not its main toolbar.
        if (tool.id === "wps-writer" && action.id === "inspect") continue;
        expect(html, `${tool.id}/${action.id}`).toContain(
          `data-action="${action.id}"`,
        );
      }
    });
    if (tool.id !== "wps-writer")
      it(`${tool.id}: preserves all editable parameters`, () => {
        const html = render(tool);
        for (const field of tool.fields) {
          // Files are supplied by the native picker; market JSON by its dedicated editor.
          if (field.kind === "files" || field.key === "manual_market") continue;
          expect(html, `${tool.id}/${field.key}`).toContain(
            `data-field="${field.key}"`,
          );
        }
      });
  }
});
