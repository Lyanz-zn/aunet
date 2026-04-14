# aunet.py

Program berbasis python untuk monitoring proses berbasis jaringan (*network bandwidth*). Alat ini akan memonitoring proses secara otomatis ketika kecepatan unduhan berada di bawah ambang batas (*threshold*) selama durasi stabil yang telah ditentukan, setelah itu lanjut memonitoring disk I/O yang digunakan proses untuk mengantisipasi ekstraksi file, setelah itu akan menghentikan proses secara otomatis.

## ✨ Fitur Utama
* **Monitoring Real-time:** Memantau penggunaan bandwidth network dan I/O disk (Read/Write) pada setiap proses.
* **Auto-kill & Post-Action:** Bisa otomatis *sleep* atau *shutdown* setelah monitoring selesai.
* **Notifikasi Telegram:** Terintegrasi dengan Bot Telegram untuk update status.
* **Kontrol melalui Telegram:** Mendukung kontrol monitoring melalui bot Telegram.
* **Sistem Peringatan:** Mendukung ringtone sebagai alarm.

## 🛠 Dependensi
Instal pustaka yang dibutuhkan melalui pip:

```bash
pip install psutil pyyaml pyTelegramBotAPI mss pygame 
