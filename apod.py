#!/usr/bin/env python3

import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


IMAGE_URL = "https://raw.githubusercontent.com/yohanduartep/APOD-Script/refs/heads/main/001.jpg"
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024


def command(name: str) -> str | None:
    return shutil.which(name)


def cache_directory() -> Path:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library/Caches/APOD"
    if system == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "APOD"
    return Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "apod"


def download_wallpaper() -> Path:
    directory = cache_directory()
    directory.mkdir(parents=True, exist_ok=True)
    run_id = time.time_ns()
    temporary = directory / f"wallpaper-{run_id}.tmp"
    request = urllib.request.Request(
        f"{IMAGE_URL}?v={time.time_ns()}",
        headers={"User-Agent": "apod-launcher/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response, temporary.open("wb") as output:
            content_length = response.headers.get("Content-Length")
            if content_length and content_length.isdigit() and int(content_length) > MAX_DOWNLOAD_BYTES:
                raise RuntimeError("Wallpaper exceeds 100 MB")
            downloaded = 0
            while chunk := response.read(1024 * 1024):
                downloaded += len(chunk)
                if downloaded > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError("Wallpaper exceeds 100 MB")
                output.write(chunk)
        suffix = image_suffix(temporary)
        destination = directory / f"wallpaper-{run_id}{suffix}"
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def image_suffix(path: Path) -> str:
    with path.open("rb") as image:
        header = image.read(16)
    if header.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if header.startswith(b"BM"):
        return ".bmp"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return ".webp"
    raise RuntimeError("Downloaded file is not a supported image")


def set_macos(path: Path) -> None:
    xattr = command("xattr")
    if xattr:
        subprocess.run(
            [xattr, "-d", "com.apple.quarantine", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    path_literal = json.dumps(str(path))
    script = f"""ObjC.import('AppKit');
var path = {path_literal};
var workspace = $.NSWorkspace.sharedWorkspace;
var url = $.NSURL.fileURLWithPath(path);
$.NSScreen.screens.js.forEach(function(screen) {{
    var options = workspace.desktopImageOptionsForScreen(screen);
    var error = Ref();
    var success = workspace.setDesktopImageURLForScreenOptionsError(url, screen, options, error);
    if (!success) throw new Error(error[0] ? error[0].localizedDescription.js : 'Wallpaper update failed');
    if (workspace.desktopImageURLForScreen(screen).path.js !== path) throw new Error('Wallpaper update was not retained');
}});"""
    osascript = command("osascript")
    if not osascript:
        raise RuntimeError("osascript not found")
    result = subprocess.run(
        [osascript, "-l", "JavaScript", "-e", script],
        text=True,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "macOS wallpaper update failed")
    killall = command("killall")
    if killall:
        subprocess.run([killall, "WallpaperAgent"], check=False)


def set_windows(path: Path) -> None:
    result = ctypes.windll.user32.SystemParametersInfoW(20, 0, str(path), 3)
    if not result:
        raise ctypes.WinError()


def set_gnome(path: Path, schema: str) -> bool:
    gsettings = command("gsettings")
    if not gsettings:
        return False
    schemas = subprocess.run([gsettings, "list-schemas"], text=True, capture_output=True, check=True).stdout.splitlines()
    if schema not in schemas:
        return False
    uri = path.resolve().as_uri()
    subprocess.run([gsettings, "set", schema, "picture-uri", uri], check=True)
    keys = subprocess.run([gsettings, "list-keys", schema], text=True, capture_output=True, check=True).stdout.splitlines()
    if "picture-uri-dark" in keys:
        subprocess.run([gsettings, "set", schema, "picture-uri-dark", uri], check=True)
    return True


def set_kde(path: Path) -> bool:
    plasma = command("plasma-apply-wallpaperimage")
    if plasma:
        subprocess.run([plasma, str(path)], check=True)
        return True
    qdbus = command("qdbus6") or command("qdbus")
    if not qdbus:
        return False
    image = json.dumps(path.resolve().as_uri())
    script = f"""var desktops = desktops();
for (var i = 0; i < desktops.length; i++) {{
    desktops[i].wallpaperPlugin = 'org.kde.image';
    desktops[i].currentConfigGroup = ['Wallpaper', 'org.kde.image', 'General'];
    desktops[i].writeConfig('Image', {image});
}}"""
    subprocess.run(
        [qdbus, "org.kde.plasmashell", "/PlasmaShell", "org.kde.PlasmaShell.evaluateScript", script],
        check=True,
    )
    return True


def set_xfce(path: Path) -> bool:
    xfconf = command("xfconf-query")
    if not xfconf:
        return False
    result = subprocess.run([xfconf, "-c", "xfce4-desktop", "-l"], text=True, capture_output=True, check=True)
    properties = [value for value in result.stdout.splitlines() if value.endswith("/last-image")]
    if not properties:
        return False
    for prop in properties:
        subprocess.run([xfconf, "-c", "xfce4-desktop", "-p", prop, "-s", str(path.resolve())], check=True)
    return True


def set_linux(path: Path) -> None:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if "gnome" in desktop and set_gnome(path, "org.gnome.desktop.background"):
        return
    if "cinnamon" in desktop and set_gnome(path, "org.cinnamon.desktop.background"):
        return
    if "kde" in desktop and set_kde(path):
        return
    if "xfce" in desktop and set_xfce(path):
        return
    if not desktop:
        if set_gnome(path, "org.gnome.desktop.background"):
            return
        if set_gnome(path, "org.cinnamon.desktop.background"):
            return
        if set_kde(path):
            return
        if set_xfce(path):
            return
    feh = command("feh")
    if feh:
        subprocess.run([feh, "--bg-fill", str(path.resolve())], check=True)
        return
    raise RuntimeError("Unsupported Linux desktop; install feh or use GNOME, Cinnamon, KDE Plasma, or Xfce")


def clean_cache(active: Path) -> None:
    cutoff = time.time() - 7 * 24 * 60 * 60
    for path in active.parent.glob("wallpaper-*.*"):
        if path != active and path.stat().st_mtime < cutoff:
            path.unlink()


def main() -> int:
    try:
        path = download_wallpaper()
        system = platform.system()
        if system == "Darwin":
            set_macos(path)
        elif system == "Windows":
            set_windows(path)
        elif system == "Linux":
            set_linux(path)
        else:
            raise RuntimeError(f"Unsupported operating system: {system}")
        clean_cache(path)
        print(f"Wallpaper set: {path}")
        return 0
    except Exception as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
