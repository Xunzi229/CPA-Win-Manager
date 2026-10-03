"""Read Windows uninstall records without running or modifying installed software."""
import os
from pathlib import Path
import re


def installation_directory(location, icon):
    location = os.path.expandvars(location.strip().strip('"'))
    if location:
        return location
    # DisplayIcon may be a quoted executable followed by an icon resource index.
    match = re.match(r'^\s*"([^"]+)"', icon)
    path = match[1] if match else re.sub(r",\s*-?\d+\s*$", "", icon.strip())
    path = os.path.expandvars(path)
    if path.lower().endswith(".exe") and Path(path).is_absolute():
        return str(Path(path).parent)
    return ""


def scan_installed():
    if os.name != "nt":
        return []
    import winreg
    records, seen = [], set()
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive_name, hive in (("HKCU", winreg.HKEY_CURRENT_USER), ("HKLM", winreg.HKEY_LOCAL_MACHINE)):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                parent = winreg.OpenKey(hive, base, 0, winreg.KEY_READ | view)
            except FileNotFoundError:
                continue
            with parent:
                count = winreg.QueryInfoKey(parent)[0]
                for index in range(count):
                    try:
                        key_name = winreg.EnumKey(parent, index)
                        with winreg.OpenKey(parent, key_name) as key:
                            def value(name):
                                try:
                                    return winreg.QueryValueEx(key, name)[0]
                                except FileNotFoundError:
                                    return ""
                            name = value("DisplayName")
                            if not isinstance(name, str) or not name.strip() or value("SystemComponent") == 1:
                                continue
                            def text(field):
                                result = value(field)
                                return result.strip() if isinstance(result, str) else ""
                            version = text("DisplayVersion")
                            directory = installation_directory(text("InstallLocation"), text("DisplayIcon"))
                            signature = (hive_name, key_name, name, version, directory)
                            if signature in seen:
                                continue
                            seen.add(signature)
                            records.append({"id": f"{hive_name}:{view}:{key_name}", "name": name.strip(),
                                            "version": version, "directory": directory,
                                            "uninstall": text("UninstallString"),
                                            "publisher": text("Publisher")})
                    except (FileNotFoundError, PermissionError):
                        continue
    return sorted(records, key=lambda item: item["name"].casefold())


def normalized_name(value):
    value = re.sub(r"\s+(?:version\s+|v)?\d+(?:\.\d+)+(?:\S*)\s*$", "", value, flags=re.I)
    value = re.sub(r"\s*\((?:preview|x64|x86|arm64|64-bit|32-bit)\)\s*$", "", value, flags=re.I)
    return "".join(character for character in value.casefold() if character.isalnum())


def match_installed(profile, records):
    identity = profile.get("installed_id")
    if identity:
        # A missing explicit association must not silently switch to another installation.
        return next((record for record in records if record["id"] == identity), None)
    if profile.get("installed_auto") is False:
        return None
    names = {normalized_name(profile.get("name", "")),
             normalized_name(profile.get("repository", "").rstrip("/").rsplit("/", 1)[-1])}
    names.discard("")
    matches = []
    for record in records:
        name = normalized_name(record["name"])
        publisher = normalized_name(record.get("publisher", ""))
        if name in names or publisher and name.startswith(publisher) and name[len(publisher):] in names:
            matches.append(record)
    return matches[0] if len(matches) == 1 else None


def installed_update_available(local, latest):
    """Compare system versions with Release tags, including four-part and dated versions."""
    def key(value):
        if not isinstance(value, str):
            return None
        match = re.search(r"(?i)(?:^v?|[-_ ]v?)(\d+(?:\.\d+)+)(?:-([0-9a-z.-]+))?(?:\+[0-9a-z.-]+)?$", value.strip())
        if not match:
            return None
        numbers = [int(number) for number in match[1].split(".")]
        while len(numbers) > 1 and numbers[-1] == 0:
            numbers.pop()
        prerelease = match[2]
        identifiers = tuple((0, int(part)) if part.isdigit() else (1, part.casefold())
                            for part in prerelease.split(".")) if prerelease else ()
        return tuple(numbers), prerelease is None, identifiers
    local_key, latest_key = key(local), key(latest)
    return local_key is not None and latest_key is not None and latest_key > local_key


def uninstall_command(command):
    """Split a registry EXE command without a shell; MSI maintenance becomes uninstall."""
    command = os.path.expandvars(command.strip())
    match = re.fullmatch(r'"([^"\r\n]+\.exe)"(?:\s+(.*))?', command, re.I)
    if match is None:
        match = re.fullmatch(r'([^"\r\n]+?\.exe)(?:\s+(.*))?', command, re.I)
    if match is None:
        raise ValueError("系统记录中没有可用的卸载命令，请在 Windows 设置中卸载。")
    executable, arguments = match[1], match[2] or ""
    if Path(executable).name.lower() == "msiexec.exe":
        arguments = re.sub(r"(?i)(?<!\S)/(?:i(?=\s|\{|$)|package(?=\s|$))", "/X", arguments)
    return executable, arguments


def launch_uninstaller(record):
    # Resolve the current registry record, so a stale association cannot invoke an old command.
    current = next((item for item in scan_installed() if item["id"] == record["id"]), None)
    if current is None:
        raise ValueError("软件已不在系统安装记录中，请刷新本地版本。")
    executable, arguments = uninstall_command(current.get("uninstall", ""))
    import ctypes
    from ctypes import wintypes
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    execute = shell32.ShellExecuteW
    execute.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
    execute.restype = ctypes.c_ssize_t
    result = execute(None, "open", executable, arguments or None, None, 1)
    if result <= 32:
        raise OSError(result, "无法打开系统卸载程序，请在 Windows 设置中卸载。")
