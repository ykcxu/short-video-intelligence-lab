# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from shutil import which

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs, collect_submodules


ROOT = Path.cwd()


def _ffmpeg_binary(name):
    """构建时动态查找 ffmpeg/ffprobe，避免把本机绝对路径写死。"""
    found = which(name)
    if found:
        return (found, ".")
    raise FileNotFoundError(f"打包 GUI 需要先安装 {name} 并加入 PATH")


def _tree_datas(source, target):
    """把离线模型目录整体加入 exe，保证目标机器不再联网下载模型。"""
    root = ROOT / source
    if not root.exists():
        raise FileNotFoundError(f"缺少打包资源：{root}")
    return [(str(path), str(Path(target) / path.relative_to(root).parent)) for path in root.rglob("*") if path.is_file()]


def _collect_package(name):
    """收集重模型库的数据、动态库和隐藏导入。"""
    datas, binaries, hiddenimports = collect_all(name)
    return list(datas), list(binaries), list(hiddenimports)


datas = [('artifacts\\analysis\\video_effect_model.json', 'artifacts\\analysis')]
binaries = [_ffmpeg_binary('ffmpeg'), _ffmpeg_binary('ffprobe')]
hiddenimports = []

for package_name in ('faster_whisper', 'ctranslate2', 'easyocr', 'cv2', 'mediapipe'):
    package_datas, package_binaries, package_hiddenimports = _collect_package(package_name)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

datas += collect_data_files('torch')
binaries += collect_dynamic_libs('torch')
hiddenimports += collect_submodules('torch')
datas += _tree_datas('artifacts/models/faster-whisper-tiny', 'artifacts/models/faster-whisper-tiny')
datas += _tree_datas('artifacts/models/easyocr/model', 'artifacts/models/easyocr/model')
datas += [('artifacts\\models\\pose_landmarker_lite.task', 'artifacts\\models')]


a = Analysis(
    ['tools\\video_effect_gui.py'],
    pathex=['src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
