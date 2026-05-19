# =============================================================================
# Copyright (c) 2025 Cr4ckX. All Rights Reserved.
# Лицензия: GNU License
# =============================================================================
# FREE SOFTWARE FOR FREE PEOPLE | СВОБОДУ ПРОГРАММНОМУ ОБЕСПЕЧЕНИЮ!
# =============================================================================

import frida
import sys
import time
import os
import subprocess
import json
import logging
import threading
from pathlib import Path

SCRIPT_DIR   = Path(__file__).parent.resolve()
CONFIG_FILE  = SCRIPT_DIR / "config.json"
MONET_DIR    = SCRIPT_DIR / "monetloader"
HOOK_FILE    = SCRIPT_DIR / "hook.js"
LOG_FILE     = SCRIPT_DIR / "casper.log"
XRAY_DUMP    = SCRIPT_DIR / "lua_xray_dump.txt"

def _resolve_adb() -> str:
    adb_dir = SCRIPT_DIR / "adb"
    if sys.platform == "win32":
        local = adb_dir / "adb.exe"
        return str(local) if local.exists() else "adb"
    else:
        local = adb_dir / "adb"
        return str(local) if local.exists() else "adb"

ADB_BINARY = _resolve_adb()
RECONNECT_DELAY   = 0.4
DEVICE_POLL_DELAY = 2.0

LAUNCHER_PROFILES = {
    "1": {
        "name":       "Arizona/Rodina RP (x64)",
        "package":    "com.arizona.game.git",
        "remote_dir": "/storage/emulated/0/Android/media/com.arizona.game.git/monetloader",
    },
    "2": {
        "name":       "Arizona/Rodina RP (x32)",
        "package":    "com.arizona.game",
        "remote_dir": "/storage/emulated/0/Android/media/com.arizona.game/monetloader",
    },
}

REQUIRED_FILES = ["libfrida-gadget.so", "frida_start.lua", "Arizona Helper.lua"]

def setup_logger() -> logging.Logger:
    logger = logging.getLogger("Casper")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("[%(-asctime)s] %(message)s", datefmt="%H:%M:%S")

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger

log = setup_logger()

try:
    XRAY_DUMP.write_text("=== LUA X-RAY STREAM INITIALIZED ===\n", encoding="utf-8")
except Exception as e:
    print(f"Не удалось создать файл дампа: {e}")

def adb(*args: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [ADB_BINARY, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
        return result.returncode == 0, result.stdout.strip()
    except FileNotFoundError:
        log.error("❌ ADB не найден. Убедитесь, что adb лежит рядом со скриптом или добавлен в PATH.")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)

def get_connected_devices() -> list[str]:
    success, stdout = adb("devices")
    if not success:
        return []
    lines = stdout.splitlines()
    return [
        line.split()[0]
        for line in lines[1:]
        if line.strip() and "device" in line and "offline" not in line
    ]

def wait_for_device() -> str:
    adb("kill-server")
    adb("start-server")

    while True:
        devices = get_connected_devices()

        if len(devices) == 1:
            log.info(f"✅ Устройство обнаружено: {devices[0]}")
            return devices[0]

        if len(devices) > 1:
            print("\n📱 Обнаружено несколько устройств:")
            for i, dev in enumerate(devices, 1):
                print(f"  [{i}] {dev}")
            while True:
                raw = input("Выберите номер устройства: ").strip()
                if raw.isdigit() and 1 <= int(raw) <= len(devices):
                    return devices[int(raw) - 1]
                print("❌ Неверный ввод.")

        log.info("⏳ Ожидание USB-подключения (проверьте режим отладки ADB)...")
        time.sleep(DEVICE_POLL_DELAY)

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as f:
                cfg = json.load(f)
            if {"device_serial", "launcher_choice", "remote_dir"}.issubset(cfg):
                log.info("💾 Конфигурация успешно загружена.")
                return cfg
        except (json.JSONDecodeError, OSError):
            log.warning("⚠️ Конфигурация повреждена — создаю новую.")

    return create_config()

def create_config() -> dict:
    log.info("🔍 Конфигурация не найдена — запуск мастера настройки.\n")
    serial = wait_for_device()

    print("\n=========================================================")
    print("  Выберите профиль лаунчера:")
    for key, val in LAUNCHER_PROFILES.items():
        print(f"  [{key}] {val['name']}")
    print("=========================================================")

    choice = ""
    while choice not in LAUNCHER_PROFILES:
        choice = input("Введите 1 или 2: ").strip()
        if choice not in LAUNCHER_PROFILES:
            print("❌ Неверный выбор.")

    profile = LAUNCHER_PROFILES[choice]
    cfg = {
        "device_serial":   serial,
        "launcher_choice": choice,
        "launcher_name":   profile["name"],
        "remote_dir":      profile["remote_dir"],
    }

    try:
        with CONFIG_FILE.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)
        log.info(f"💾 Конфигурация сохранена: {CONFIG_FILE}")
    except OSError as e:
        log.warning(f"⚠️ Не удалось сохранить конфигурацию: {e}")

    return cfg

def reset_config():
    if CONFIG_FILE.exists():
        CONFIG_FILE.unlink()
        log.info("🗑️ Конфигурация удалена.")

def sync_files(remote_path: str):
    log.info("🔍 Проверка файловой структуры на устройстве...")
    adb("shell", f"mkdir -p {remote_path}")

    for filename in REQUIRED_FILES:
        remote_file = f"{remote_path}/{filename}"
        ok, out = adb("shell", f"ls '{remote_file}'")

        if not ok or "No such file" in out or out == "":
            local_file = MONET_DIR / filename
            if local_file.exists():
                log.info(f"⬆️ Синхронизация: отправка {filename}...")
                ok2, _ = adb("push", str(local_file), remote_file)
                if ok2:
                    log.info(f"✅ {filename} успешно установлен.")
                else:
                    log.error(f"❌ Ошибка загрузки {filename}.")
            else:
                log.warning(f"⚠️ Файл {filename} отсутствует локально: {local_file}")

def on_message(message: dict, _data):
    if message["type"] == "send":
        payload = message["payload"]
        m_type = payload.get("type")
        m_data = payload.get("payload")
        
        log_line = ""
        if m_type == "info":
            log_line = f"[!] Info: {m_data}\n"
            log.info(f"💬 {m_data}")
            
        if log_line:
            try:
                with open(XRAY_DUMP, "a", encoding="utf-8") as f:
                    f.write(log_line)
            except Exception:
                pass
    elif message["type"] == "error":
        log.error(f"❌ Frida JS: {message['description']}")

def monitor_loop(device_serial: str):
    if not HOOK_FILE.exists():
        log.error(f"❌ Файл хука не найден: {HOOK_FILE}")
        sys.exit(1)

    hook_code = HOOK_FILE.read_text(encoding="utf-8")

    log.info("🎉 Мониторинг запущен. Свободу коду!")
    log.info("📡 Ожидание запуска игры на устройстве...\n")

    while True:
        session = None
        try:
            device = frida.get_device(device_serial)
            session = device.attach("Gadget")

            log.info("🔗 Подключение к игровому процессу установлено!")
            log.info("⚡ Внедрение сигнатур обхода...")

            script = session.create_script(hook_code)
            script.on("message", on_message)
            script.load()

            log.info("✅ Перехватчики активны. Лог транслируется в lua_xray_dump.txt\n")

            done = threading.Event()

            def on_detached(*args, **kwargs):
                reason = args[0] if args else kwargs.get("reason", "unknown")
                log.info(f"🔄 Сессия завершена (Причина: {reason}). Ожидание перезапуска...")
                done.set()

            session.on("detached", on_detached)
            done.wait()

        except KeyboardInterrupt:
            log.info("👋 Работа Casper завершена пользователем.")
            sys.exit(0)
        except frida.ServerNotRunningError:
            pass
        except frida.TransportError:
            log.warning("⚠️ Потеряна связь с устройством. Попытка переподключения...")
        except Exception:
            pass
        finally:
            if session is not None:
                try:
                    session.detach()
                except Exception:
                    pass

        time.sleep(RECONNECT_DELAY)

def print_banner():
    print("=========================================================")
    print("===      Casper CARZH — Launcher v2.0 | by Cr4ckX     ===")
    print("===             FREE SOFTWARE FOR FREE PEOPLE         ===")
    print("=========================================================\n")

def main():
    print_banner()

    if "--reset" in sys.argv:
        reset_config()

    cfg = load_config()

    log.info(f"🎯 Активное устройство: {cfg['device_serial']}")
    log.info(f"🎯 Текущий профиль:     {cfg.get('launcher_name', 'Из конфигурации')}")

    sync_files(cfg["remote_dir"])
    monitor_loop(cfg["device_serial"])

if __name__ == "__main__":
    main()
