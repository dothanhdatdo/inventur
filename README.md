# Mai Wok Inventur – Quản lý kho nhà hàng (Python)

Ứng dụng web quản lý kho cho nhà hàng: **quét hoá đơn nhập hàng bằng AI**, nhập kho thủ công, theo dõi tồn kho theo lô (FIFO/FEFO, hạn sử dụng), **cảnh báo hàng sắp hết qua email**, định lượng món (BOM), kiểm kê (Inventur) và báo cáo hao hụt / food cost / biến động giá.

Viết bằng **Python** (FastAPI + SQLAlchemy + Jinja2). Cùng một mã nguồn chạy được theo 2 cách:

| | Bản GitHub Pages (trong trình duyệt) | Bản server |
|---|---|---|
| Cách chạy | Mở link, không cần cài gì. Python chạy ngay trong trình duyệt nhờ [Pyodide](https://pyodide.org) | `uvicorn` trên máy bạn, Render, Railway, Docker… |
| Link | <https://dothanhdatdo.github.io/inventur/> | tuỳ nơi deploy |
| Dữ liệu | Lưu trong trình duyệt của thiết bị đó (IndexedDB). Có nút sao lưu/khôi phục `.db` | SQLite trên server, dùng chung cho mọi thiết bị |
| Email cảnh báo | Qua dịch vụ miễn phí FormSubmit.co (lần đầu phải bấm link kích hoạt trong email) | SMTP (vd. Gmail + App Password) |
| AI đọc hoá đơn | Nhập API key Gemini (miễn phí), Claude hoặc OpenAI ở trang **Cài đặt** (key chỉ lưu trên trình duyệt đó) | Biến môi trường `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` |

**Không cần API key** cho hoá đơn PDF gửi qua email của METRO: app đọc chữ trong PDF ngay trên máy (thư viện `pypdf`), miễn phí và chính xác từng dòng. Ảnh chụp hoá đơn giấy thì cần AI: lấy key Gemini miễn phí (xem bên dưới); chưa có key thì ảnh vẫn được lưu kèm phiếu để gõ tay các dòng. Muốn thử quy trình bằng dữ liệu giả: chọn **Demo** ở Cài đặt rồi tải [ảnh hoá đơn mẫu](app/static/sample-invoice.png).

## Tính năng

- **Hai khu kho: 🍳 Bếp (Küche) và 🍹 Quầy (Theke – đồ uống)**
  - Mỗi nguyên liệu và mỗi món/đồ uống thuộc một khu. Nút chuyển **Tất cả / Bếp / Quầy** ở thanh bên lọc mọi trang: tồn kho, kiểm kê, ngưỡng, bán hàng, báo cáo, đặt hàng.
  - Trang tổng quan khi xem "Tất cả" có thẻ riêng cho từng khu: giá trị tồn, số hàng dưới ngưỡng, sắp hết hạn, doanh thu, **food cost** (bếp) / **beverage cost** (quầy), hao hụt.
  - **Kiểm kê (Inventur) riêng từng khu**, có bản in phiếu đếm (ngày, người đếm, đơn vị và quy đổi thùng/két).
  - Hoá đơn có cả đồ ăn lẫn đồ uống (vd. Großmarkt) tự **chia tiền theo khu**. Hàng mới từ hoá đơn được đoán khu theo tên (Bier, Wein, Saft, Wasser, Cola…) và VAT (ở Đức đồ uống 19 %, thực phẩm 7 %); sửa lại được ở trang nguyên liệu.
  - **Email cảnh báo theo khu**: có thể đặt email riêng cho bếp trưởng và cho quầy bar; trống thì gửi về email chung.
  - Dữ liệu từ bản cũ (đã lưu trong trình duyệt) được **tự chuyển sang dạng mới** khi mở app: đồ uống sang Quầy, còn lại sang Bếp.
- **Cần đặt hàng**: gợi ý theo ngưỡng tối thiểu (gấp) và **mức tồn chuẩn** (Par-Bestand), làm tròn theo thùng/két/bao, gom theo nhà cung cấp, kèm **tin nhắn đặt hàng tiếng Đức** để copy gửi đi.
- **Nhập hàng**
  - 🧾 *Hoá đơn PDF METRO* (từ email): đọc không cần AI – từng dòng hàng, số cái/thùng, giá NET sau Mengenrabatt, hạn dùng (MHD), nhóm hàng (Bier/AfG, Wein, Fleisch, Nonfood…) để đoán khu Bếp/Quầy; tiền cọc vỏ (Leergut) không tính vào kho. Quét lại hoá đơn đã nhập thì các dòng trùng được bỏ sẵn.
  - 📷 *Ảnh chụp hoá đơn*: chụp bằng điện thoại hoặc tải JPG, PNG, PDF khác → AI (Google Gemini miễn phí, Claude hoặc GPT-4o) bóc tách nhà cung cấp, số và ngày hoá đơn, mặt hàng, số lượng, đơn giá, thành tiền, VAT.
  - Màn hình *đối soát*: ảnh hoá đơn bên trái, dữ liệu bên phải để sửa, gắn nguyên liệu, quy đổi đơn vị (vd. 1 bao = 18 kg, 1 thùng = 24 lon), nhập hạn sử dụng, rồi bấm **Nhập kho**.
  - Tự khớp tên hàng trên hoá đơn với nguyên liệu trong kho (hiểu cả từ ghép tiếng Đức) và **ghi nhớ mapping** theo từng nhà cung cấp cho lần quét sau.
  - ✍️ *Nhập thủ công*: tạo phiếu nhập gõ tay, hoặc nhập nhanh từng nguyên liệu.
- **Tồn kho**: theo lô, xuất kho theo hạn dùng sớm nhất (FEFO/FIFO), ghi hao hụt, huỷ lô quá hạn, nhật ký xuất nhập.
- **Ngưỡng tồn & cảnh báo**: ngưỡng tối thiểu và mức tồn chuẩn cho từng nguyên liệu (trang *Cài đặt & cảnh báo*). Khi tồn xuống dưới ngưỡng, app **tự gửi email**. Mỗi nguyên liệu chỉ báo một lần cho tới khi nhập lại lên trên ngưỡng. Có nút “Gửi báo cáo ngay”.
- **Định lượng món (BOM)** và **bán hàng**: món bếp trừ kho Bếp, đồ uống trừ kho Quầy (vd. Weißwein 0,2 l), tính giá vốn và cost %.
- **Báo cáo**: bảng Bếp/Quầy (doanh thu, giá vốn, cost %, tiền nhập, hao hụt, tồn kho), biểu đồ doanh thu theo ngày tách khu, giá trị tồn theo nhóm hàng, biến động giá nhập theo nhà cung cấp.

## AI đọc hoá đơn miễn phí (Google Gemini)

1. Mở <https://aistudio.google.com/apikey>, đăng nhập Google, bấm **Create API key**, copy key (`AIza…`).
2. Trong app: **Cài đặt & cảnh báo** → *AI đọc hoá đơn* → dán vào ô **Gemini API key**, chọn **Google Gemini** → **Lưu cài đặt AI**.

Gói miễn phí giới hạn số lượt mỗi phút/ngày (thừa cho một nhà hàng) và không cần thẻ thanh toán. Về dữ liệu: theo [điều khoản Gemini API](https://ai.google.dev/gemini-api/terms), người dùng ở EU (gồm Đức), Thuỵ Sĩ và Anh được áp dụng quy định dữ liệu của gói trả phí cả khi dùng miễn phí (Google không dùng dữ liệu để cải thiện sản phẩm). Ở nước khác, dữ liệu gói miễn phí có thể được Google dùng và cho nhân viên xem xét, nên đừng gửi tài liệu nhạy cảm. Model mặc định `gemini-flash-latest` luôn trỏ tới bản Gemini Flash mới nhất; có thể đổi trong Cài đặt.

Claude / OpenAI tính phí theo lượt dùng qua API (tài khoản API riêng, không dùng chung với gói Claude Pro/ChatGPT Plus).

## Bật bản web trên GitHub Pages

Vào **Settings → Pages** của repo → *Build and deployment* → *Source*: **Deploy from a branch** → Branch **main**, thư mục **/ (root)** → **Save**. Sau 1–2 phút app chạy tại <https://dothanhdatdo.github.io/inventur/>.

Repo này tách riêng khỏi website đặt món `maiwokzo`, nên sửa hay deploy Inventur không ảnh hưởng gì tới trang của khách.

## Chạy trên máy (bản server)

```bash
git clone https://github.com/dothanhdatdo/inventur.git
cd inventur
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
# mở http://127.0.0.1:8000
```

Lần chạy đầu app tự tạo dữ liệu mẫu của một nhà hàng (tắt bằng `SEED_DEMO=0`). Cấu hình qua biến môi trường, xem [.env.example](.env.example).

### Bật email cảnh báo (bản server, Gmail)

1. Bật xác minh 2 bước cho tài khoản Gmail dùng để gửi.
2. Tạo *App Password* tại <https://myaccount.google.com/apppasswords>.
3. Đặt `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER=<gmail>`, `SMTP_PASSWORD=<app password>`, `ALERT_EMAIL=dothanhdatdo@gmail.com`.

### Deploy

- **Render** (miễn phí): dùng file [`render.yaml`](render.yaml). Vào Render → *New* → *Blueprint* → chọn repo này. Lưu ý: gói free không giữ ổ đĩa, dữ liệu SQLite sẽ mất khi service khởi động lại. Hãy thêm Persistent Disk mount vào `/data` và đặt `DATA_DIR=/data`.
- **Docker**: `docker build -t inventur . && docker run -p 8000:8000 -v inventur-data:/data inventur`
- **GitHub Codespaces**: *Code* → *Codespaces* → *Create codespace*. App tự chạy ở cổng 8000.

Khi đưa lên mạng công khai, nên đặt `APP_PASSWORD` để bật đăng nhập.

## Bản GitHub Pages hoạt động thế nào

`index.html` tải Pyodide (Python biên dịch sang WebAssembly) từ CDN jsDelivr, cài FastAPI/SQLAlchemy/Jinja2, rồi nạp mã nguồn trong `app/` (danh sách ở `web/manifest.json`). `web/runtime.js` chặn các cú click/submit form và chuyển chúng thành request gửi thẳng tới app FastAPI chạy trong trình duyệt (`app/browser.py`). Database SQLite nằm trong IndexedDB nên dữ liệu còn nguyên khi tải lại trang.

Sau khi sửa file trong `app/`, chạy lại:

```bash
python tools/build_manifest.py
```

(test `test_browser_manifest_is_up_to_date` sẽ báo lỗi nếu quên.) Repo có file `.nojekyll` để GitHub Pages phục vụ cả các file bắt đầu bằng `_` (vd. `__init__.py`).

## Kiểm thử

```bash
pip install -r requirements-dev.txt
pytest
```

## Cấu trúc

```
.
├── index.html            # trang khởi động bản GitHub Pages
├── web/runtime.js        # cầu nối trình duyệt <-> app Python (Pyodide)
├── web/manifest.json     # danh sách file Python/template cho bản Pages
├── app/
│   ├── main.py           # các trang web (FastAPI)
│   ├── db.py             # mô hình dữ liệu (SQLAlchemy)
│   ├── browser.py        # chạy app trong Pyodide
│   ├── areas.py          # khu Bếp / Quầy, đoán khu cho hàng mới
│   ├── seed.py           # dữ liệu mẫu
│   ├── services/
│   │   ├── stock.py      # nhập/xuất kho theo lô, kiểm kê, bán món, gợi ý đặt hàng
│   │   ├── ocr.py        # AI đọc hoá đơn (Gemini / Claude / GPT-4o / demo)
│   │   ├── pdf_invoice.py # đọc hoá đơn PDF METRO không cần AI
│   │   ├── matching.py   # khớp tên hàng trên HĐ với nguyên liệu, quy đổi đơn vị
│   │   ├── invoices.py   # quy trình hoá đơn: nháp → duyệt → nhập kho
│   │   └── alerts.py     # cảnh báo sắp hết + gửi email (theo khu)
│   ├── templates/        # giao diện (Jinja2)
│   └── static/           # CSS, JS, Chart.js, hoá đơn mẫu
├── tests/
└── tools/build_manifest.py
```
