from __future__ import annotations

import json
import mimetypes
import os
import shutil
import tempfile
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = Path(__file__).resolve().parent / "anchor_calibrator"
CANDIDATES_PATH = ROOT / "data" / "500kv_node_position_candidates.json"
POSITIONS_PATH = ROOT / "data" / "500kv_node_positions.json"
IMAGE_PATH = ROOT / "接线图.png"
DATA_LOCK = Lock()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def initialize_positions(candidates: dict) -> dict:
    return {
        "version": 1,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "image": candidates["image"],
        "topology": candidates["topology"],
        "nodes": [
            {
                "name": node["name"],
                "anchor": node.get("suggested_anchor"),
                "status": "candidate" if node.get("suggested_anchor") else "unresolved",
                "source": "opencv-candidate" if node.get("suggested_anchor") else None,
                "updated_at": None,
            }
            for node in candidates["nodes"]
        ],
    }


def ensure_positions(candidates: dict) -> dict:
    if POSITIONS_PATH.exists():
        positions = load_json(POSITIONS_PATH)
        existing = {node["name"]: node for node in positions.get("nodes", [])}
        candidate_names = {node["name"] for node in candidates["nodes"]}
        retained_nodes = [node for node in positions.get("nodes", []) if node["name"] in candidate_names]
        changed = len(retained_nodes) != len(positions.get("nodes", []))
        positions["nodes"] = retained_nodes
        for candidate in candidates["nodes"]:
            if candidate["name"] not in existing:
                positions.setdefault("nodes", []).append(
                    {
                        "name": candidate["name"],
                        "anchor": candidate.get("suggested_anchor"),
                        "status": "candidate" if candidate.get("suggested_anchor") else "unresolved",
                        "source": "opencv-candidate" if candidate.get("suggested_anchor") else None,
                        "updated_at": None,
                    }
                )
                changed = True
        if changed:
            atomic_write(POSITIONS_PATH, positions)
        return positions
    positions = initialize_positions(candidates)
    atomic_write(POSITIONS_PATH, positions)
    return positions


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 32_768:
            raise ValueError("请求过大")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/data":
            with DATA_LOCK:
                candidates = load_json(CANDIDATES_PATH)
                positions = ensure_positions(candidates)
            self._json({"candidates": candidates, "positions": positions})
            return
        if path == "/image.png":
            self._serve_file(IMAGE_PATH)
            return
        if path == "/":
            self._serve_file(WEB_ROOT / "index.html")
            return
        target = WEB_ROOT / path.lstrip("/")
        if target.is_file() and target.resolve().is_relative_to(WEB_ROOT.resolve()):
            self._serve_file(target)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _serve_file(self, path: Path) -> None:
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/nodes/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        name = unquote(path.removeprefix("/api/nodes/"))
        try:
            patch = self._body()
            if set(patch) - {"anchor", "status", "source"}:
                raise ValueError("包含不支持的字段")
            status = str(patch.get("status", ""))
            if status not in {"verified", "candidate", "unresolved"}:
                raise ValueError("状态无效")
            anchor = patch.get("anchor")
            if anchor is not None:
                if not isinstance(anchor, dict) or not {"x", "y"} <= set(anchor):
                    raise ValueError("锚点格式无效")
                x, y = round(float(anchor["x"]), 2), round(float(anchor["y"]), 2)
                if not (0 <= x <= 7111 and 0 <= y <= 5025):
                    raise ValueError("锚点超出接线图范围")
                patch["anchor"] = {"x": x, "y": y}
            with DATA_LOCK:
                candidates = load_json(CANDIDATES_PATH)
                positions = ensure_positions(candidates)
                node = next((item for item in positions["nodes"] if item["name"] == name), None)
                if node is None:
                    raise ValueError("节点不存在")
                node.update(patch)
                node["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                positions["updated_at"] = node["updated_at"]
                if not POSITIONS_PATH.with_suffix(".backup.json").exists():
                    shutil.copy2(POSITIONS_PATH, POSITIONS_PATH.with_suffix(".backup.json"))
                atomic_write(POSITIONS_PATH, positions)
            self._json(node)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")


def main() -> None:
    if not CANDIDATES_PATH.exists():
        raise FileNotFoundError(f"请先生成候选数据: {CANDIDATES_PATH}")
    host = os.environ.get("ANCHOR_CALIBRATOR_HOST", "127.0.0.1")
    port = int(os.environ.get("ANCHOR_CALIBRATOR_PORT", "8766"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"500kV节点锚点校准器: http://{host}:{port}")
    print(f"保存位置: {POSITIONS_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
