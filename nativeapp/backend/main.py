"""PaperHid on-device backend — message router and action dispatcher."""
import logging
import subprocess
import sys
import threading
import time
import traceback

from backend.protocol import Protocol, MSG_REQUEST, SYS_TERMINATE, SYS_NEW_FRONTEND
from backend import bluetooth, config, layout_patcher, service

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
log = logging.getLogger(__name__)

KEYBOARD_LAYOUTS = [
    ("US English", "us"),
    ("US International", "us_intl"),
    ("UK English", "uk"),
    ("German", "de"),
    ("French", "fr"),
    ("Canadian French", "fr_ca"),
    ("Spanish", "es"),
    ("Italian", "it"),
    ("Portuguese", "pt"),
    ("Brazilian", "br"),
    ("Dutch", "nl"),
    ("Swedish", "sv"),
    ("Norwegian", "no"),
    ("Danish", "dk"),
    ("Finnish", "fi"),
    ("Icelandic", "is"),
    ("Swiss German", "de_ch"),
    ("Swiss French", "fr_ch"),
    ("Belgian", "be"),
    ("Russian", "ru"),
    ("Ukrainian", "ua"),
    ("Czech", "cz"),
    ("Hungarian", "hu"),
    ("Turkish", "tr"),
    ("Greek", "gr"),
    ("Hebrew", "he"),
]

LAYOUT_MAP = dict(KEYBOARD_LAYOUTS)


class Backend:
    def __init__(self, socket_path):
        self.proto = Protocol(socket_path)
        self.cfg = config.load()

    def run(self):
        log.info("Backend started")
        while True:
            try:
                msg_type, payload = self.proto.recv()
            except Exception as e:
                log.error("Socket recv failed: %s", e)
                break

            log.info("Received msg_type=%s payload=%s", msg_type, repr(payload)[:200])

            if msg_type == SYS_TERMINATE:
                log.info("Received terminate signal")
                break

            if msg_type == SYS_NEW_FRONTEND:
                log.info("New frontend connected — sending initial status")
                thread = threading.Thread(
                    target=self._push_initial_status, daemon=True
                )
                thread.start()
                continue

            if msg_type == MSG_REQUEST and payload:
                action = payload.get("action")
                params = payload.get("params", {})
                req_id = payload.get("id")
                if action:
                    thread = threading.Thread(
                        target=self._handle_action,
                        args=(action, params, req_id),
                        daemon=True,
                    )
                    thread.start()

    def _handle_action(self, action, params, req_id):
        try:
            handler = getattr(self, f"_action_{action}", None)
            if not handler:
                self._send_error(req_id, f"Unknown action: {action}")
                return
            result = handler(params)
            self._send_result(req_id, result)
        except Exception as e:
            log.error("Action %s failed: %s", action, traceback.format_exc())
            self._send_error(req_id, str(e))

    def _push_initial_status(self):
        time.sleep(0.5)
        try:
            status = self._action_get_status({})
            self._send_event("initial_status", status)
        except Exception as e:
            log.error("Failed to push initial status: %s", e)

    def _send_result(self, req_id, data=None):
        msg = {"id": req_id, "ok": True}
        if data is not None:
            msg["data"] = data
        self.proto.send_response(msg)

    def _send_error(self, req_id, error):
        self.proto.send_response({"id": req_id, "ok": False, "error": error})

    def _send_event(self, event, data=None):
        msg = {"event": event}
        if data is not None:
            msg["data"] = data
        self.proto.send_event(msg)

    def _action_get_status(self, params):
        changed = False
        mac, name = bluetooth.read_device_keyboard()
        if mac:
            if mac != self.cfg.get("keyboard_mac"):
                self.cfg["keyboard_mac"] = mac
                changed = True
            if name and name != self.cfg.get("keyboard_name"):
                self.cfg["keyboard_name"] = name
                changed = True
        elif self.cfg.get("keyboard_mac"):
            self.cfg["keyboard_mac"] = ""
            self.cfg["keyboard_name"] = ""
            changed = True
        device_layout_name = layout_patcher.read_current_layout_display_name(
            KEYBOARD_LAYOUTS
        )
        if device_layout_name and device_layout_name != self.cfg.get("keyboard_layout"):
            self.cfg["keyboard_layout"] = device_layout_name
            changed = True
        if changed:
            config.save(self.cfg)

        state = bluetooth.verify_device_state(self.cfg)
        return {
            **state,
            "keyboard_mac": self.cfg.get("keyboard_mac", ""),
            "keyboard_name": self.cfg.get("keyboard_name", ""),
            "keyboard_layout": self.cfg.get("keyboard_layout", "US English"),
            "layouts": [display for display, _ in KEYBOARD_LAYOUTS],
        }

    def _action_get_config(self, params):
        return {
            "keyboard_mac": self.cfg.get("keyboard_mac", ""),
            "keyboard_name": self.cfg.get("keyboard_name", ""),
            "keyboard_layout": self.cfg.get("keyboard_layout", "US English"),
        }

    def _stop_bt_service(self):
        mac = self.cfg.get("keyboard_mac")
        if mac and bluetooth.get_connection_status(mac):
            try:
                subprocess.run(
                    f"bluetoothctl disconnect {mac}",
                    shell=True, capture_output=True, timeout=5,
                )
                time.sleep(2)
            except Exception:
                pass
        subprocess.run(
            f"systemctl stop {service.SERVICE_NAME}",
            shell=True, capture_output=True, timeout=10,
        )

    def _start_bt_service(self):
        subprocess.run(
            f"systemctl start {service.SERVICE_NAME}",
            shell=True, capture_output=True, timeout=15,
        )

    def _action_scan_devices(self, params):
        timeout = params.get("timeout", 15)
        self._send_event("scan_started")
        self._stop_bt_service()
        try:
            devices = bluetooth.scan_devices(timeout=timeout)
        finally:
            # Leave service stopped until pair finishes; pair restarts it.
            pass
        return {"devices": devices}

    def _action_pair_keyboard(self, params):
        mac = params["mac"]
        name = params.get("name") or mac
        old_mac = self.cfg.get("keyboard_mac") or None

        def passkey_cb(passkey):
            self._send_event("passkey", {"passkey": passkey})

        self._stop_bt_service()
        try:
            bluetooth.pair_and_connect(
                mac, old_mac=old_mac, passkey_callback=passkey_cb
            )
        except Exception:
            self._send_event("pair_error")
            self._start_bt_service()
            raise

        bluez_name = bluetooth.get_device_name(mac)
        if bluez_name:
            name = bluez_name

        service.save_keyboard_mac(mac)
        self.cfg["keyboard_mac"] = mac
        self.cfg["keyboard_name"] = name
        config.save(self.cfg)
        self._start_bt_service()
        self._send_event("pair_complete", {"mac": mac, "name": name})
        return {"mac": mac, "name": name, "connected": True}

    def _action_unpair_keyboard(self, params):
        self._stop_bt_service()
        try:
            mac = self.cfg.get("keyboard_mac")
            if mac:
                bluetooth.remove(mac)
            service.clear_keyboard_mac()
            self.cfg["keyboard_mac"] = ""
            self.cfg["keyboard_name"] = ""
            config.save(self.cfg)
        finally:
            self._start_bt_service()
        return {"unpaired": True}

    def _action_reconnect_keyboard(self, params):
        mac = params.get("mac") or self.cfg.get("keyboard_mac") or None
        self._stop_bt_service()
        try:
            result = bluetooth.reconnect_now(mac)
        finally:
            self._start_bt_service()
        self.cfg["keyboard_mac"] = result["mac"]
        if not self.cfg.get("keyboard_name"):
            self.cfg["keyboard_name"] = bluetooth.get_device_name(result["mac"])
        config.save(self.cfg)
        self._send_event("pair_complete", {
            "mac": result["mac"],
            "name": self.cfg.get("keyboard_name") or result["mac"],
        })
        return result

    def _action_set_layout(self, params):
        display_name = params["layout"]
        layout_key = LAYOUT_MAP.get(display_name)
        if not layout_key:
            raise RuntimeError(f"Unknown layout: {display_name}")

        def status_cb(msg):
            self._send_event("layout_status", {"message": msg})

        layout_patcher.apply_layout(layout_key, status_cb=status_cb)
        self.cfg["keyboard_layout"] = display_name
        config.save(self.cfg)

        try:
            subprocess.Popen(
                [
                    "systemd-run", "--on-active=3s", "--collect",
                    "/bin/systemctl", "restart", "xochitl",
                ],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            log.error("Failed to schedule xochitl restart: %s", e)

        return {
            "layout": display_name,
            "needs_restart": True,
            "message": "Layout changed — restarting interface...",
        }

    def _action_install_service(self, params):
        service.install()
        self.cfg["service_installed"] = True
        config.save(self.cfg)
        return {"service_installed": True}

    def _action_uninstall_service(self, params):
        service.uninstall()
        self.cfg["service_installed"] = False
        self.cfg["keyboard_layout"] = "US English"
        config.save(self.cfg)
        return {"service_installed": False}


def main():
    if len(sys.argv) < 2:
        print("Usage: main.py <socket_path>", file=sys.stderr)
        sys.exit(1)
    Backend(sys.argv[1]).run()


if __name__ == "__main__":
    main()
