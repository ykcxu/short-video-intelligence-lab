# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from shutil import which


def _ffmpeg_binary(name):
    """构建时动态查找 ffmpeg/ffprobe，避免把本机绝对路径写死。"""
    found = which(name)
    if found:
        return (found, ".")
    raise FileNotFoundError(f"打包 GUI 需要先安装 {name} 并加入 PATH")


a = Analysis(
    ['tools\\video_effect_gui.py'],
    pathex=['src'],
    binaries=[_ffmpeg_binary('ffmpeg'), _ffmpeg_binary('ffprobe')],
    datas=[('artifacts\\analysis\\video_effect_model.json', 'artifacts\\analysis')],
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
    a.binaries,
    a.datas,
    [],
    name='video_effect_gui',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
