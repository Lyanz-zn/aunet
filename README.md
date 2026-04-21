# aunet

> Auto-kill app atau game process setelah download selesai — berbasis network bandwidth + disk I/O monitoring.

Capek nungguin app atau game download dan mau ditinggal tidur atau pergi? **aunet** bakal mantau prosesnya, tunggu sampai download *dan* ekstraksi beres, lalu matiin prosesnya otomatis. Bisa juga langsung shutdown/sleep PC kamu setelahnya. Ada notifikasi Telegram-nya juga jika diaktifkan.

---

## Cara Kerjanya

```
[1] [Monitor Jaringan] ──► turun di bawah threshold N detik
          │
          ▼
[2] [Monitor Disk I/O] ──► aktivitas disk juga sudah idle N detik
          │
          ▼
[3] [Tunggu POST_MONITORING_DELAY] ──► buffer aman buat proses cleanup
          │
          ▼
[4] [Kill Proses] ──► 🔔 Alarm bunyi + notif Telegram
          │
          ▼
[5] [Post-action] ──► shutdown / sleep / exit (opsional)
```

Kenapa dua fase? Karena setelah download selesai, app atau game biasanya langsung ekstrak file — kalau langsung di-kill di sini, file bisa corrupt. Fase I/O monitoring mastiin proses dekompresi udah bener-bener kelar dulu.

---

## Fitur

| Fitur | Keterangan |
|---|---|
| 🌐 Net Monitor | Ukur kecepatan unduhan via `psutil.net_io_counters()` |
| 💿 Disk I/O Monitor | Ukur read+write per-proses via `psutil.Process.io_counters()` |
| 🔔 Alarm Ringtone | Putar file audio saat proses di-kill |
| 📸 Screenshot + Kirim | Ambil screenshot, compress via PIL, kirim ke Telegram |
| 🤖 Telegram Dashboard | Live update status + terima command `/stop` `/screenshot` |
| ⏱ Anti False-Positive | Butuh N iterasi *berturut-turut* di bawah threshold sebelum trigger |
| 💤 Post-action | Shutdown atau sleep otomatis setelah monitoring selesai |
| 📄 Config YAML | Semua setting bisa disimpan di `config.yaml` |
| 🪟 Cross-platform | Windows & Linux (Saat ini hanya support untuk Arch dan turunannya) |

---

## Download

Tersedia binary siap pakai di [Releases](../../releases/latest) — nggak perlu install Python.

| Platform | File | Isi |
|---|---|---|
| Windows (x64) | `aunet-windows-x64.zip` | `aunet.exe` + `config.yaml` |

---

## Konfigurasi

Semua setting ada di `config.yaml` yang sudah ikut di dalam zip/. Edit sesuai kebutuhan sebelum jalanin.
**aunet berjalan normal tanpa `config.yaml`**

```yaml
# ── Telegram (opsional) ──────────────────────────────────────
TELEGRAM_DASHBOARD_ENABLED: true
BOT_TOKEN: "" # Isi dengan token bot Telegram kamu (dapat dari @BotFather)
CHAT_ID: ""   # Isi dengan chat ID kamu (bisa dapat dari @userinfobot)

# ── Network Monitoring ───────────────────────────────────────
THRESHOLD_KBPS: 10          # Anggap download selesai jika di bawah ini (KB/s)
CHECK_INTERVAL: 5           # Cek setiap N detik
RETRY_ATTEMPT: 15           # Butuh N iterasi berturut-turut di bawah threshold
DURATION_STABLE: 60         # Total durasi stabil (detik, untuk display)

# ── Disk I/O Monitoring ──────────────────────────────────────
IO_THRESHOLD_KBPS: 100      # Anggap ekstraksi selesai jika di bawah ini (KB/s)
IO_CHECK_INTERVAL: 5
IO_RETRY_ATTEMPT: 12
IO_DURATION_STABLE: 60

# ── Kill & Post-action (opsional) ────────────────────────────
POST_MONITORING: "shutdown"   # "shutdown" | "sleep" | "" | null
POST_MONITORING_DELAY: 180    # Detik jeda sebelum kill (buat cleanup proses)

# ── Ringtone (opsional) ──────────────────────────────────────
RINGTONE: true  # Aktifkan alarm ringtone saat proses di-kill
RINGTONE_LOOP: "loop"  # Loop atau putar beberapa kali, value: [int, "loop"]
RINGTONE_PATH: "lib/ringtone.mp3"  # Path ke file ringtone
```

> Kalau nggak mau pake Telegram, set `TELEGRAM_DASHBOARD_ENABLED: false` atau hapus baris Telegram-nya. aunet tetap jalan normal.

---

## Usage

### Jalankan Biasa

```bash
# Binary akan minta nama proses secara interaktif
.\aunet.exe

# Atau langsung dengan post-monitoring
.\aunet.exe --post-monitoring sleep
.\aunet.exe -pm shutdown
```

```
Masukkan nama proses app atau game (contoh: StarRail.exe): SteamService.exe
```

### Flag CLI

| Flag | Singkat | Deskripsi |
|---|---|---|
| `--post-monitoring` | `-pm` | Tindakan setelah selesai: `shutdown` atau `sleep` |
| `--debug` | `-d` | Mode debug: baca `BOT_TOKEN` & `CHAT_ID` dari `.env` |

### Telegram Commands

Kalau `TELEGRAM_DASHBOARD_ENABLED: true`, kamu bisa remote aunet dari HP:

| Command | Fungsi |
|---|---|
| `/stop` | Hentikan monitoring dan keluar |
| `/screenshot` | Kirim screenshot layar sekarang (otomatis di-compress) |

---

## Build dari Source

### Kebutuhan

- Python 3.11+
- Dependencies:

```bash
pip install psutil pyyaml pyTelegramBotAPI mss Pillow pygame
```

### Jalankan Langsung

```bash
git clone https://github.com/Lyanz-zn/aunet.git
cd aunet/

python aunet.py
python aunet.py --post-monitoring shutdown
```

### Build Binary

Binary compile menggunakan Nuitka

**Windows:**

```powershell
pip install nuitka 
nuitka aunet.py --lto=yes --standalone --onefile --include-data-file="lib/ringtone.mp3"="lib/ringtone.mp3" output-file-name=aunet.py
# Output: aunet.exe
```

---

## Struktur Project

```

aunet/
├── aunet.py          # Source utama
├── config.yaml       # Konfigurasi (ikut dirilis dalam zip/tar.gz)
├── lib/ringtone.mp3  # File ringtone default
└── README.md

```

---

## Catatan Platform

| Hal | Windows | Linux |
|---|---|---|
| Net monitoring | ✅ Tanpa root | ✅ Tanpa root |
| I/O monitoring | ✅ Tanpa admin | ⚠️ Butuh root (kalau AccessDenied, fase ini di-skip otomatis) |
| Post-action shutdown | ✅ | ✅ |
| Post-action sleep | ✅ | ✅ (`systemctl sleep`) |
| Screenshot | ✅ | ✅ (butuh display aktif) |
| Ringtone alarm | ✅ | ✅ (butuh audio output) |

---
