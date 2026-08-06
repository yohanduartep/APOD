#!/usr/bin/env python3

import ctypes
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


CONTENTS_URL = "https://api.github.com/repos/yohanduartep/APOD-Script/contents"
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


def repository_images() -> list[tuple[str, str]]:
    request = urllib.request.Request(
        f"{CONTENTS_URL}?ref=main&v={time.time_ns()}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "apod-launcher/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        entries = json.load(response)
    images = [
        (entry["name"], entry["download_url"])
        for entry in entries
        if entry.get("type") == "file"
        and re.fullmatch(r"\d{3}\.(?:jpe?g|png|gif|bmp|webp)", entry.get("name", ""), re.IGNORECASE)
        and entry.get("download_url")
    ]
    if not images:
        raise RuntimeError("APOD-Script has no published wallpaper images")
    return sorted(images)


def download_image(name: str, url: str, directory: Path, run_id: int) -> Path:
    temporary = directory / f".{name}-{run_id}.tmp"
    request = urllib.request.Request(f"{url}?v={run_id}", headers={"User-Agent": "apod-launcher/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response, temporary.open("wb") as output:
            content_length = response.headers.get("Content-Length")
            if content_length and content_length.isdigit() and int(content_length) > MAX_DOWNLOAD_BYTES:
                raise RuntimeError(f"{name} exceeds 100 MB")
            downloaded = 0
            while chunk := response.read(1024 * 1024):
                downloaded += len(chunk)
                if downloaded > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError(f"{name} exceeds 100 MB")
                output.write(chunk)
        suffix = image_suffix(temporary)
        destination = directory / f"wallpaper-{run_id}-{Path(name).stem}{suffix}"
        temporary.replace(destination)
        return destination
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def download_wallpapers() -> list[Path]:
    directory = cache_directory()
    directory.mkdir(parents=True, exist_ok=True)
    run_id = time.time_ns()
    return [download_image(name, url, directory, run_id) for name, url in repository_images()]


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


def set_macos(paths: list[Path]) -> list[Path]:
    xattr = command("xattr")
    if xattr:
        for path in paths:
            subprocess.run(
                [xattr, "-d", "com.apple.quarantine", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    osascript = command("osascript")
    if not osascript:
        raise RuntimeError("osascript not found")
    screens_result = subprocess.run(
        [
            osascript,
            "-l",
            "JavaScript",
            "-e",
            "ObjC.import('AppKit'); JSON.stringify($.NSScreen.screens.js.map(function(screen) { return screen.localizedName.js; }));",
        ],
        text=True,
        capture_output=True,
        timeout=15,
    )
    if screens_result.returncode != 0:
        raise RuntimeError(screens_result.stderr.strip() or "Could not list macOS displays")
    screens = json.loads(screens_result.stdout)
    if not screens:
        raise RuntimeError("No macOS displays found")
    applied_paths = []
    for index, name in enumerate(screens, start=1):
        source = paths[(index - 1) % len(paths)]
        screen_path = source.with_name(f"{source.stem}-screen-{index}{source.suffix}")
        shutil.copy2(source, screen_path)
        applied_paths.append(screen_path)
        name_literal = json.dumps(name)
        path_literal = json.dumps(str(screen_path))
        script = f"""ObjC.import('AppKit');
var name = {name_literal};
var path = {path_literal};
var screens = $.NSScreen.screens.js.filter(function(screen) {{ return screen.localizedName.js === name; }});
if (screens.length !== 1) throw new Error('Display not found: ' + name);
var screen = screens[0];
var workspace = $.NSWorkspace.sharedWorkspace;
var url = $.NSURL.fileURLWithPath(path);
var options = workspace.desktopImageOptionsForScreen(screen);
var error = Ref();
var success = workspace.setDesktopImageURLForScreenOptionsError(url, screen, options, error);
if (!success) throw new Error(error[0] ? error[0].localizedDescription.js : 'Wallpaper update failed');
if (workspace.desktopImageURLForScreen(screen).path.js !== path) throw new Error('Wallpaper update was not retained for ' + name);"""
        last_error = "macOS wallpaper update failed"
        for attempt in range(3):
            result = subprocess.run(
                [osascript, "-l", "JavaScript", "-e", script],
                text=True,
                capture_output=True,
                timeout=15,
            )
            if result.returncode == 0:
                break
            last_error = result.stderr.strip() or last_error
            if attempt < 2:
                time.sleep(1)
        else:
            raise RuntimeError(last_error)
    killall = command("killall")
    if killall:
        subprocess.run([killall, "WallpaperAgent"], check=False)
    return applied_paths


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


def clean_cache(active: list[Path]) -> None:
    preserved = {path.resolve() for path in active}
    cutoff = time.time() - 7 * 24 * 60 * 60
    for path in active[0].parent.glob("wallpaper-*.*"):
        if path.resolve() not in preserved and path.stat().st_mtime < cutoff:
            path.unlink()


def main() -> int:
    try:
        paths = download_wallpapers()
        system = platform.system()
        if system == "Darwin":
            active = set_macos(paths)
        elif system == "Windows":
            set_windows(paths[0])
            active = [paths[0]]
        elif system == "Linux":
            set_linux(paths[0])
            active = [paths[0]]
        else:
            raise RuntimeError(f"Unsupported operating system: {system}")
        clean_cache(active)
        print(f"Downloaded {len(paths)} wallpaper(s)")
        for path in paths:
            print(path)
        return 0
    except Exception as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
