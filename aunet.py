"""
aunet.py
==============
Monitoring proses berbasis jaringan (network bandwidth).
Menghentikan proses secara otomatis ketika kecepatan unduhan berada
di bawah threshold selama durasi stabil yang ditentukan.

Dependensi:
    pip install psutil pyyaml pyTelegramBotAPI mss pygame

Jalankan sebagai Administrator/root agar psutil bisa membaca
koneksi jaringan semua proses.
"""

import os
import sys
import time
import yaml
import threading
import argparse
import subprocess as sp
from typing import Literal

import psutil
import telebot  # pyTelegramBotAPI
import telebot.apihelper
import pygame

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

# =============================================================================
# KONFIGURASI GLOBAL
# =============================================================================


class UnsupportedOSError(Exception):
    def __init__(self) -> None:
        self.USER_OS = os.name
        super().__init__(f"[ERROR] OS '{self.USER_OS}' tidak didukung.")


ACTIVE: bool = True  # Kontroller loop utama

if os.name == "nt":
    USER_OS = "nt"
elif os.name == "posix":
    USER_OS = "posix"
else:
    raise UnsupportedOSError()

# ── Konfigurasi Telegram ─────────────────────────────────────────────────────

TELEGRAM_DASHBOARD_ENABLED: bool = False  # Diisi dari config.yaml atau --debug

BOT_TOKEN: str = ""  # Token Bot Telegram
CHAT_ID: str = ""  # Chat ID Telegram
BOT_LISTENER: bool = False  # Aktifkan listener command dari Telegram

# ── Konfigurasi Proses ───────────────────────────────────────────────────────

TARGET_PROCESS_NAME: str = ""  # Diisi saat runtime via input()

POST_MONITORING: Literal["shutdown", "sleep", None] = None
POST_MONITORING_DELAY: int = 120  # Detik; rekomendasi > 180 untuk waktu dekompresi
RINGTONE: bool = False
RINGTONE_LOOP: int | Literal["loop"] = 10  # Perilaku pemutaran ringtone
RINGTONE_PATH: str = str(
    os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "lib/ringtone.mp3")
)

THRESHOLD_KBPS: float = 100.0  # KB/s batas bawah kecepatan unduhan
CHECK_INTERVAL: int = 5  # Detik antar pengukuran
RETRY_ATTEMPT: int = 15  # Iterasi berturut-turut di bawah threshold sebelum kill
DURATION_STABLE: int = 60  # Detik total durasi stabil (dipakai untuk display)

# ── Konfigurasi Disk I/O ─────────────────────────────────────────────────────
# Dijalankan otomatis setelah monitoring jaringan selesai, sebelum proses di-kill.
# Tujuan: mendeteksi proses ekstraksi/dekompresi file setelah unduhan selesai.

IO_THRESHOLD_KBPS: float = 1000.0  # KB/s; batas bawah aktivitas disk (read+write)
IO_CHECK_INTERVAL: int = 5  # Detik antar pengukuran I/O
IO_RETRY_ATTEMPT: int = 12  # Iterasi stabil berturut-turut sebelum dianggap selesai
IO_DURATION_STABLE: int = 60  # Detik total stabil (dipakai untuk display)

DEBUGGING: bool = False

# =============================================================================
# HELPER THREAD
# =============================================================================


class AutoThread(threading.Thread):
    """Thread yang langsung berjalan saat objek dibuat (fire-and-forget)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.daemon = True
        self.start()


# =============================================================================
# UTILITAS FORMAT
# =============================================================================


def fmt_speed(bps: float) -> str:
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if bps < 1024 or unit == "GB/s":
            return f"{bps:.1f} {unit}"
        bps /= 1024


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024


def fmt_duration(seconds: int) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def clear():
    os.system("cls" if USER_OS == "nt" else "clear")


# =============================================================================
# LOAD KONFIGURASI
# =============================================================================


def load_config(path: str = "config.yaml") -> None:
    """Memuat dan memvalidasi konfigurasi dari file YAML ke variabel global."""

    config_schema: dict = {
        "BOT_TOKEN": str,
        "CHAT_ID": str,
        "TELEGRAM_DASHBOARD_ENABLED": bool,
        "THRESHOLD_KBPS": (int, float),
        "CHECK_INTERVAL": int,
        "DURATION_STABLE": int,
        "RETRY_ATTEMPT": int,
        "POST_MONITORING": str,
        "POST_MONITORING_DELAY": int,
        "IO_THRESHOLD_KBPS": (int, float),
        "IO_CHECK_INTERVAL": int,
        "IO_RETRY_ATTEMPT": int,
        "IO_DURATION_STABLE": int,
        "RINGTONE": bool,
        "RINGTONE_LOOP": int | str,
        "RINGTONE_FILE": str | None,
    }

    try:
        with open(path, "r") as f:
            config = yaml.safe_load(f) or {}

        for var, expected_type in config_schema.items():
            value = config.get(var)

            if value is None:
                continue

            if not isinstance(value, expected_type):
                print(
                    f"[SKIP] {var}: Tipe tidak cocok "
                    f"(butuh {expected_type if isinstance(expected_type, str) else getattr(expected_type, '__name__', str(expected_type))})"
                )
                continue

            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if value <= 0:
                    print(f"[SKIP] {var}: Nilai harus > 0")
                    continue

            if isinstance(value, str) and value.strip() == "":
                if var == "RINGTONE_LOOP":
                    if value != "loop":
                        print(
                            "[SKIP] Value dari  RINGTONE_LOOP hanya boleh int atau 'loop' (string)"
                        )
                        continue

                print(f"[SKIP] {var}: String tidak boleh kosong")
                continue

            globals()[var] = value

    except FileNotFoundError:
        print(f"[WARNING] File '{path}' tidak ditemukan. Menggunakan nilai default.")
    except Exception as e:
        print(f"[ERROR] Gagal memuat config: {e}")


# =============================================================================
# TELEGRAM BOT (pyTelegramBotAPI)
# =============================================================================


class TelegramBot:
    """
    Wrapper di atas pyTelegramBotAPI dengan fitur:
      - send/edit pesan teks dengan retry otomatis
      - buffer log + flush sebagai satu blok pesan
      - kirim gambar
      - listener command via long polling di thread terpisah
    """

    def __init__(self, token: str, chat_id: str) -> None:
        self.chat_id = chat_id
        self.msg_ids: dict[str, int] = {}
        self.last_texts: dict[str, str] = {}
        self.buffers: dict[str, list[str]] = {}

        self.MAX_RETRY = 3
        self.RETRY_DELAY = 2
        self.REQUEST_TIMEOUT = 10
        self.SILENT_MODE = True

        self._stop_event = threading.Event()
        self._tbot: telebot.TeleBot | None = None

        # Hanya inisialisasi bot jika token dan chat_id tersedia
        if token and chat_id:
            self._tbot = telebot.TeleBot(token, parse_mode=None)

    # ── Guard ────────────────────────────────────────────────────────────────

    def _is_ready(self) -> bool:
        return self._tbot is not None and TELEGRAM_DASHBOARD_ENABLED

    # ── Buffer Log ───────────────────────────────────────────────────────────

    def add_log(self, text: str, key: str = "log") -> None:
        if not self._is_ready():
            return
        if key not in self.buffers:
            self.buffers[key] = []
        self.buffers[key].append(text)

    def flush(self, key: str = "log", title: str = "Dashboard Update") -> None:
        """Kirim semua log yang ada di buffer[key] sebagai satu blok pesan."""
        if not self._is_ready():
            return
        if key not in self.buffers or not self.buffers[key]:
            return
        content = "\n".join(self.buffers[key])
        full_msg = f"*@ {title}*\n```\n{content}\n```"
        self.send_text(full_msg, key=key, edit=True)
        self.buffers[key] = []

    # ── Kirim / Edit Teks ────────────────────────────────────────────────────

    def send_text(self, msg: str, key: str = "default", edit: bool = False) -> bool:
        if not self._is_ready():
            return False

        current_msg_id = self.msg_ids.get(key)
        should_edit = edit and current_msg_id is not None

        # Jangan kirim ulang jika pesan identik
        if should_edit and msg == self.last_texts.get(key):
            return True

        for attempt in range(self.MAX_RETRY):
            try:
                if should_edit:
                    result = self._tbot.edit_message_text(
                        msg,
                        chat_id=self.chat_id,
                        message_id=current_msg_id,
                        parse_mode="Markdown",
                    )
                else:
                    result = self._tbot.send_message(
                        self.chat_id, msg, parse_mode="Markdown"
                    )

                self.msg_ids[key] = result.message_id
                self.last_texts[key] = msg
                return True

            except telebot.apihelper.ApiTelegramException as e:
                desc = str(e).lower()
                if (
                    "message to edit not found" in desc
                    or "message can't be edited" in desc
                ):
                    # Pesan target sudah tidak bisa diedit; kirim baru saja
                    self.msg_ids[key] = None
                    return self.send_text(msg, key=key, edit=False)
                if not self.SILENT_MODE:
                    print(f"[TELEGRAM RETRY {attempt + 1}] ApiError: {e}")

            except Exception as e:
                if not self.SILENT_MODE:
                    print(f"[TELEGRAM RETRY {attempt + 1}] Error: {e}")

            time.sleep(self.RETRY_DELAY)

        return False

    # ── Kirim Gambar ─────────────────────────────────────────────────────────

    def send_image(self, photo_path: str, caption: str | None = None) -> bool:
        """
        Kirim gambar ke chat. Bersifat blocking — menunggu hingga berhasil
        atau semua retry habis.

        Returns:
            True jika berhasil, False jika semua retry gagal.
        """
        if not self._is_ready():
            return False

        for attempt in range(self.MAX_RETRY):
            try:
                with open(photo_path, "rb") as photo:
                    self._tbot.send_photo(
                        self.chat_id,
                        photo,
                        caption=caption,
                        parse_mode="Markdown",
                    )
                return True

            except telebot.apihelper.ApiTelegramException as e:
                if not self.SILENT_MODE:
                    print(f"[TELEGRAM RETRY {attempt + 1}] Photo ApiError: {e}")
            except FileNotFoundError:
                print(f"[ERROR] File gambar tidak ditemukan: {photo_path}")
                return False
            except Exception as e:
                if not self.SILENT_MODE:
                    print(f"[TELEGRAM RETRY {attempt + 1}] Photo Error: {e}")

            time.sleep(self.RETRY_DELAY)

        print("[ERROR] Gagal mengirim gambar setelah semua retry.")
        return False

    # ── Listener Command ─────────────────────────────────────────────────────

    def start_listener(self, callback_func) -> threading.Thread | None:
        """
        Mulai listener command Telegram di thread terpisah menggunakan
        infinity_polling dari pyTelegramBotAPI.

        pyTelegramBotAPI mengoper objek telebot.types.Message ke handler,
        bukan dict — callback_func harus menerima Message object.
        """
        if not self._is_ready():
            return None

        @self._tbot.message_handler(func=lambda m: True)
        def _handle(message: telebot.types.Message):
            callback_func(message)

        def _polling_loop():
            while not self._stop_event.is_set():
                try:
                    # skip_pending=True: abaikan pesan sebelum bot dijalankan
                    self._tbot.infinity_polling(
                        timeout=20,
                        long_polling_timeout=20,
                        skip_pending=True,
                    )
                except Exception as e:
                    if not self.SILENT_MODE:
                        print(f"[LISTENER ERROR] {e}")
                    time.sleep(5)

        thread = threading.Thread(target=_polling_loop, daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        """Hentikan polling listener secara aman."""
        self._stop_event.set()
        if self._tbot:
            try:
                self._tbot.stop_polling()
            except Exception:
                pass


# =============================================================================
# SCREENSHOT
# =============================================================================


class Screenshot:
    """Ambil screenshot layar utama dan simpan ke temp OS."""

    def __init__(self, prefix: str, ext: str) -> None:
        self.prefix = prefix
        self.ext = ext.lstrip(".").lower()

    def get_screenshot(self) -> str:
        """
        Ambil screenshot dan kembalikan path file.

        Returns:
            Path lengkap ke file PNG yang disimpan.
        """
        import mss
        import mss.tools
        import tempfile

        temp_dir = tempfile.gettempdir()
        path = self._unique_path(temp_dir)

        with mss.mss() as sct:
            img = sct.grab(sct.monitors[1])
            mss.tools.to_png(img.rgb, img.size, output=path)

        return path

    def _unique_path(self, directory: str) -> str:
        base = os.path.join(directory, f"{self.prefix}.{self.ext}")
        if not os.path.exists(base):
            return base
        i = 1
        while True:
            candidate = os.path.join(directory, f"{self.prefix}({i}).{self.ext}")
            if not os.path.exists(candidate):
                return candidate
            i += 1


# =============================================================================
# TELEGRAM COMMAND HANDLER
# =============================================================================

# Variabel ini diisi di main() setelah bot dan ss diinisialisasi
bot: TelegramBot
ss: Screenshot
key_status_update: str = "status"  # Diakses dari monitor() dan bot_handler()


def bot_handler(message: telebot.types.Message) -> None:
    """
    Handler untuk pesan masuk dari Telegram.
    Menerima telebot.types.Message (bukan dict).
    """
    global ACTIVE, key_status_update

    text = (message.text or "").strip().lower()

    if text == "/stop":
        print("\n[TELEGRAM] Perintah /stop diterima.")
        ACTIVE = False
        bot.stop()

    elif text == "/screenshot":
        try:
            path = ss.get_screenshot()
            caption = f"@ Screenshot pukul {time.strftime('%H:%M:%S')}"

            # Kirim gambar secara blocking — tunggu hingga selesai (berhasil/error)
            success = bot.send_image(path, caption=caption)

            if success:
                # Gambar sudah terkirim, pesan status sebelumnya sudah tertimbun.
                # Rotasi key agar monitor() mengirim pesan baru (bukan edit yang terkubur).
                key_status_update = f"status_after_ss_{int(time.time())}"
            else:
                print("[TELEGRAM] Gagal mengirim screenshot.")

        except Exception as e:
            print(f"[TELEGRAM] Error saat screenshot: {e}")


# =============================================================================
# MANAJEMEN PROSES
# =============================================================================


def find_procs(name: str) -> list[psutil.Process]:
    name_lower = name.lower()
    result = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if name_lower in p.info["name"].lower():
                result.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return result


def count_connections(pids: list[int]) -> int:
    pid_set = set(pids)
    count = 0
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.pid in pid_set and conn.raddr:
                count += 1
    except psutil.AccessDenied:
        pass
    return count


def kill_procs(procs: list[psutil.Process]) -> None:
    """Hentikan proses dengan delay agar file punya waktu dekompresi."""
    try:
        remaining = POST_MONITORING_DELAY
        while remaining >= 0:
            print(
                f"\r[INFO] Proses di-kill dalam {remaining // 60:02d}:{remaining % 60:02d}.",
                end="",
                flush=True,
            )
            time.sleep(1)
            remaining -= 1
    except KeyboardInterrupt:
        print("\n[WARN] Kill dibatalkan via Ctrl+C. Proses masih berjalan.")
        sys.exit(0)

    print()

    for proc in procs:
        try:
            name, pid = proc.name(), proc.pid
            print(f"[ACTION] Menghentikan '{name}' (PID {pid})...", end=" ")
            proc.terminate()
            proc.wait(timeout=5)
            print("OK (terminate)")
        except psutil.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=3)
                print("OK (force kill)")
            except psutil.NoSuchProcess:
                print("sudah berhenti sendiri")
        except psutil.NoSuchProcess:
            print("sudah tidak ada")
        except psutil.AccessDenied:
            print("GAGAL — jalankan sebagai Administrator/root")

    time.sleep(1)


# =============================================================================
# POST-MONITORING ACTION
# =============================================================================


class PostMonitoringAction:
    def __init__(self) -> None:
        self.delay = max(POST_MONITORING_DELAY, 60)

    def shutdown(self) -> None:
        try:
            if USER_OS == "nt":
                sp.run(["shutdown", "/S", "/T", str(self.delay)], check=True)
            else:
                delay_minutes = max(1, -(-self.delay // 60))
                sp.run(["shutdown", f"+{delay_minutes}"], check=True)
            print("[INFO] Shutdown dijadwalkan.")
        except sp.CalledProcessError as e:
            print(f"[ERROR] Gagal menjadwalkan shutdown: {e}")

    def do_sleep(self) -> None:
        try:
            print("[INFO] Masuk ke mode sleep...")
            if USER_OS == "nt":
                sp.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,0,0"])
            else:
                sp.run(["systemctl", "sleep"])
        except KeyboardInterrupt:
            print("\n[INFO] Sleep dibatalkan.")


# =============================================================================
# LOOP MONITORING UTAMA
# =============================================================================


def monitor_io(procs: list[psutil.Process]) -> None:
    """
    Monitoring aktivitas Disk I/O per-proses menggunakan psutil.Process.io_counters().
    Dipanggil secara otomatis setelah monitoring jaringan selesai, sebagai
    konfirmasi bahwa proses ekstraksi/dekompresi file juga sudah tuntas.

    Setelah kondisi stabil terpenuhi, fungsi ini memanggil kill_procs()
    yang di dalamnya sudah menyertakan POST_MONITORING_DELAY.

    Catatan platform:
      - Windows  : io_counters() tersedia tanpa hak khusus.
      - Linux    : membutuhkan root; jika AccessDenied akan skip proses tersebut.
      - macOS    : io_counters() tersedia tapi tidak semua field terisi.

    Args:
        procs: List psutil.Process yang dipantau (diwarisi dari monitor()).
    """
    global key_status_update

    pids = [p.pid for p in procs]
    names = ", ".join(sorted({p.name() for p in procs}))
    threshold_bps = IO_THRESHOLD_KBPS * 1024

    # ── Cek ketersediaan io_counters() sebelum masuk loop ────────────────────
    io_available = False
    for p in procs:
        try:
            p.io_counters()
            io_available = True
            break
        except (psutil.AccessDenied, AttributeError):
            pass

    if not io_available:
        print()
        print("[WARNING] io_counters() tidak tersedia (AccessDenied / platform).")
        print("[WARNING] Melewati fase monitoring I/O — langsung ke kill.")
        kill_procs(procs)
        return

    # ── Header ───────────────────────────────────────────────────────────────
    print()
    print("╔" + "═" * 62 + "╗")
    print(f"║  {'Disk I/O Monitor':^58}  ║")
    print("╚" + "═" * 62 + "╝")

    io_info = [
        f"[💿] Proses        : {names}",
        f"[💿] PID           : {', '.join(str(p) for p in pids)}",
        f"[💿] Threshold     : {IO_THRESHOLD_KBPS} KB/s (read+write)",
        f"[💿] Interval      : {IO_CHECK_INTERVAL}s",
        f"[💿] Durasi stabil : {fmt_duration(IO_DURATION_STABLE)} ({IO_RETRY_ATTEMPT}x iterasi)",
    ]
    for info in io_info:
        print(info)
        bot.add_log(info, key="io_start")

    AutoThread(target=bot.flush, args=("io_start", "Disk I/O Monitor"))

    print("╔" + "═" * 62 + "╗")
    print(
        f"║  {'Waktu':^8}  {'Read':^13}  {'Write':^13}  {'Total':^12}  {'Status':^7}  ║"
    )
    print("╠" + "═" * 62 + "╣")

    # ── Snapshot awal — ambil io_counters semua proses ───────────────────────
    def _snapshot(proc_list: list[psutil.Process]) -> dict[int, tuple[int, int]]:
        """
        Kembalikan {pid: (read_bytes, write_bytes)} untuk proses yang tersedia.
        Proses yang NoSuchProcess / AccessDenied di-skip.
        """
        result = {}
        for p in proc_list:
            try:
                c = p.io_counters()
                result[p.pid] = (c.read_bytes, c.write_bytes)
            except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
                pass
        return result

    prev_snap = _snapshot(procs)
    prev_time = time.monotonic()
    time.sleep(IO_CHECK_INTERVAL)

    total_read = 0
    total_write = 0
    stable_count = 0
    iteration = 0
    key_io = "io_status"

    try:
        while ACTIVE:
            iteration += 1

            # 1. Cek proses masih hidup
            alive = [p for p in procs if _proc_alive(p)]
            if not alive:
                print()
                print(
                    f"║  {time.strftime('%H:%M:%S'):^8}  {'—':^13}  {'—':^13}  "
                    f"{'—':^12}  {'Berhenti':^7}  ║"
                )
                print("╠" + "═" * 62 + "╣")
                print(
                    "║  [INFO] Proses berhenti saat monitoring I/O.                 ║"
                )
                return

            procs = alive

            # 2. Snapshot sekarang + hitung delta
            curr_snap = _snapshot(procs)
            curr_time = time.monotonic()
            dt = curr_time - prev_time

            d_read = d_write = 0
            for pid, (cr, cw) in curr_snap.items():
                if pid in prev_snap:
                    pr, pw = prev_snap[pid]
                    d_read += max(0, cr - pr)
                    d_write += max(0, cw - pw)

            speed_read = d_read / dt
            speed_write = d_write / dt
            speed_total = speed_read + speed_write

            total_read += d_read
            total_write += d_write
            prev_snap = curr_snap
            prev_time = curr_time

            # 3. Evaluasi threshold (berdasarkan total read+write)
            if speed_total < threshold_bps:
                stable_count += 1
                status = (
                    f"{stable_count}/{IO_RETRY_ATTEMPT}"
                    if stable_count < IO_RETRY_ATTEMPT
                    else "SELESAI"
                )
            else:
                stable_count = 0
                status = "Aktif"

            # 4. Cetak baris status
            ts = time.strftime("%H:%M:%S")
            read_str = fmt_speed(speed_read)
            write_str = fmt_speed(speed_write)
            total_str = fmt_speed(speed_total)

            print(
                f"\r║  {ts:^8}  {read_str:^13}  {write_str:^13}  {total_str:^12}  {status:^7}  ║",
                end="",
                flush=True,
            )

            # 5. Push ke Telegram
            bot.add_log(f"[💿] Time    : {ts}", key=key_io)
            bot.add_log(f"[💿] Read    : {read_str}", key=key_io)
            bot.add_log(f"[💿] Write   : {write_str}", key=key_io)
            bot.add_log(f"[💿] Total   : {total_str}", key=key_io)
            bot.add_log(f"[💿] Status  : {status}", key=key_io)
            AutoThread(target=bot.flush, args=(key_io, "Disk I/O Update"))

            # 6. Trigger kill jika stabil cukup lama
            if stable_count >= IO_RETRY_ATTEMPT:
                print()
                print("╚" + "═" * 62 + "╝")
                print(
                    f"[TRIGGER] Disk I/O stabil di bawah {IO_THRESHOLD_KBPS} KB/s "
                    f"selama ≥ {fmt_duration(IO_DURATION_STABLE)}"
                )

                # Ringkasan I/O sebelum kill
                io_summary = [
                    f"[💿] Total read    : {fmt_bytes(total_read)}",
                    f"[💿] Total write   : {fmt_bytes(total_write)}",
                    f"[💿] Total iterasi : {iteration}",
                ]
                for s in io_summary:
                    print(s)
                    bot.add_log(s, key="io_summary")
                AutoThread(target=bot.flush, args=("io_summary", "Disk I/O Summary"))

                # Masuk ke delay + kill (POST_MONITORING_DELAY ada di sini)
                kill_procs(procs)
                return

            time.sleep(IO_CHECK_INTERVAL)

    except KeyboardInterrupt:
        print()
        print("╠" + "═" * 62 + "╣")
        print("║  [INFO] Monitoring I/O dihentikan oleh user (Ctrl+C).        ║")
        sys.exit(0)


def monitor(procs: list[psutil.Process]) -> None:
    """
    Loop utama: ukur bandwidth tiap CHECK_INTERVAL detik.
    Kill proses jika kecepatan konsisten di bawah THRESHOLD_KBPS
    selama RETRY_ATTEMPT iterasi berturut-turut.
    """
    global key_status_update

    pids = [p.pid for p in procs]
    names = ", ".join(sorted({p.name() for p in procs}))
    threshold_bps = THRESHOLD_KBPS * 1024

    # ── Header ──────────────────────────────────────────────────────────────
    print()
    print("╔" + "═" * 62 + "╗")
    print(f"║  {'App Network Monitor':^58}  ║")
    print("╚" + "═" * 62 + "╝")

    start_info = [
        f"[✨] Proses        : {names}",
        f"[✨] PID           : {', '.join(str(p) for p in pids)}",
        f"[✨] Threshold     : {THRESHOLD_KBPS} KB/s",
        f"[✨] Interval      : {CHECK_INTERVAL}s",
        f"[✨] Durasi stabil : {fmt_duration(DURATION_STABLE)} ({RETRY_ATTEMPT}x iterasi)",
        f"[✨] Retry attempt : {RETRY_ATTEMPT}",
    ]
    for info in start_info:
        print(info)
        bot.add_log(info, key="start_info")

    AutoThread(target=bot.flush, args=("start_info", "Info"))

    print("╔" + "═" * 62 + "╗")
    print(f"║  {'Waktu':^8}  {'Download':^14}  {'Koneksi':^8}  {'Status':^22}  ║")
    print("╠" + "═" * 62 + "╣")

    # ── Snapshot awal (dibuang — selisih pertama tidak akurat) ───────────────
    prev = psutil.net_io_counters()
    prev_time = time.monotonic()
    time.sleep(CHECK_INTERVAL)

    total_recv = 0
    stable_count = 0
    iteration = 0

    if BOT_LISTENER:
        bot.start_listener(bot_handler)

    try:
        while ACTIVE:
            iteration += 1

            # 1. Cek proses masih hidup
            alive = [p for p in procs if _proc_alive(p)]

            if not alive:
                print()
                print(
                    f"║  {time.strftime('%H:%M:%S'):^8}  {'—':^14}  {'—':^8}  "
                    f"{'Proses berhenti':^22}  ║"
                )
                print("╠" + "═" * 62 + "╣")
                print(
                    "║  [INFO] Proses tidak berjalan lagi. Monitoring selesai.      ║"
                )
                break

            procs = alive
            pids = [p.pid for p in procs]

            # 2. Ukur bandwidth
            curr = psutil.net_io_counters()
            curr_time = time.monotonic()
            dt = curr_time - prev_time

            d_recv = max(0, curr.bytes_recv - prev.bytes_recv)
            speed_down = d_recv / dt

            total_recv += d_recv
            prev, prev_time = curr, curr_time

            # 3. Koneksi aktif
            conn_count = count_connections(pids)

            # 4. Evaluasi threshold
            if speed_down < threshold_bps:
                stable_count += 1
                status = (
                    f"LOW {stable_count}/{RETRY_ATTEMPT}"
                    if stable_count < RETRY_ATTEMPT
                    else "KILL TRIGGER"
                )
            else:
                stable_count = 0
                status = "Aktif"

            # 5. Cetak baris status (overwrite baris yang sama)
            ts = time.strftime("%H:%M:%S")
            speed_str = fmt_speed(speed_down)
            conn_str = f"{conn_count} aktif"

            print(
                f"\r║  {ts:^8}  {speed_str:^14}  {conn_str:^8}  {status:^22}  ║",
                end="",
                flush=True,
            )

            # 6. Push ke Telegram (pakai key terkini agar tepat sasaran)
            bot.add_log(f"[✨] Time        : {ts}", key=key_status_update)
            bot.add_log(f"[✨] Download    : {speed_str}", key=key_status_update)
            bot.add_log(f"[✨] Connections : {conn_str}", key=key_status_update)
            bot.add_log(f"[✨] Status      : {status}", key=key_status_update)
            AutoThread(target=bot.flush, args=(key_status_update, "Status Update"))

            # 7. Lanjut ke fase I/O jika net threshold sudah konsisten terpenuhi
            if stable_count >= RETRY_ATTEMPT:
                print()
                print("╚" + "═" * 62 + "╝")
                print(
                    f"[TRIGGER] Internet stabil di bawah {THRESHOLD_KBPS} KB/s "
                    f"selama ≥ {fmt_duration(DURATION_STABLE)}"
                )
                print("[INFO] Melanjutkan ke fase monitoring Disk I/O...")
                monitor_io(procs)
                break

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        print()
        print("╠" + "═" * 62 + "╣")
        print("║  [INFO] Monitoring dihentikan oleh user (Ctrl+C).            ║")
        _finalize(total_recv, iteration)
        sys.exit(0)

    # ── Screenshot akhir ─────────────────────────────────────────────────────
    if TELEGRAM_DASHBOARD_ENABLED:
        bot.send_image(
            ss.get_screenshot(),
            caption=f"@ Screenshot terakhir ({time.strftime('%H:%M:%S')})",
        )

    _finalize(total_recv, iteration)

    # ── Putar ringtone jika diaktifkan ───────────────────────────────────────
    if RINGTONE:
        if os.path.exists(RINGTONE_PATH):
            player = AudioPlayer()
            try:
                print("[INFO] Memutar ringtone...")
                print("[INFO] Tekan Ctrl+C untuk menghentikan ringtone.")
                if isinstance(RINGTONE_LOOP, int) and RINGTONE_LOOP > 0:
                    att = RINGTONE_LOOP
                    while att >= 1:
                        player.play(file_path=RINGTONE_PATH, block=True)
                        att -= 1
                else:
                    while True:
                        player.play(file_path=RINGTONE_PATH, block=True)

            except KeyboardInterrupt:
                print("[INFO] Ringtone dihentikan.")
                player.stop()
        else:
            print(f"[ERROR] File audio {RINGTONE_PATH} tidak ditemukan")

    # ── Tindakan setelah monitoring selesai ─────────────────────────────────
    pma = PostMonitoringAction()
    if POST_MONITORING == "shutdown":
        pma.shutdown()
    elif POST_MONITORING == "sleep":
        pma.do_sleep()


def _proc_alive(p: psutil.Process) -> bool:
    """Cek apakah proses masih hidup dan bukan zombie."""
    try:
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _finalize(total_recv: float, iteration: int) -> None:
    """Cetak dan kirim ringkasan akhir."""
    ts = time.strftime("%H:%M:%S")
    rows = [
        "╔" + "═" * 62 + "╗",
        f"║  Total terima   : {fmt_bytes(total_recv):<43}║",
        f"║  Total iterasi  : {iteration:<43}║",
        f"║  Selesai pada   : {ts:<43}║",
        "╚" + "═" * 62 + "╝",
    ]
    for r in rows:
        print(r)

    summary = [
        "[INFO] Monitoring selesai.",
        f"[INFO] Total data diterima : {fmt_bytes(total_recv)}",
        f"[INFO] Total iterasi       : {iteration}",
        f"[INFO] Selesai pada        : {ts}",
    ]
    for s in summary:
        bot.add_log(s, key="footer")

    bot.flush(key="footer", title="Summary")


class AudioPlayer:
    """
    Pemutar audio sederhana menggunakan pygame.mixer.
    """

    def __init__(self):
        self._initialized = False
        self._lock = threading.Lock()

    def _init_mixer(self):
        if not self._initialized:
            pygame.mixer.init()
            self._initialized = True

    def _play_internal(self, file_path: str):
        with self._lock:
            self._init_mixer()
            pygame.mixer.music.load(file_path)
            pygame.mixer.music.play()

        # tunggu sampai selesai
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)

    def play(self, file_path: str, block: bool = True):
        """
        Play audio
        :param file_path: path file audio
        :param block: True = blocking, False = thread
        """
        if block:
            self._play_internal(file_path)
        else:
            t = threading.Thread(
                target=self._play_internal, args=(file_path,), daemon=True
            )
            t.start()

    def stop(self):
        """Stop audio kapan saja"""
        if self._initialized:
            pygame.mixer.music.stop()


# =============================================================================
# ENTRY POINT
# =============================================================================


def main() -> None:
    global TARGET_PROCESS_NAME, DEBUGGING
    global TELEGRAM_DASHBOARD_ENABLED, BOT_TOKEN, CHAT_ID, BOT_LISTENER
    global POST_MONITORING
    global bot, ss

    # ── Parse argumen CLI ────────────────────────────────────────────────────
    parser = argparse.ArgumentParser(
        description="Monitor internet proses; kill otomatis jika kondisi terpenuhi."
    )
    parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Mode debugging: muat BOT_TOKEN dan CHAT_ID dari .env",
    )
    parser.add_argument(
        "--post-monitoring",
        "-pm",
        type=str,
        choices=["shutdown", "sleep"],
        default=None,
        help="Tindakan device setelah monitoring selesai",
    )
    args = parser.parse_args()

    clear()

    header = [
        "╔" + "═" * 62 + "╗",
        f"║  {'App Network Monitor — Auto Kill Edition':^58}  ║",
        "╚" + "═" * 62 + "╝",
    ]
    for h in header:
        print(h)

    # ── Load konfigurasi YAML ────────────────────────────────────────────────
    load_config()

    # ── Mode debug: ambil token dari .env ────────────────────────────────────
    if args.debug:
        from dotenv import load_dotenv

        load_dotenv()
        BOT_TOKEN = os.getenv("BOT_TOKEN", "")
        CHAT_ID = os.getenv("CHAT_ID", "")
        BOT_LISTENER = True
        DEBUGGING = True
        print("[DEBUG] Mode debugging diaktifkan.")

    # ── Override POST_MONITORING dari CLI ────────────────────────────────────
    if args.post_monitoring:
        POST_MONITORING = args.post_monitoring

    # ── Validasi Telegram ────────────────────────────────────────────────────
    if TELEGRAM_DASHBOARD_ENABLED and (not BOT_TOKEN or not CHAT_ID):
        print("[WARNING] BOT_TOKEN atau CHAT_ID kosong. Fitur Telegram dinonaktifkan.")
        TELEGRAM_DASHBOARD_ENABLED = False

    # ── Inisialisasi bot (sekali, setelah semua config siap) ─────────────────
    bot = TelegramBot(BOT_TOKEN, CHAT_ID)

    if TELEGRAM_DASHBOARD_ENABLED:
        ss = Screenshot("aunet-ss", ".png")
        print("[INFO] Telegram Dashboard diaktifkan.")

    # ── Input nama proses ────────────────────────────────────────────────────
    if not TARGET_PROCESS_NAME:
        TARGET_PROCESS_NAME = input(
            "\nMasukkan nama proses game (contoh: StarRail.exe): "
        ).strip()

    if not TARGET_PROCESS_NAME:
        print("[ERROR] Nama proses tidak boleh kosong. Program dihentikan.")
        sys.exit(1)

    procs = find_procs(TARGET_PROCESS_NAME)

    if not procs:
        print(f"\n[ERROR] Proses '{TARGET_PROCESS_NAME}' tidak ditemukan.")
        print("        Pastikan game sudah berjalan sebelum menjalankan script ini.\n")
        sys.exit(1)

    clear()
    for h in header:
        print(h)

    # ── Pesan startup ────────────────────────────────────────────────────────
    startup_msgs = [
        "[INFO] Game Network Monitor dimulai!",
        f"[INFO] Memantau  : {', '.join(p.name() for p in procs)}",
        f"[INFO] [NET] Threshold : {THRESHOLD_KBPS} KB/s | Interval : {CHECK_INTERVAL}s | Stabil : {fmt_duration(DURATION_STABLE)} ({RETRY_ATTEMPT}x)",
        f"[INFO] [I/O] Threshold : {IO_THRESHOLD_KBPS} KB/s | Interval : {IO_CHECK_INTERVAL}s | Stabil : {fmt_duration(IO_DURATION_STABLE)} ({IO_RETRY_ATTEMPT}x)",
        f"[INFO] Kill delay : {fmt_duration(POST_MONITORING_DELAY)} setelah I/O selesai",
    ]
    if POST_MONITORING:
        startup_msgs.append(f"[INFO] Post-action: {POST_MONITORING}")

    for m in startup_msgs:
        print(m)
        bot.add_log(m, key="startup")

    if TELEGRAM_DASHBOARD_ENABLED:
        AutoThread(target=bot.flush, args=("startup", "Monitor Started"))
        time.sleep(0.5)  # Beri jeda agar flush pertama tidak nabrak flush berikutnya

    # ── Mulai monitoring ─────────────────────────────────────────────────────
    try:
        monitor(procs)
    except Exception as e:
        print(f"\n[ERROR] Kesalahan tidak terduga: {e}")
        bot.stop()
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        try:
            bot.stop()
        except NameError:
            pass
        print("\n[INFO] Program dihentikan oleh user (Ctrl+C).")
        sys.exit(0)
    except Exception as e:
        print(f"\n[ERROR] Kesalahan tidak terduga di main: {e}")
        try:
            bot.stop()
        except NameError:
            pass
        sys.exit(1)
