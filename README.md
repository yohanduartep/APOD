# APOD

Downloads every numbered wallpaper currently published by
[APOD-Script](https://github.com/yohanduartep/APOD-Script) without requiring a NASA API key.

On macOS, the downloaded images are assigned to connected displays in order. On Windows and
Linux, the first published image is used as the system wallpaper and every image is still downloaded.

## macOS and Linux

```bash
chmod +x apod.sh
./apod.sh
```

Linux support includes GNOME, Cinnamon, KDE Plasma, Xfce, and `feh`.

## Windows

```powershell
.\apod.ps1
```

Python 3 is required.
