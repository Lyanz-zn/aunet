# aunet

aunet adalah tool monitoring proses berbasis network bandwidth dan disk I/O (read/write).

Tool ini secara otomatis akan menghentikan proses target ketika:

- Kecepatan unduhan (network) rendah, dan
- Aktivitas disk (read/write) berada di bawah threshold
- Dalam durasi stabil yang telah ditentukan
- Diberhentikan oleh user ("ctrl + c") atau melalui bot Telegram ("/stop")

---

## 🚀 Fitur

- Monitoring bandwidth per proses
- Monitoring disk I/O (read/write)
- Auto terminate proses berdasarkan threshold & durasi
- Integrasi dengan bot Telegram (dashboard & kontrol)
- Screenshot device host via Telegram
- Action seletah selesai monitoring (opsional)

---

## 📦 Dependensi

**Pastikan python 3.x Sudah Terpasang!**

Install dependency berikut:

pip install psutil pyyaml pyTelegramBotAPI mss pygame

---

## ⚙️ Cara Menjalankan

Jalankan script sebagai Administrator (Windows) atau root (Linux):

```bash
# Windows
python3 aunet.py

# Unix
sudo python3 aunet.py
```

«⚠️ Hal ini diperlukan agar "psutil" dapat membaca koneksi jaringan dari semua proses.»

---

## 📁 Struktur File

Repository ini memiliki 2 file utama:

- "aunet.py" → file utama (main script)
- "config.yaml" → file konfigurasi (opsional)

Script tetap dapat berjalan tanpa "config.yaml".

---

## 🤖 Telegram Dashboard

Jika fitur Telegram diaktifkan, tersedia command berikut untuk kontrol dan mointoring:

- "/screenshot" → mengambil screenshot terkini dari device host yang menjalankan script
- "/stop" → menghentikan proses monitoring

---

## 🧪 Kompatibilitas

Sudah diuji pada:

- Windows 11
- Arch Linux
- CachyOS

---

## ⚠️ Catatan

- Gunakan dengan hati-hati karena tool ini dapat menghentikan proses secara otomatis
- Pastikan konfigurasi threshold sesuai kebutuhan agar tidak terjadi false trigger

---
---
