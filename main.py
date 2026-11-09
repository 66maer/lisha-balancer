"""Source and frozen entry point; no network access is needed at runtime."""
from pathlib import Path
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

def run():
    try:
        from lisha_overlay.app import main
        return main()
    except Exception:
        # Capture import-time failures too, before Qt and the main logger exist.
        import os
        import traceback
        details = traceback.format_exc()
        folder = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "LishaBalancer"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "startup-error.log").write_text(details, encoding="utf-8")
        except OSError:
            pass
        if sys.stderr is not None:
            sys.stderr.write(details)
        elif sys.platform == "win32" and not {"--quit", "--check-runtime"}.intersection(sys.argv):
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, "启动失败，详细信息已记录到：\n" + str(folder / "startup-error.log"),
                "戾煞配平 · 启动检查", 0x10)
        return 1

if __name__ == "__main__":
    raise SystemExit(run())
