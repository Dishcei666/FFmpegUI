# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

# Conda 把 Qt/Shiboken 等 DLL 放在 Library/bin；未激活的 shell 中 PATH
# 不含此目录，且部分 Qt 构建的 BinariesPath 指向空目录。让依赖分析收集
# 正确的 DLL 闭包，避免产物只能在已激活 Conda 的机器上启动。
conda_bin = Path(sys.prefix) / 'Library' / 'bin'
if os.name == 'nt' and (Path(sys.prefix) / 'conda-meta').is_dir() and conda_bin.is_dir():
    os.environ['PATH'] = str(conda_bin) + os.pathsep + os.environ.get('PATH', '')


a = Analysis(
    ['launch.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FFmpegUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# CLI 使用独立脚本与控制台 bootloader；两份依赖分析的 DLL/数据统一收集，
# 保持同目录共享运行时，且每个 exe 只执行自己的入口。
cli_analysis = Analysis(
    ['launch_cli.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
cli_pyz = PYZ(cli_analysis.pure)
cli_exe = EXE(
    cli_pyz,
    cli_analysis.scripts,
    [],
    exclude_binaries=True,
    name='FFmpegUI-cli',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    cli_exe,
    a.binaries,
    a.datas,
    cli_analysis.binaries,
    cli_analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FFmpegUI',
)
