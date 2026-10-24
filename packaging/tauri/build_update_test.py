"""Build a local desktop updater test without changing the signed delta base.

The original application and runtime remain byte-identical inside the wrapper.
The existing TOOLBOX_WORKSPACE override loads a frozen export of the fixed code.
This is a local diagnostic application, never a production release artifact.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile

import build_support
from prepare_payload import ROOT, runtime_source

sys.path.insert(0, str(ROOT / 'desktop/python'))
from delta_update import app_version, inventory

NAME = '电力工具箱更新修复测试.app'

LAUNCHER = r'''
#import <Foundation/Foundation.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdlib.h>
int main(int argc, char **argv) { @autoreleasepool {
    NSString *resources = [[NSBundle mainBundle] resourcePath];
    NSString *workspace = [resources stringByAppendingPathComponent:@"workspace"];
    NSString *baseline = [resources stringByAppendingPathComponent:@"baseline/电力工具箱.app"];
    NSString *executable = [baseline stringByAppendingPathComponent:@"Contents/MacOS/electricity-toolbox"];
    NSString *home = [NSHomeDirectory() stringByAppendingPathComponent:
        @"Library/Application Support/com.electricity.toolbox/update-fix-desktop-test"];
    NSString *data = [home stringByAppendingPathComponent:@"data"];
    if (argc > 1 && strcmp(argv[1], "--self-test") == 0) {
        NSDictionary *info = @{@"workspace": workspace, @"baseline": baseline, @"data": data};
        NSData *json = [NSJSONSerialization dataWithJSONObject:info options:0 error:nil];
        fwrite([json bytes], 1, [json length], stdout); fputc('\n', stdout); return 0;
    }
    [[NSFileManager defaultManager] createDirectoryAtPath:data withIntermediateDirectories:YES
        attributes:nil error:nil];
    setenv("TOOLBOX_WORKSPACE", [workspace fileSystemRepresentation], 1);
    setenv("TOOLBOX_APP_HOME", [home fileSystemRepresentation], 1);
    setenv("TOOLBOX_DATA_ROOT", [data fileSystemRepresentation], 1);
    unsetenv("PYTHONHOME"); unsetenv("PYTHONPATH");
    NSString *log = [home stringByAppendingPathComponent:@"launcher.log"];
    int fd = open([log fileSystemRepresentation], O_WRONLY | O_CREAT | O_APPEND, 0600);
    if (fd >= 0) { dup2(fd, STDOUT_FILENO); dup2(fd, STDERR_FILENO); close(fd); }
    const char *binary = [executable fileSystemRepresentation];
    // The second invocation asks the existing native updater UI to open.
    if (fork() == 0) { sleep(4); execl(binary, binary, "--show-updater", NULL); _exit(127); }
    execl(binary, binary, NULL);
    perror("无法启动更新修复测试应用"); return 127;
} }
'''


def build(baseline: Path, patch: Path, output: Path) -> Path:
    baseline = baseline.resolve()
    version = app_version(baseline)
    with tarfile.open(patch, 'r:xz') as archive:
        with archive.extractfile('manifest.json') as stream:
            manifest = json.load(stream)
    if manifest['base'] != version or inventory(baseline) != manifest['before']:
        raise ValueError('测试基线必须与正式补丁的完整文件、权限和链接清单一致')
    target = output.resolve() / NAME
    if target.exists():
        raise FileExistsError(f'测试应用已存在，不会覆盖：{target}')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='toolbox-update-test-', dir=output) as directory:
        app = Path(directory) / NAME
        contents = app / 'Contents'
        resources = contents / 'Resources'
        resources.mkdir(parents=True)
        executable = contents / 'MacOS/toolbox-update-test'
        executable.parent.mkdir()
        embedded = resources / 'baseline/电力工具箱.app'
        embedded.parent.mkdir()
        subprocess.run(['/usr/bin/ditto', '--norsrc', '--noextattr', '--noqtn', '--noacl',
                        str(baseline), str(embedded)], check=True, capture_output=True)
        workspace = resources / 'workspace'
        workspace.mkdir()
        count = build_support.export_source(workspace, predicate=runtime_source)
        # Reuse the native helper from the existing verified packaging stage.
        helper = Path('500kV断面分析/bin/toolbox-price-xlsx')
        origin = ROOT / 'desktop/src-tauri/payload/app' / helper
        if not origin.is_file():
            origin = embedded / 'Contents/Resources/payload/app' / helper
        (workspace / helper).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, workspace / helper)
        fixed = Path('desktop/python/installer_image.py')
        if build_support.sha256(workspace / fixed) != build_support.sha256(ROOT / fixed):
            raise ValueError('导出的更新器与当前修复源码不一致')
        build_support.normalize_permissions(workspace)
        shutil.copy2(ROOT / 'desktop/src-tauri/icons/icon.icns', resources / 'icon.icns')
        (contents / 'Info.plist').write_bytes(plistlib.dumps(dict(
            CFBundleIdentifier='com.electricity.toolbox.updatefix-test',
            CFBundleName='电力工具箱更新修复测试', CFBundleDisplayName='电力工具箱更新修复测试',
            CFBundleExecutable=executable.name, CFBundlePackageType='APPL',
            CFBundleShortVersionString=version, CFBundleVersion=version + '.1',
            CFBundleIconFile='icon.icns', LSMinimumSystemVersion='14.0', LSUIElement=True)))
        subprocess.run(['/usr/bin/clang', '-x', 'objective-c', '-arch', 'arm64',
                        '-mmacosx-version-min=14.0', '-framework', 'Foundation',
                        '-o', str(executable), '-'], input=LAUNCHER, text=True,
                       check=True, capture_output=True)
        for path in (executable, app):
            subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(path)],
                           check=True, capture_output=True)
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)],
                       check=True, capture_output=True)
        if inventory(embedded) != manifest['before']:
            raise ValueError('打包后原版补丁基线发生变化')
        evidence = dict(baseline=version, update_target=manifest['target'], source_files=count,
            source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            fixed_updater_sha256=build_support.sha256(workspace / fixed),
            baseline_inventory_unchanged=True, signed=True, architecture='arm64',
            data_profile='com.electricity.toolbox/update-fix-desktop-test')
        (output / 'build-verification.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        app.rename(target)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--patch', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(build(args.baseline, args.patch, args.output))
