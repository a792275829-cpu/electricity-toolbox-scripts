"""Real DMG creation must not corrupt the service's JSON-lines pipe."""
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


@unittest.skipUnless(sys.platform == 'darwin', 'macOS hdiutil required')
class InstallerImageTransportTests(unittest.TestCase):
    def test_real_build_preserves_protocol_and_image_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / '电力工具箱.app'
            (app / 'Contents').mkdir(parents=True)
            (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(dict(
                CFBundleIdentifier='com.electricity.toolbox',
                CFBundleShortVersionString='1.2.3')))
            installer = root / 'installer.py'
            installer.write_text('# isolated installer fixture\n')
            # Match service_bridge: Python logs use stderr, while fd 1 is
            # reserved for protocol replies and still inherited by subprocesses.
            script = '''import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from installer_image import build
transport = sys.stdout
sys.stdout = sys.stderr
print(json.dumps({'id': 1, 'result': 'running'}), file=transport, flush=True)
image = build(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
print(json.dumps({'id': 2, 'result': str(image)}), file=transport, flush=True)
'''
            result = subprocess.run([sys.executable, '-c', script,
                str(ROOT / 'desktop/python'), str(app), str(root / 'output'), str(installer)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual([row['id'] for row in rows], [1, 2])
            image = Path(rows[1]['result'])
            self.assertTrue(image.is_file())
            checksum = hashlib.sha256(image.read_bytes()).hexdigest()
            self.assertEqual(image.with_suffix('.dmg.sha256').read_text(),
                             f'{checksum}  {image.name}\n')
            verified = subprocess.run(['/usr/bin/hdiutil', 'verify', str(image)],
                                      capture_output=True, timeout=60)
            self.assertEqual(verified.returncode, 0, verified.stderr)
