Power Peasant - Windows build
=============================

1. Unpack this folder anywhere (USB stick is fine).
2. Run PowerPeasant.exe
   The dashboard opens in its own window (Edge WebView2).
3. On first start Windows SmartScreen may warn: "More info" -> "Run anyway".

Files
-----
PowerPeasant.exe   the app
assets\            UI and the default config template (read-only)
data\              history, config.json, logs, WebView2 profile (created at runtime)

Notes
-----
- Requires Windows 10/11 x64 with a Bluetooth LE adapter.
  Windows 7 is not supported (no Python 3.13 / WebView2 / WinRT BLE there).
- Settings: gear button in the window, or edit data\config.json.
- Logs: data\monitor.log
- The phone app cannot stay connected to a battery while this app holds the BLE link.
- The dashboard has no authentication; keep the default 127.0.0.1 unless you
  know what you are doing (--host 0.0.0.0 / -BindHost 0.0.0.0).
- Requires a Bluetooth LE adapter and Edge WebView2 Runtime
  (preinstalled on Windows 10/11; otherwise install from Microsoft).

Full documentation: docs\README.md in the repository.
