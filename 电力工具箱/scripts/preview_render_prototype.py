"""Preview the scoped rendering experiment with the normal toolbox logic."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from toolbox.app import ToolboxApp


if __name__ == "__main__":
    app = ToolboxApp(render_prototype=True)
    app.show_page("出清结果汇总上传")
    app.mainloop()
