"""Bounded local environment reports and a user-exported support archive."""
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import platform
import sys
import tempfile
import zipfile

from PySide6.QtCore import qVersion
from PySide6.QtGui import QGuiApplication

from . import __version__

LOG = logging.getLogger(__name__)


def _hardware():
    result = dict(cpu=platform.processor(), logical_cpus=os.cpu_count())
    if sys.platform != 'win32':
        return result
    class MemoryStatus(ctypes.Structure):
        _fields_ = [('length', wintypes.DWORD), ('load', wintypes.DWORD)] + [
            (name, ctypes.c_ulonglong) for name in
            ('physical', 'available', 'pagefile', 'available_pagefile', 'virtual', 'available_virtual', 'extended')]
    class DisplayDevice(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('name', wintypes.WCHAR*32),
                    ('description', wintypes.WCHAR*128), ('flags', wintypes.DWORD),
                    ('id', wintypes.WCHAR*128), ('key', wintypes.WCHAR*128)]
    try:
        memory = MemoryStatus()
        memory.length = ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            result.update(ram_bytes=memory.physical, available_ram_bytes=memory.available)
        displays = []
        for index in range(32):
            device = DisplayDevice()
            device.size = ctypes.sizeof(device)
            if not ctypes.windll.user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
                break
            displays.append(dict(name=device.name, adapter=device.description, flags=device.flags))
        result['display_adapters'] = displays
    except (OSError, AttributeError) as error:
        result['hardware_query_error'] = str(error)
    return result


def collect_environment():
    from .platform_win import is_remote_session
    versions = {}
    for distribution, module in [('numpy', 'numpy'), ('opencv-python-headless', 'cv2'),
                                  ('onnxruntime', 'onnxruntime'), ('mss', 'mss'), ('PySide6-Essentials', 'PySide6')]:
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            loaded = sys.modules.get(module)
            versions[distribution] = getattr(loaded, '__version__', 'bundled')
    screens = []
    for screen in QGuiApplication.screens():
        geometry = screen.geometry()
        screens.append(dict(name=screen.name(), x=geometry.x(), y=geometry.y(), width=geometry.width(),
                            height=geometry.height(), scale=screen.devicePixelRatio(),
                            dpi=screen.logicalDotsPerInch(), refresh_hz=screen.refreshRate()))
    return dict(version=__version__, recorded_at=datetime.now(timezone.utc).isoformat(),
                windows=platform.platform(), architecture=platform.machine(), python=platform.python_version(),
                frozen=bool(getattr(sys, 'frozen', False)), qt=qVersion(), qt_platform=QGuiApplication.platformName(),
                packages=versions, hardware=_hardware(), screens=screens, remote_session=is_remote_session(),
                capture=dict(backend='MSS region-only', desktop_recording_visible=True,
                             hide_for_recognition=['marksOverlay']),
                strategy='reviewed_few_participants_with_verified_extended_fallback')


def record_environment(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    environment = collect_environment()
    target = folder/'environment.json'
    content = json.dumps(environment, ensure_ascii=False, indent=2)
    descriptor, temporary_name = tempfile.mkstemp(prefix='.environment-', suffix='.tmp', dir=folder)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf8') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    LOG.info('Environment %s', json.dumps(environment, ensure_ascii=False))
    return environment


def export_bundle(folder, target):
    """Package the known debug files only, with size limits and no linked paths."""
    folder, target = Path(folder), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    files = []
    for pattern in ('app.log*', 'startup-*.log', 'crash.log', 'environment.json', 'settings.json', 'latest-*.json',
                    'latest-*.png', 'analysis-history.jsonl*', 'latest-buffs/*.png', 'latest-unrecognized/*.png'):
        for source in folder.glob(pattern):
            try:
                if (source.is_file() and not source.is_symlink() and source.resolve().is_relative_to(folder.resolve())
                        and source.stat().st_size <= 12_000_000):
                    files.append(source)
            except OSError:
                continue
    for handler in logging.getLogger().handlers:
        handler.flush()
    descriptor, temporary = tempfile.mkstemp(prefix='.lisha-debug-', suffix='.zip', dir=target.parent)
    os.close(descriptor)
    total = 0
    included = []
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            for source in sorted(set(files)):
                try:
                    content = source.read_bytes()
                except OSError:
                    continue
                if total+len(content) > 50_000_000:
                    continue
                relative = source.relative_to(folder).as_posix()
                archive.writestr(relative, content)
                included.append(relative)
                total += len(content)
            archive.writestr('manifest.json', json.dumps(dict(version=__version__,
                exported_at=datetime.now(timezone.utc).isoformat(), files=included,
                image_scope='Selected team region and Buff crops only', uncompressed_bytes=total), ensure_ascii=False, indent=2))
        Path(temporary).replace(target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target
