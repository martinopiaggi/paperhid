import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from ui import styles
from core import (
    config,
    bluetooth,
    service_installer,
    layout_patcher,
    native_app_installer,
)
from core import device as device_mod
from core.logutil import get_logger
from core.service_installer import save_keyboard_mac

log = get_logger("ui")

PENDING, RUNNING, DONE, ERROR = "pending", "running", "done", "error"
DOTS = {PENDING: "\u25cb", RUNNING: "\u25d4", DONE: "\u25cf", ERROR: "\u2717"}
DOT_COLORS = {
    PENDING: styles.FG_DIM,
    RUNNING: styles.WARNING,
    DONE: styles.SUCCESS,
    ERROR: styles.ERROR,
}
PAD_X = 24

KEYBOARD_LAYOUTS = [
    ("US English", "us"), ("US International", "us_intl"), ("UK English", "uk"),
    ("German", "de"), ("French", "fr"), ("Canadian French", "fr_ca"),
    ("Spanish", "es"), ("Italian", "it"), ("Portuguese", "pt"),
    ("Brazilian", "br"), ("Dutch", "nl"), ("Swedish", "sv"),
    ("Norwegian", "no"), ("Danish", "dk"), ("Finnish", "fi"),
    ("Icelandic", "is"), ("Swiss German", "de_ch"), ("Swiss French", "fr_ch"),
    ("Belgian", "be"), ("Russian", "ru"), ("Ukrainian", "ua"),
    ("Czech", "cz"), ("Hungarian", "hu"), ("Turkish", "tr"),
    ("Greek", "gr"), ("Hebrew", "he"),
]
LAYOUT_NAMES = [n for n, _ in KEYBOARD_LAYOUTS]
LAYOUT_MAP = dict(KEYBOARD_LAYOUTS)

_LAYOUT_HINT = (
    "Like Windows layout switching: pick Italian and a US keyboard types "
    "Italian positions ([ → è, Shift+[ → é). US International: ' then e → é."
)


class PasskeyDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Bluetooth Passkey")
        self.configure(bg=styles.BG)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.geometry("360x200")
        self.update_idletasks()
        tl = parent.winfo_toplevel()
        x = tl.winfo_rootx() + (tl.winfo_width() - 360) // 2
        y = tl.winfo_rooty() + (tl.winfo_height() - 200) // 2
        self.geometry(f"+{x}+{y}")

        tk.Label(
            self, text="Passkey required",
            font=styles.FONT_HEADING, bg=styles.BG, fg=styles.FG,
        ).pack(pady=(24, 8))
        self.passkey_label = tk.Label(
            self, text="------",
            font=("Menlo", 36, "bold"), bg=styles.BG, fg=styles.ACCENT,
        )
        self.passkey_label.pack(pady=(4, 8))
        tk.Label(
            self, text="Type this code on your keyboard, then press Enter.",
            font=styles.FONT_BODY, bg=styles.BG, fg=styles.FG_DIM, wraplength=300,
        ).pack(pady=(0, 16))
        self.protocol("WM_DELETE_WINDOW", self._close)

    def show_passkey(self, passkey):
        self.passkey_label.configure(text=passkey)

    def close_with_success(self):
        self._close()

    def close_with_error(self, _msg):
        self._close()

    def _close(self):
        try:
            self.grab_release()
            self.destroy()
        except Exception:
            pass


class MainScreen(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.configure(style="TFrame")
        self.sections = {}
        self.device_list = []
        self._monitor_active = False
        self._passkey_dialog = None
        self._build_ui()
        if self.app.cfg.get("service_installed"):
            self.svc_status_var.set("Connect to verify")

    # ── helpers ───────────────────────────────────────────────

    def _bg(self, work, on_ok=None, on_err=None):
        def runner():
            try:
                result = work()
                if on_ok:
                    self.after(0, on_ok, result)
            except Exception as e:
                log.warning("%s", e)
                if on_err:
                    self.after(0, on_err, str(e))
        threading.Thread(target=runner, daemon=True).start()

    def _set_status(self, name, status):
        sec = self.sections[name]
        sec["status"] = status
        if sec["dot"]:
            sec["dot"].configure(text=DOTS[status], foreground=DOT_COLORS[status])

    def _make_section(self, name, title):
        outer = ttk.Frame(self._scroll_body, style="Card.TFrame")
        outer.pack(fill="x", padx=PAD_X, pady=(8, 0))
        border = tk.Frame(outer, bg=styles.BORDER, padx=1, pady=1)
        border.pack(fill="x")
        inner = ttk.Frame(border, style="Card.TFrame")
        inner.pack(fill="x")

        header = ttk.Frame(inner, style="Card.TFrame")
        header.pack(fill="x", padx=16, pady=(12, 8))
        ttk.Label(header, text=title, style="CardHeading.TLabel").pack(side="left")
        dot = ttk.Label(
            header, text=DOTS[PENDING], foreground=DOT_COLORS[PENDING],
            background=styles.BG_CARD, font=("Helvetica", 16),
        )
        dot.pack(side="right", padx=(8, 0))

        body = ttk.Frame(inner, style="Card.TFrame")
        body.pack(fill="x", padx=16, pady=(0, 14))
        self.sections[name] = {"dot": dot, "status": PENDING}
        return body

    def _label(self, parent, text="", style="CardStatus.TLabel", pady=(0, 8), **kw):
        kw.setdefault("wraplength", 400)
        lbl = ttk.Label(parent, text=text, style=style, **kw)
        lbl.pack(anchor="w", pady=pady)
        return lbl

    def _btn_row(self, parent, text, cmd, style="Accent.TButton", state="disabled"):
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x")
        btn = ttk.Button(row, text=text, style=style, command=cmd, state=state)
        btn.pack(side="left")
        var = tk.StringVar()
        ttk.Label(row, textvariable=var, style="CardStatus.TLabel").pack(
            side="left", padx=(12, 0)
        )
        return btn, var

    def _need_ssh(self, status_var):
        if self.app.ssh.is_connected:
            return True
        status_var.set("Connect to device first.")
        return False

    # ── build ─────────────────────────────────────────────────

    def _build_ui(self):
        canvas = tk.Canvas(self, bg=styles.BG, highlightthickness=0, bd=0)
        canvas.pack(side="left", fill="both", expand=True)
        self._scroll_canvas = canvas
        self._scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=self._scrollbar.set)

        body = ttk.Frame(canvas, style="TFrame")
        self._scroll_body = body
        self._body_window = canvas.create_window((0, 0), window=body, anchor="nw")

        def on_body(_e):
            canvas.configure(scrollregion=canvas.bbox("all"))
            self._update_scrollbar()

        def on_canvas(e):
            canvas.itemconfigure(self._body_window, width=e.width)
            self._update_scrollbar()

        body.bind("<Configure>", on_body)
        canvas.bind("<Configure>", on_canvas)
        self._bind_mousewheel()

        logo_frame = ttk.Frame(body, style="TFrame")
        logo_frame.pack(fill="x", padx=PAD_X, pady=(20, 4))
        logo_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "images", "movewriter-logo.png"
        )
        if os.path.exists(logo_path):
            from PIL import Image, ImageTk
            img = Image.open(logo_path)
            scale = 40 / img.height
            img = img.resize((int(img.width * scale), 40), Image.LANCZOS)
            self._logo_image = ImageTk.PhotoImage(img)
            ttk.Label(logo_frame, image=self._logo_image, background=styles.BG).pack(
                side="left"
            )

        sep = ttk.Frame(self._scroll_body, height=1)
        sep.pack(fill="x", padx=PAD_X, pady=(8, 4))
        tk.Frame(sep, bg=styles.BORDER, height=1).pack(fill="x")

        self._build_connection()
        self._build_service()
        self._build_keyboard()
        self._build_native()

    def _update_scrollbar(self):
        bbox = self._scroll_canvas.bbox("all")
        if not bbox:
            return
        overflow = (bbox[3] - bbox[1]) > self._scroll_canvas.winfo_height()
        if overflow and not self._scrollbar.winfo_ismapped():
            self._scrollbar.pack(side="right", fill="y")
        elif not overflow and self._scrollbar.winfo_ismapped():
            self._scrollbar.pack_forget()
            self._scroll_canvas.yview_moveto(0)

    def _bind_mousewheel(self):
        def on_wheel(event):
            if not self._scrollbar.winfo_ismapped():
                return
            if event.num == 4:
                self._scroll_canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                self._scroll_canvas.yview_scroll(1, "units")
            else:
                self._scroll_canvas.yview_scroll(
                    -1 if event.delta > 0 else 1, "units"
                )
        self.bind_all("<MouseWheel>", on_wheel)
        self.bind_all("<Button-4>", on_wheel)
        self.bind_all("<Button-5>", on_wheel)

    # ── connection ────────────────────────────────────────────

    def _build_connection(self):
        body = self._make_section("connection", "Device")
        form = ttk.Frame(body, style="Card.TFrame")
        form.pack(fill="x")

        ttk.Label(form, text="IP Address", style="Card.TLabel").pack(anchor="w")
        self.ip_var = tk.StringVar(value=self.app.cfg.get("ip", "10.11.99.1"))
        self.ip_entry = styles.make_entry(form, textvariable=self.ip_var, width=30)
        self.ip_entry.pack(fill="x", pady=(2, 6))

        ttk.Label(form, text="SSH Password", style="Card.TLabel").pack(anchor="w")
        self.pw_var = tk.StringVar(value=config.get_password(self.app.cfg))
        self.pw_entry = styles.make_entry(
            form, textvariable=self.pw_var, show="*", width=30
        )
        self.pw_entry.pack(fill="x", pady=(2, 0))
        ttk.Label(
            form,
            text="Settings \u2192 Help \u2192 About \u2192 Copyrights and licenses",
            style="CardDim.TLabel",
        ).pack(anchor="w", pady=(0, 10))

        row = ttk.Frame(form, style="Card.TFrame")
        row.pack(fill="x")
        self.connect_btn = ttk.Button(
            row, text="Connect to Device", style="Accent.TButton",
            command=self._on_connect,
        )
        self.connect_btn.pack(side="left")
        self.conn_status_var = tk.StringVar()
        self.conn_status_label = ttk.Label(
            row, textvariable=self.conn_status_var, style="CardStatus.TLabel",
        )
        self.conn_status_label.pack(side="left", padx=(12, 0))
        self.ip_entry.bind("<Return>", lambda e: self.pw_entry.focus())
        self.pw_entry.bind("<Return>", lambda e: self._on_connect())

    def _set_conn_status(self, text, label_style="CardStatus.TLabel"):
        self.conn_status_var.set(text)
        self.conn_status_label.configure(style=label_style)

    def _on_connect(self):
        ip = self.ip_var.get().strip()
        pw = self.pw_var.get().strip()
        if not ip:
            self._set_conn_status("Enter IP address", "Error.TLabel")
            return
        self.connect_btn.configure(state="disabled")
        self._set_conn_status("Connecting...", "Warning.TLabel")

        def work():
            self.app.ssh.connect(ip, pw)
            return ip, pw

        self._bg(work, on_ok=lambda r: self._on_connected(*r), on_err=self._on_connect_error)

    def _on_connected(self, ip, pw):
        self.app.cfg["ip"] = ip
        config.set_password(self.app.cfg, pw)
        config.save(self.app.cfg)
        self._set_conn_status("Connected — detecting device...", "Warning.TLabel")
        self._set_status("connection", DONE)
        self.install_btn.configure(state="normal")
        self.scan_btn.configure(state="normal")

        def work():
            label = "reMarkable"
            supports_layout = supports_native = False
            try:
                info = device_mod.detect(self.app.ssh)
                self.app.device_info = info
                label = info.get("label") or label
                try:
                    service_installer.ensure_installed(self.app.ssh)
                except Exception as e:
                    log.warning("ensure_installed soft-fail: %s", e)
                supports_layout = bool(info.get("supports_layout_patch"))
                supports_native = bool(info.get("supports_native_app"))
            except Exception as e:
                log.warning("device detect failed: %s", e)
                self.app.device_info = {
                    "model": device_mod.MODEL_UNKNOWN,
                    "label": label,
                    "supports_layout_patch": False,
                    "supports_native_app": False,
                }
            return label, supports_layout, supports_native

        def apply(result):
            label, supports_layout, supports_native = result
            self._set_conn_status(f"Connected ({label})", "Success.TLabel")
            if supports_layout:
                self.layout_combo.configure(state="readonly")
                self.layout_status_var.set("")
                self.layout_hint_var.set(_LAYOUT_HINT)
            else:
                self.layout_combo.configure(state="disabled")
                self.layout_status_var.set("Language layouts not available on this device")
                self.layout_hint_var.set("Language layouts are not available on this model.")
            if supports_native:
                self.native_btn.configure(state="normal")
            else:
                self.native_btn.configure(state="disabled")
                self.native_status_var.set("On-device app not available on this device")
            self._verify_device_state()
            self._start_monitor()

        self._bg(work, on_ok=apply)

    def _on_connect_error(self, msg):
        self.connect_btn.configure(state="normal")
        self._set_conn_status(f"Failed: {msg.split(chr(10))[0][:80]}", "Error.TLabel")
        self._set_status("connection", ERROR)

    def _start_monitor(self):
        self._monitor_active = True
        self._check_connection()

    def _check_connection(self):
        if not self._monitor_active:
            return

        def ping():
            try:
                self.app.ssh.exec("true", timeout=3)
                if self._monitor_active:
                    self.after(3000, self._check_connection)
            except Exception:
                if self._monitor_active:
                    self.after(0, self._on_disconnected)

        threading.Thread(target=ping, daemon=True).start()

    def _on_disconnected(self):
        self._monitor_active = False
        self.app.device_info = None
        self.connect_btn.configure(state="normal")
        self._set_conn_status("Disconnected", "Error.TLabel")
        self._set_status("connection", ERROR)
        self._set_status("service", PENDING)
        self.svc_status_var.set("")
        self._set_status("keyboard", PENDING)
        self.kb_status_var.set("")
        self.install_btn.configure(state="disabled")
        self.scan_btn.configure(state="disabled")
        self.layout_combo.configure(state="disabled")
        self.layout_status_var.set("")
        self.native_btn.configure(state="disabled")
        self._set_status("native_app", PENDING)
        self.native_status_var.set("")

    # ── service ───────────────────────────────────────────────

    def _build_service(self):
        body = self._make_section("service", "Bluetooth Service")
        self._label(
            body,
            "Enables your Paper Pro to remember and reconnect to a Bluetooth keyboard.",
        )
        self.install_btn, self.svc_status_var = self._btn_row(
            body, "Enable", self._run_service_toggle
        )

    def _run_service_toggle(self):
        if not self._need_ssh(self.svc_status_var):
            return
        installed = self.app.cfg.get("service_installed", False)
        native = self.app.cfg.get("native_app_installed", False)
        if installed and native:
            if not messagebox.askyesno(
                "Disable Bluetooth Service",
                "The on-device app depends on the Bluetooth Service. "
                "Disabling will also remove the Native App.\n\nContinue?",
            ):
                return

        self._set_status("service", RUNNING)
        self.install_btn.configure(state="disabled")
        self._monitor_active = False
        cascade = installed and native
        self.svc_status_var.set("Disabling..." if installed else "Enabling...")

        def work():
            if installed:
                if cascade:
                    native_app_installer.uninstall(self.app.ssh)
                service_installer.uninstall(self.app.ssh)
                return ("off", cascade)
            service_installer.install(self.app.ssh)
            return ("on", False)

        def done(result):
            kind, cascaded = result
            if kind == "on":
                self._set_status("service", DONE)
                self.svc_status_var.set("Enabled")
                self.install_btn.configure(state="normal", text="Disable")
                self.app.cfg["service_installed"] = True
                config.save(self.app.cfg)
                mac = self.app.cfg.get("keyboard_mac")
                if mac:
                    self._bg(lambda: (save_keyboard_mac(self.app.ssh, mac), True)[1])
            else:
                self._set_status("service", PENDING)
                self.svc_status_var.set("")
                self.install_btn.configure(state="normal", text="Enable")
                self.app.cfg["service_installed"] = False
                self.app.cfg["setup_complete"] = False
                if cascaded:
                    self.app.cfg["native_app_installed"] = False
                    self._set_status("native_app", PENDING)
                    self.native_status_var.set("")
                    self.native_btn.configure(text="Install on device")
                config.save(self.app.cfg)
            delay = 8000 if cascaded else 0
            self.after(delay, self._start_monitor)

        def err(msg):
            self._set_status("service", ERROR)
            self.svc_status_var.set(f"Error: {msg[:100]}")
            self.install_btn.configure(state="normal")
            self._start_monitor()

        self._bg(work, on_ok=done, on_err=err)

    # ── keyboard ──────────────────────────────────────────────

    def _var_label(self, parent, var, style="CardStatus.TLabel", **pack):
        lbl = ttk.Label(parent, textvariable=var, style=style)
        lbl.pack(**pack)
        return lbl

    def _build_keyboard(self):
        body = self._make_section("keyboard", "Keyboard")
        self._label(
            body,
            "After pairing, your keyboard stays connected even after closing this app.",
            pady=(0, 10),
        )

        # Saved keyboard
        self.kb_saved_frame = ttk.Frame(body, style="Card.TFrame")
        self.kb_name_var, self.kb_status_var = tk.StringVar(), tk.StringVar()
        self._var_label(
            self.kb_saved_frame, self.kb_name_var, "Card.TLabel", anchor="w"
        ).configure(font=styles.FONT_HEADING)
        self._var_label(
            self.kb_saved_frame, self.kb_status_var, anchor="w", pady=(2, 8)
        )

        layout_row = ttk.Frame(self.kb_saved_frame, style="Card.TFrame")
        layout_row.pack(fill="x", pady=(0, 8))
        ttk.Label(layout_row, text="Language", style="Card.TLabel").pack(side="left")
        self.layout_var = tk.StringVar()
        self.layout_combo = ttk.Combobox(
            layout_row, textvariable=self.layout_var,
            values=LAYOUT_NAMES, state="readonly", width=20,
        )
        self.layout_combo.pack(side="left", padx=(8, 0))
        self.layout_combo.set(self.app.cfg.get("keyboard_layout", "US English"))
        self.layout_combo.bind("<<ComboboxSelected>>", self._on_layout_changed)
        self.layout_status_var = tk.StringVar()
        self._var_label(
            layout_row, self.layout_status_var, side="left", padx=(8, 0)
        )
        self.layout_hint_var = tk.StringVar(value="Connect to enable Language. " + _LAYOUT_HINT)
        ttk.Label(
            self.kb_saved_frame, textvariable=self.layout_hint_var,
            style="CardStatus.TLabel", wraplength=400,
        ).pack(anchor="w", pady=(0, 10))

        btns = ttk.Frame(self.kb_saved_frame, style="Card.TFrame")
        btns.pack(fill="x")
        self.forget_btn = ttk.Button(btns, text="Change Keyboard", command=self._show_scan_view)
        self.forget_btn.pack(side="left")
        self.unpair_btn = ttk.Button(btns, text="Unpair", command=self._run_unpair)
        self.unpair_btn.pack(side="left", padx=(8, 0))

        # Scan / pair
        self.kb_scan_frame = ttk.Frame(body, style="Card.TFrame")
        self.cancel_scan_link = ttk.Label(
            self.kb_scan_frame, text="\u2190 Back to saved keyboard",
            style="CardStatus.TLabel", foreground=styles.ACCENT, cursor="hand2",
        )
        self.cancel_scan_link.bind("<Button-1>", lambda e: self._show_saved_view())
        self.kb_scan_instructions = ttk.Label(
            self.kb_scan_frame,
            text="Put keyboard in pairing mode. Remove it from other devices first.",
            style="CardStatus.TLabel", wraplength=400,
        )
        self.kb_scan_instructions.pack(anchor="w", pady=(0, 8))
        self.scan_btn, self.scan_status_var = self._btn_row(
            self.kb_scan_frame, "Scan for Keyboards", self._run_scan, style="Blue.TButton"
        )
        self.device_listbox = tk.Listbox(
            self.kb_scan_frame, height=5,
            bg=styles.BG_CARD, fg=styles.FG, font=styles.FONT_BODY,
            selectbackground=styles.ACCENT, selectforeground="white",
            borderwidth=1, highlightthickness=0, relief="solid", selectmode="browse",
        )
        self.pair_row = ttk.Frame(self.kb_scan_frame, style="Card.TFrame")
        self.pair_btn = ttk.Button(
            self.pair_row, text="Pair Selected", style="Blue.TButton", command=self._run_pair,
        )
        self.pair_btn.pack(side="left")
        self.pair_status_var = tk.StringVar()
        self._var_label(self.pair_row, self.pair_status_var, side="left", padx=(12, 0))

        (self._show_saved_view if self.app.cfg.get("keyboard_mac") else self._show_scan_view)()

    def _show_saved_view(self):
        self.kb_scan_frame.pack_forget()
        self.kb_name_var.set(self.app.cfg.get("keyboard_name", "Unknown"))
        self.kb_status_var.set("")
        self.kb_saved_frame.pack(fill="x")

    def _show_scan_view(self):
        self._old_keyboard_mac = self.app.cfg.get("keyboard_mac")
        self.kb_saved_frame.pack_forget()
        self.scan_status_var.set("")
        self.pair_status_var.set("")
        self.device_listbox.delete(0, tk.END)
        self.device_listbox.pack_forget()
        self.pair_row.pack_forget()
        self.pair_btn.configure(state="normal")
        if self.app.cfg.get("keyboard_mac"):
            self.cancel_scan_link.pack(
                anchor="w", pady=(16, 4), before=self.kb_scan_instructions
            )
        else:
            self.cancel_scan_link.pack_forget()
        self.kb_scan_frame.pack(fill="x")

    def _run_scan(self):
        if not self._need_ssh(self.scan_status_var):
            return
        self.scan_btn.configure(state="disabled")
        self.scan_status_var.set("Checking Bluetooth radio, then scanning...")
        self.device_listbox.delete(0, tk.END)
        self.device_listbox.pack_forget()
        self.pair_row.pack_forget()

        def done(devices):
            self.scan_btn.configure(state="normal")
            self.device_list = devices or []
            self.device_listbox.delete(0, tk.END)
            if not self.device_list:
                self.scan_status_var.set("No devices found. Try again.")
                return
            self.scan_status_var.set(f"Found {len(self.device_list)} device(s). Select yours:")
            for d in self.device_list:
                self.device_listbox.insert(tk.END, f"{d['name']}  ({d['mac']})")
            self.device_listbox.pack(fill="x", pady=(8, 8))
            self.pair_btn.configure(state="normal")
            self.pair_status_var.set("")
            self.pair_row.pack(fill="x")

        def err(msg):
            self.scan_btn.configure(state="normal")
            lower = (msg or "").lower()
            if "discovery is broken" in lower or "0x2005" in msg:
                self.scan_status_var.set(
                    "Paper Pro radio cannot scan (firmware) — pairing blocked until scan works"
                )
                try:
                    messagebox.showerror(
                        "Paper Pro Bluetooth discovery broken", msg[:1200], parent=self
                    )
                except Exception:
                    pass
                return
            self.scan_status_var.set(
                f"Scan error: {(msg or '').replace(chr(10), ' ').split('. ')[0][:140]}"
            )

        self._bg(
            lambda: bluetooth.scan_devices(self.app.ssh, timeout=12),
            on_ok=done, on_err=err,
        )

    def _run_pair(self):
        sel = self.device_listbox.curselection()
        if not sel:
            self.pair_status_var.set("Select a device first.")
            return
        if not self._need_ssh(self.pair_status_var):
            return
        device = self.device_list[sel[0]]
        self.pair_btn.configure(state="disabled")
        self._set_status("keyboard", RUNNING)
        self.pair_status_var.set(f"Pairing with {device['name']}...")

        def on_passkey(passkey):
            self.after(0, self._show_passkey, passkey)

        def work():
            old = getattr(self, "_old_keyboard_mac", None)
            bluetooth.pair_and_connect(
                self.app.ssh, device["mac"],
                old_mac=old, passkey_callback=on_passkey,
            )
            save_keyboard_mac(self.app.ssh, device["mac"])
            return device

        def done(device):
            if self._passkey_dialog is not None:
                self._passkey_dialog.close_with_success()
                self._passkey_dialog = None
            self._set_status("keyboard", DONE)
            self.app.cfg["keyboard_mac"] = device["mac"]
            self.app.cfg["keyboard_name"] = device["name"]
            config.save(self.app.cfg)
            self._show_saved_view()
            self.kb_status_var.set("Connected")

        def err(msg):
            if self._passkey_dialog is not None:
                self._passkey_dialog.close_with_error(msg)
                self._passkey_dialog = None
            self._set_status("keyboard", ERROR)
            self.pair_btn.configure(state="normal")
            first = (msg or "").replace("\n", " ").split(". ")[0][:140]
            self.pair_status_var.set(f"Pair error: {first}")
            if len(msg or "") > 120:
                try:
                    messagebox.showerror("Pairing failed", msg[:900], parent=self)
                except Exception:
                    pass

        self._bg(work, on_ok=done, on_err=err)

    def _show_passkey(self, passkey):
        if self._passkey_dialog is not None:
            try:
                self._passkey_dialog.show_passkey(passkey)
                return
            except Exception:
                self._passkey_dialog = None
        self._passkey_dialog = PasskeyDialog(self)
        self._passkey_dialog.show_passkey(passkey)

    def _run_unpair(self):
        mac = self.app.cfg.get("keyboard_mac")
        if not mac or not self._need_ssh(self.kb_status_var):
            return
        self.unpair_btn.configure(state="disabled")
        self.kb_status_var.set("Unpairing...")

        def work():
            bluetooth.remove(self.app.ssh, mac)
            self.app.ssh.exec(f"rm -f {service_installer.KEYBOARD_MAC_PATH}", timeout=5)
            return True

        def done(_):
            self.app.cfg.pop("keyboard_mac", None)
            self.app.cfg.pop("keyboard_name", None)
            config.save(self.app.cfg)
            self._set_status("keyboard", PENDING)
            self._show_scan_view()

        def err(msg):
            self.unpair_btn.configure(state="normal")
            self.kb_status_var.set(f"Unpair failed: {msg[:80]}")

        self._bg(work, on_ok=done, on_err=err)

    # ── layout ────────────────────────────────────────────────

    def _on_layout_changed(self, _event=None):
        display = self.layout_var.get()
        key = LAYOUT_MAP.get(display, "us")
        self.app.cfg["keyboard_layout"] = display
        config.save(self.app.cfg)
        if not self.app.ssh.is_connected:
            self.layout_status_var.set("Saved (apply on next connect)")
            return
        info = getattr(self.app, "device_info", None) or {}
        if not info.get("supports_layout_patch"):
            self.layout_status_var.set("Language layouts not available on this device")
            return
        self._apply_layout(key)

    def _apply_layout(self, layout_key, display_name=None):
        if not self.app.ssh.is_connected:
            return
        info = getattr(self.app, "device_info", None) or {}
        if not info.get("supports_layout_patch"):
            return
        if display_name:
            self.layout_var.set(display_name)
        self.layout_combo.configure(state="disabled")
        self.layout_status_var.set("Applying...")

        def work():
            layout_patcher.apply_layout(
                self.app.ssh, layout_key,
                status_cb=lambda m: self.after(0, self.layout_status_var.set, m),
            )
            return True

        def done(_):
            self.layout_combo.configure(state="readonly")
            self.layout_status_var.set("Applied")

        def err(msg):
            self.layout_combo.configure(state="readonly")
            self.layout_status_var.set(f"Error: {msg[:60]}")

        self._bg(work, on_ok=done, on_err=err)

    # ── state sync ────────────────────────────────────────────

    def _verify_device_state(self):
        self._apply_config_state()

        def work():
            state = bluetooth.verify_device_state(self.app.ssh, self.app.cfg)
            state["native_app_installed"] = native_app_installer.is_installed(self.app.ssh)
            return state

        def done(state):
            self._apply_verified_state(state)

        self._bg(work, on_ok=done)

        def sync():
            self._sync_layout()
            return True

        self._bg(sync)

    def _sync_layout(self):
        info = getattr(self.app, "device_info", None) or {}
        if not info.get("supports_layout_patch"):
            return
        try:
            device_layout = layout_patcher.read_device_layout(self.app.ssh)
        except Exception:
            device_layout = None
        cfg_display = self.app.cfg.get("keyboard_layout", "US English")
        cfg_key = LAYOUT_MAP.get(cfg_display, "us")

        if device_layout and cfg_key == device_layout:
            display = next(
                (n for n, k in KEYBOARD_LAYOUTS if k == device_layout), "US English"
            )
            self.after(0, self.layout_combo.set, display)
            return
        if device_layout and cfg_key != device_layout:
            self.after(0, self._apply_layout, cfg_key, cfg_display)
            return
        if cfg_key and cfg_key != "us":
            self.after(0, self._apply_layout, cfg_key, cfg_display)

    def _apply_verified_state(self, state):
        cfg = self.app.cfg
        changed = False

        if state["service_installed"]:
            self._set_status("service", DONE)
            self.svc_status_var.set("Enabled")
            self.install_btn.configure(text="Disable")
            if not cfg.get("service_installed"):
                cfg["service_installed"] = True
                changed = True
        elif cfg.get("service_installed"):
            self._set_status("service", ERROR)
            self.svc_status_var.set("Bluetooth Service was disabled")
            self.install_btn.configure(text="Enable")
            cfg["service_installed"] = False
            changed = True
        else:
            self._set_status("service", PENDING)
            self.svc_status_var.set("")

        dmac, dname = state.get("keyboard_mac", ""), state.get("keyboard_name", "")
        cmac = cfg.get("keyboard_mac") or ""
        if dmac and dmac.lower() != cmac.lower():
            cfg["keyboard_mac"], cfg["keyboard_name"] = dmac, dname
            self.kb_name_var.set(dname)
            changed = True
        elif dmac and dname and dname != cfg.get("keyboard_name"):
            cfg["keyboard_name"] = dname
            self.kb_name_var.set(dname)
            changed = True
        elif not dmac and cmac:
            cfg["keyboard_mac"] = cfg["keyboard_name"] = ""
            changed = True

        if cfg.get("keyboard_mac"):
            self._show_saved_view()
            if state["keyboard_connected"]:
                self._set_status("keyboard", DONE)
                self.kb_status_var.set("Connected")
            else:
                self._set_status("keyboard", RUNNING)
                self.kb_status_var.set(
                    "Paired but not connected" if state["keyboard_paired"] else "Not connected"
                )
        else:
            self._set_status("keyboard", PENDING)
            self._show_scan_view()

        native = bool(state.get("native_app_installed"))
        cfg["native_app_installed"] = native
        self._set_status("native_app", DONE if native else PENDING)
        self.native_status_var.set("Installed" if native else "")
        self.native_btn.configure(text="Uninstall" if native else "Install on device")
        if changed:
            config.save(cfg)

    def _apply_config_state(self):
        installed = bool(self.app.cfg.get("service_installed"))
        self._set_status("service", DONE if installed else PENDING)
        self.svc_status_var.set("Enabled" if installed else "")
        self.install_btn.configure(text="Disable" if installed else "Enable")
        if self.app.cfg.get("keyboard_mac"):
            self._set_status("keyboard", DONE)
            self._show_saved_view()
        else:
            self._set_status("keyboard", PENDING)
            self._show_scan_view()

    # ── native app ────────────────────────────────────────────

    def _build_native(self):
        body = self._make_section("native_app", "On-device App (Experimental)")
        self._label(
            body,
            "Install PaperWriter on the tablet (XOVI/AppLoad). Manage pair, layout, "
            "and the Bluetooth service from ☰ → AppLoad → PaperWriter — no computer "
            "needed after install.",
        )
        self._label(
            body,
            "Uses community XOVI/AppLoad (firmware-specific). Disable automatic "
            "updates on the tablet. After an OS update: Uninstall here, update, "
            "reinstall. First-time pair is smoother from this desktop app.",
            style="CardDim.TLabel",
            pady=(0, 10),
        )
        self.native_btn, self.native_status_var = self._btn_row(
            body, "Install on device", self._run_native_toggle
        )

    def _run_native_toggle(self):
        if not self._need_ssh(self.native_status_var):
            return
        installed = self.app.cfg.get("native_app_installed", False)
        if installed:
            if not messagebox.askyesno(
                "Uninstall on-device app",
                "Remove the on-device PaperWriter app and briefly restart the "
                "tablet interface. Bluetooth keyboard service is left alone. Continue?",
            ):
                return
            self._native_op(uninstall=True)
        else:
            if not messagebox.askyesno(
                "Install on-device app",
                "Installs XOVI/AppLoad pieces if needed, Python (entware), and "
                "PaperWriter on the tablet.\n\n"
                "Screen will flicker for 1–2 minutes.\n"
                "Disable automatic OS updates.\n\nContinue?",
            ):
                return
            self._native_op(uninstall=False)

    def _native_op(self, uninstall):
        if not uninstall:
            info = getattr(self.app, "device_info", None) or {}
            if info and not info.get("supports_native_app", False):
                self.native_status_var.set("On-device app not available on this device")
                return

        self._set_status("native_app", RUNNING)
        self.native_btn.configure(state="disabled")
        self.native_status_var.set("Starting...")
        self._monitor_active = False

        def work():
            cb = lambda m: self.after(0, self.native_status_var.set, m)
            if uninstall:
                native_app_installer.uninstall(self.app.ssh, status_cb=cb)
            else:
                native_app_installer.install(self.app.ssh, status_cb=cb)
            return uninstall

        def done(was_uninstall):
            if was_uninstall:
                self._set_status("native_app", PENDING)
                self.native_status_var.set("")
                self.native_btn.configure(state="normal", text="Install on device")
                self.app.cfg["native_app_installed"] = False
            else:
                self._set_status("native_app", DONE)
                self.native_status_var.set("Installed")
                self.native_btn.configure(state="normal", text="Uninstall")
                self.app.cfg["native_app_installed"] = True
            config.save(self.app.cfg)
            self.after(8000, self._start_monitor)

        def err(msg):
            self._set_status("native_app", ERROR)
            self.native_btn.configure(state="normal")
            self.native_status_var.set(f"Error: {msg[:100]}")
            self._start_monitor()

        self._bg(work, on_ok=done, on_err=err)
