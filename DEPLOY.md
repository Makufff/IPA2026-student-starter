# การเปิด bot และอัปเดต Webex webhook

## 1. ตั้งค่า secret

```bash
cp .env.example .env   # แล้วใส่ WEBEX_BOT_TOKEN, ROUTER_USER, ROUTER_PASS
```

`.env` ถูกละไว้ใน `.gitignore` ห้าม commit

## 2. เปิด webhook และ HTTPS tunnel

```bash
docker compose --profile tunnel up -d --build
docker compose ps
curl http://127.0.0.1:8000/health      # {"status": "ok", "result": "healthy"}
```

service `tunnel` (cloudflared quick tunnel) เปิดเฉพาะเมื่อใส่ `--profile tunnel` จึงไม่กระทบ Student CI
ถ้าใช้ tunnel อื่น ให้รันแค่ `docker compose up -d webhook` แล้วชี้ tunnel ไปที่ `http://127.0.0.1:8000`

## 3. ลงทะเบียน / อัปเดต webhook ทุกครั้งที่ URL ของ tunnel เปลี่ยน

```bash
python scripts/register_webhook.py --from-tunnel                 # อ่าน URL จาก cloudflared
python scripts/register_webhook.py https://<your-tunnel-host>    # หรือระบุ URL เอง
```

สคริปต์หา room ชื่อ `IPA2026` และ `IPA2026 Exam Room` ที่ bot เป็นสมาชิก แล้วสร้างหรืออัปเดต
webhook `messages/created` หนึ่งตัวต่อห้อง ให้ชี้ไปที่ `<url>/webhook`
(เปลี่ยนชื่อห้องได้ด้วย `WEBEX_ROOM_TITLES`) หรือใส่ `--any-room` เพื่อใช้ webhook ตัวเดียวที่ไม่กรองห้อง
(bot ได้รับเฉพาะข้อความที่ mention ตัวเองอยู่แล้ว จึงใช้ได้ทันทีที่ถูกเพิ่มเข้าห้องใหม่) quick tunnel ได้ URL ใหม่ทุกครั้งที่ restart
จึงต้องรันสคริปต์นี้ใหม่หลัง `docker compose restart tunnel`

## 4. ตรวจ

@mention bot ในห้อง IPA2026 โดยไม่แนบไฟล์ ควรได้ `{"status": "error", "result": "no_yaml"}`
ดู log ได้ด้วย `docker compose logs -f webhook`
