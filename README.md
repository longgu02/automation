# Automation

Hệ thống automation dạng module. Mỗi module làm **đúng một việc**; một job sau
này là **nhiều module chạy tuần tự**, nối với nhau qua một hợp đồng dữ liệu chung.

Hiện có:

| Module | Việc của nó |
|---|---|
| **`video_gen`** | sinh video từ prompt, bằng nhiều tài khoản Google AI Pro |
| **`video_export`** | ghép các clip đã sinh thành **một** file video hoàn chỉnh |
| **`image_crawl`** | lấy ảnh nổi bật từ Pinterest, với nhịp thao tác như người dùng |

```
prompt (YAML) ──> video_gen ──> nhiều clip mp4 ──> video_export ──> 1 file mp4

từ khoá ──────> image_crawl ──> top N ảnh + manifest.json
```

Nối các module thành **job** bằng giao diện sơ đồ kéo thả:

```powershell
python scripts/run_ui.py        # mở trình soạn sơ đồ trong trình duyệt
python scripts/run_job.py demo  # chạy job đã dựng
```

---

## 0. Dành cho AI agent vừa clone repo này

> Phần này viết cho trợ lý AI vừa `git clone` về một máy lạ. Người đọc cũng dùng
> được, nhưng mục tiêu của nó là: **đọc xong mục 0 là cài được, chạy được, và
> biết project đang dở ở đâu — không phải hỏi lại.**

### 0.1 Đọc gì, theo thứ tự nào

File này dài ~900 dòng và phần lớn là hướng dẫn thao tác tay. **Đừng đọc tuần
tự từ đầu tới cuối.** Chỉ cần năm chỗ sau:

| Thứ tự | Đọc | Vì sao cần |
|---|---|---|
| 1 | Mục 0 — chính là đây | trạng thái hiện tại và luật cứng |
| 2 | [core/registry.py](core/registry.py) | danh mục module và hợp đồng `reads`/`writes`. Đây là **chỗ duy nhất** kể tên mọi module — code, không phải tài liệu, nên không bao giờ lệch |
| 3 | Mục 1 — Bản đồ thư mục | file nào nằm đâu |
| 4 | Mục 12 — Hợp đồng module | bắt buộc, nếu định viết hoặc sửa module |
| 5 | Mục 13 — Giới hạn | những gì **chưa** được kiểm chứng ngoài đời thật |

Kiến trúc gói gọn một câu: *mỗi module làm đúng một việc, job là nhiều module
chạy tuần tự, nối nhau qua `ctx.shared`.*

### 0.2 Cài trên máy mới

```powershell
# 1. Phụ thuộc Python (đã kiểm trên Python 3.12.10)
pip install -r requirements.txt

# 2. Kiểm ngay — phải thấy đúng "138 passed"
python -m pytest tests/ -q
```

Test không cần Chrome, không cần ffmpeg, không cần mạng. Nếu 138 test xanh thì
phần lõi đã lành; mọi lỗi còn lại là chuyện môi trường bên ngoài.

```powershell
# 3. Công cụ ngoài — chỉ cần cái nào bạn định dùng
Test-Path "C:\Program Files\Google\Chrome\Application\chrome.exe"  # video_gen, image_crawl
ffmpeg -version                                                     # video_export
```

Thiếu ffmpeg thì `winget install Gyan.FFmpeg`. Thiếu Chrome thì đổi
`browser_channel: chromium` trong `config/video_gen.yaml` rồi
`playwright install chromium`.

```powershell
# 4. Secret — repo không chứa cái nào. Chỉ cần khi dùng backend gemini_api.
Copy-Item .env.example .env     # rồi điền GEMINI_API_KEY
```

**Điều dễ vấp nhất khi clone:** thư mục `.secrets/` **không** đi theo repo — nó
chứa cookie phiên đăng nhập nên bị `.gitignore` chặn có chủ đích. Máy mới luôn
bắt đầu ở trạng thái *chưa đăng nhập*, và không có thông báo lỗi nào nói thẳng
điều đó cho tới khi trình duyệt mở ra màn hình login. Phải đăng nhập lại:

```powershell
python scripts/login_flow.py --account acc1   # phiên Google, cho video_gen
python scripts/login_pinterest.py             # phiên Pinterest, cho image_crawl
```

```powershell
# 5. Xác nhận đã sẵn sàng — không tốn credit, không mở trình duyệt
python scripts/run_video_gen.py --dry-run
```

Lệnh trên in ra danh sách prompt sẽ sinh. Thấy được danh sách nghĩa là config,
tài khoản và hàng đợi đều đọc được — đủ để bắt đầu làm việc.

### 0.3 Trạng thái: đang dở đến đâu

Chốt ngày **2026-08-18**. Bảng này là nguồn sự thật về tiến độ; mục 13 giải
thích chi tiết từng dòng ⚠ và ❌.

| Phần | Trạng thái | Bằng chứng |
|---|---|---|
| Lõi `core/` + bộ chạy job | ✅ chạy được | job hai node `test-ui` đi hết chuỗi `video_gen → video_export`, xem log mới nhất trong `logs/` |
| Bộ test | ✅ xanh | `python -m pytest tests/ -q` → 138 passed in 0.63s |
| Giao diện sơ đồ kéo thả | ✅ chạy được | `python scripts/run_ui.py`, job dựng từ UI chạy được bằng `run_job.py` |
| Phiên đăng nhập Google + Pinterest | ✅ **chỉ trên máy gốc** | `.secrets/` không theo repo — máy mới phải làm lại mục 0.2 bước 4 |
| **Sinh video thật** | ❌ **chưa từng chạy lần nào** | `output/` rỗng, 0 file. Tất cả những gì đã chạy đều là `--dry-run` |
| Selector Flow | ⚠ chưa kiểm chứng | `config/flow_selectors.yaml` viết theo giao diện Flow tại thời điểm dựng module, chưa đối chiếu tài khoản thật |
| `results_order` | ⚠ chưa xác nhận bằng mắt | không có cách nào đoán đúng từ code |
| Đổi tài khoản khi hết credit | ⚠ chỉ đúng với backend giả | logic điều phối đã test kỹ; chưa biết Flow báo hết credit bằng chữ gì |
| Backend `gemini_api` | ❌ chưa chạy | cần bật thanh toán mới kiểm được |
| `image_crawl` trên Pinterest thật | ❌ chưa chạy | lớp trình duyệt đã kiểm bằng Chrome thật; hình dạng JSON thật của Pinterest thì chưa |
| `video_export` mã hoá thật | ⚠ mới `--dry-run` | `plan.py` là hàm thuần tuý nên test đầy đủ, nhưng chưa có clip thật nào để ghép |

**Việc tiếp theo — đúng một việc:** làm **Bước 4** ở mục 3. Chạy *một* prompt
thật với `--headful`, ngồi nhìn tận mắt, rồi sửa `flow_selectors.yaml` và
`results_order` cho khớp giao diện thật.

Lý do phải là việc này trước: mọi dòng ⚠ và ❌ trong bảng đều nằm sau nó. Chưa
có lấy một clip thật thì `video_export` không có gì để ghép, và cơ chế đổi tài
khoản không có cách nào gặp tường credit để lộ ra chữ nó cần bắt.

### 0.4 Luật cứng

1. **Không bao giờ commit `.secrets/`.** Thư mục đó chứa cookie phiên Google đã
   đăng nhập — lộ ra ngoài đồng nghĩa với trao quyền truy cập tài khoản.
   `.gitignore` đã chặn sẵn; đừng phá bằng `git add -f`.
2. **Hỏi người dùng trước khi chạy thật.** Mỗi lần sinh video là tốn credit có
   thật, không hoàn lại. Mặc định luôn là `--dry-run`. Giao diện sơ đồ cố ý
   *không* có nút "Chạy thật" — đó là thiết kế, không phải thiếu sót (mục 11).
3. **Đừng ghi file tiếng Việt bằng PowerShell.** `Set-Content` / `Get-Content`
   trên Windows PowerShell 5.1 làm hỏng dấu — `ề ọ ỏ ờ Đ` mất sạch và hỏng âm
   thầm, không báo lỗi. Dùng Python với `encoding="utf-8"`, hoặc công cụ ghi
   file sẵn có của agent. Cùng gốc rễ đó: `print` một chuỗi tiếng Việt trong
   script Python sẽ ném `UnicodeEncodeError` vì console Windows mặc định là
   cp1252 — chạy kèm `PYTHONIOENCODING=utf-8` thì hết.
4. **Thêm module mới = thêm một `ModuleSpec` vào `core/registry.py`.** Không
   phải sửa UI, không phải sửa bộ chạy job. Hợp đồng nằm ở mục 12.
5. **Nhịp chậm trong `image_crawl` là cố ý.** Những quãng nghỉ ngẫu nhiên và
   cuộn từng nấc trông như chỗ tối ưu được, nhưng bỏ chúng đi là mời Pinterest
   chặn tài khoản (mục 8).
6. **Sửa xong việc gì thì cập nhật bảng 0.3.** Bảng đó chỉ có giá trị khi nó
   còn đúng. Đổi trạng thái một dòng thì sửa luôn cả ngày chốt ở đầu mục.

---

## 1. Bản đồ thư mục

```
automation/
├── core/                     ← Hạ tầng dùng chung, KHÔNG chứa logic nghiệp vụ
│   ├── module.py               Hợp đồng của MỌI module (đọc file này trước tiên)
│   ├── registry.py             Danh mục module — UI và job runner đều đọc từ đây
│   ├── job.py                  Định nghĩa job (đồ thị) + bộ chạy theo thứ tự tô-pô
│   ├── accounts.py             Kho tài khoản + bộ cấp phát (dự phòng/song song)
│   ├── config.py               YAML → thay biến môi trường → validate Pydantic
│   ├── errors.py               Cây lỗi: cái nào retry được, cái nào không
│   ├── retry.py                Backoff luỹ thừa + jitter
│   ├── paths.py                Mọi đường dẫn của project, khai báo một chỗ
│   └── logging_setup.py
│
├── modules/
│   ├── video_gen/            ← Module thứ nhất
│   │   ├── module.py           Quy trình: bỏ việc thừa · chuẩn bị · bàn giao
│   │   ├── runner.py           Cơ chế: worker, đổi tài khoản, chạy song song
│   │   ├── models.py           VideoSpec (đầu vào) / VideoArtifact (đầu ra)
│   │   ├── config.py           Hợp đồng của config/video_gen.yaml
│   │   ├── specs.py            YAML → danh sách VideoSpec đã đủ tham số
│   │   ├── state.py            Sổ resume — không render lại thứ đã có
│   │   └── backends/
│   │       ├── base.py         Interface VideoBackend
│   │       ├── flow_browser.py Điều khiển Chrome trên Google Flow  ← đang dùng
│   │       ├── gemini_api.py   Gọi Veo qua Gemini API              ← để dành
│   │       └── locators.py     Đọc bản đồ selector từ YAML
│   ├── video_export/         ← Module thứ hai
│   │   ├── module.py           Quy trình: gom clip · đo · lập kế hoạch · thi hành
│   │   ├── plan.py             Quyết định: nối byte hay mã hoá lại (hàm thuần tuý)
│   │   ├── ffmpeg.py           Lớp bọc ffmpeg/ffprobe (toàn bộ phần chạm hệ thống)
│   │   ├── models.py           Canvas · ClipInfo · ExportPlan
│   │   └── config.py           Hợp đồng của config/video_export.yaml
│   └── image_crawl/          ← Module thứ ba
│       ├── module.py           Quy trình: gom pin · xếp hạng · tải · kê khai
│       ├── extract.py          Bóc pin khỏi JSON + xếp hạng (hàm thuần tuý)
│       ├── humanize.py         Nhịp thao tác như người thật
│       ├── browser.py          Điều khiển Playwright, ba nguồn dữ liệu
│       ├── models.py           PinCandidate · RankingBasis
│       └── config.py           Hợp đồng của config/image_crawl.yaml
│
├── ui/                       ← Giao diện sơ đồ kéo thả
│   ├── server.py               Máy chủ nhỏ (thư viện chuẩn), chỉ nghe 127.0.0.1
│   └── static/index.html       Trình soạn sơ đồ — một file, không thư viện ngoài
│
├── config/
│   ├── video_gen.yaml          Cấu hình sinh video
│   ├── video_export.yaml       Cấu hình ghép video
│   ├── image_crawl.yaml        Cấu hình lấy ảnh Pinterest
│   ├── accounts.yaml           Danh sách tài khoản Gemini Pro
│   ├── flow_selectors.yaml     Bản đồ DOM — sửa khi Google đổi giao diện
│   ├── jobs/                   Job do giao diện sơ đồ ghi ra
│   └── prompts/demo.yaml       Prompt của bạn
│
├── scripts/
│   ├── run_ui.py               Mở giao diện sơ đồ
│   ├── run_job.py              Chạy một job gồm nhiều module
│   ├── login_flow.py           Đăng nhập Google Flow (mỗi tài khoản một lần)
│   ├── login_pinterest.py      Đăng nhập Pinterest (một lần)
│   ├── inspect_flow.py         Dò selector khi giao diện đổi
│   ├── run_video_gen.py        Chạy module sinh video
│   ├── run_video_export.py     Chạy module ghép video
│   └── run_image_crawl.py      Chạy module lấy ảnh
│
├── output/video_gen/           Clip + _state.json + _runs/<run_id>.json
├── output/export/              File video cuối
├── output/image_crawl/         Ảnh tải về + manifest.json
├── logs/                       Một file log cho mỗi lần chạy
└── tests/
```

**Nguyên tắc phân tầng, đọc từ trên xuống:**

| Tầng | Biết gì | Không biết gì |
|---|---|---|
| `scripts/` | tham số dòng lệnh | logic nghiệp vụ |
| `modules/*/module.py` | thứ tự công việc, resume, bàn giao | Playwright, ffmpeg, HTTP, chuyện đa luồng |
| `modules/*/runner.py`, `plan.py` | worker/tài khoản; quyết định mã hoá | prompt tới từ đâu, kết quả đi đâu |
| `backends/`, `ffmpeg.py` | cách nói chuyện với dịch vụ/công cụ ngoài | file config, sổ trạng thái |
| `core/` | không gì cả | mọi thứ thuộc nghiệp vụ |

Riêng `video_export` còn tách thêm một lớp nữa: `plan.py` **quyết định** (hàm
thuần tuý), `ffmpeg.py` **thi hành** (tiến trình con). Nhờ ranh giới đó mà
`--dry-run` in ra được đúng từng lệnh sẽ chạy, và phần logic khó nhất kiểm thử
được trên máy không có ffmpeg.

Mỗi mũi tên chỉ đi xuống. Nhờ vậy đổi backend không phải sửa module, và thêm
module không phải sửa `core/`.

---

## 2. Cài đặt

```powershell
pip install -r requirements.txt
```

Backend `flow_browser` dùng **Google Chrome thật** đã cài trên máy (đã kiểm tra:
có sẵn ở `C:\Program Files\Google\Chrome\Application\chrome.exe`) nên không cần
tải trình duyệt riêng. Nếu bạn đổi sang `browser_channel: chromium` thì chạy thêm:

```powershell
playwright install chromium
```

Module `video_export` cần **ffmpeg** (đã kiểm tra: ffmpeg 9.0 có trong PATH của
bạn). Nếu máy khác chưa có:

```powershell
winget install Gyan.FFmpeg
```

---

## 3. Chạy lần đầu (theo đúng thứ tự này)

### Bước 1 — Đăng nhập một lần cho mỗi tài khoản

```powershell
python scripts/login_flow.py --list      # xem tài khoản nào đã đăng nhập
python scripts/login_flow.py             # đăng nhập tài khoản đầu tiên
```

Một cửa sổ Chrome mở ra. Bạn tự đăng nhập bằng tài khoản Google AI Pro, rồi
nhấn Enter ở terminal. Phiên đăng nhập được giữ trong thư mục profile của tài
khoản đó và dùng lại cho mọi lần chạy sau.

Có nhiều tài khoản thì xem [mục 5](#5-nhiều-tài-khoản--dự-phòng-và-chạy-song-song).

> Script **không** đọc, không lưu, không đụng tới mật khẩu. Tự động hoá màn hình
> đăng nhập Google là đường ngắn nhất tới 2FA, CAPTCHA và khoá tài khoản — đăng
> nhập tay 30 giây một lần đổi lại sự yên ổn hàng tháng.

### Bước 2 — Trỏ vào một project cố định *(rất nên làm)*

Trong cửa sổ vừa mở, tạo một project trên Flow, chỉnh sẵn **model** và **tỉ lệ
khung hình** bằng tay, rồi chép URL project vào `config/accounts.yaml` cho đúng
tài khoản đó:

```yaml
accounts:
  - id: acc1
    workspace_url: "https://labs.google/fx/tools/flow/project/abc123"
```

(Chỉ có một tài khoản và dùng URL chung thì đặt ở `browser.workspace_url` trong
`config/video_gen.yaml` cũng được.)

**Vì sao mặc định không tự chỉnh cài đặt:** Flow ghi nhớ cài đặt theo từng
project. Chỉnh tay một lần rồi để `apply_settings_in_ui: false` khiến automation
chỉ còn phải làm ba việc đơn giản và ổn định nhất — *gõ prompt → chờ → tải về*.
Mỗi lần đi mò bảng settings là thêm 6 selector có thể vỡ, đổi lấy một tiện lợi
mà bạn chỉ cần đúng một lần. Nếu thật sự cần đổi tham số theo từng prompt, bật
`apply_settings_in_ui: true` và chấp nhận đánh đổi đó.

### Bước 3 — Xem trước, không tốn credit

```powershell
python scripts/run_video_gen.py --dry-run
```

In ra chính xác những prompt sẽ chạy cùng tham số cuối cùng của chúng. Không mở
trình duyệt, không tiêu gì.

### Bước 4 — Chạy thật một prompt, nhìn tận mắt

```powershell
python scripts/run_video_gen.py --only bien-hoang-hon --headful
```

Lần đầu **bắt buộc** để `--headful` và quan sát. Bạn cần tự xác nhận một điều
mà code không thể tự đoán: **clip mới xuất hiện ở đầu hay cuối danh sách kết quả.**
Nếu nó xuất hiện ở đầu, sửa `config/video_gen.yaml`:

```yaml
browser:
  results_order: newest_first
```

Đặt sai giá trị này thì mọi thứ vẫn chạy trơn tru — chỉ có điều nó tải về clip cũ.

### Bước 5 — Chạy cả bộ

```powershell
python scripts/run_video_gen.py
```

---

## 4. Dùng hằng ngày

```powershell
# Xem trước
python scripts/run_video_gen.py --dry-run

# Chỉ vài prompt
python scripts/run_video_gen.py --only pho-dem-mua,san-pham-xoay

# Chỉ 5 prompt đầu
python scripts/run_video_gen.py --limit 5

# Sinh lại kể cả thứ đã xong
python scripts/run_video_gen.py --only pho-dem-mua --force

# Dùng bộ prompt khác
python scripts/run_video_gen.py --prompts config/prompts/du-lich.yaml

# Chạy song song 3 tài khoản
python scripts/run_video_gen.py --parallel 3

# Xem log chi tiết khi gỡ lỗi
python scripts/run_video_gen.py --log-level DEBUG --headful
```

### Thêm prompt

Sửa `config/prompts/demo.yaml`, hoặc tạo file mới rồi thêm vào `prompt_files`:

```yaml
prompts:
  - id: canh-moi                 # duy nhất, dùng làm tên thư mục — GIỮ ỔN ĐỊNH
    prompt: "Mô tả cảnh quay..."
    aspect_ratio: "9:16"         # ghi đè riêng cho prompt này
    outputs_per_prompt: 2
```

Thứ tự ưu tiên tham số, sau thắng trước:

```
defaults trong video_gen.yaml  →  defaults trong file prompt  →  từng mục prompt
```

---

## 5. Nhiều tài khoản — dự phòng và chạy song song

Khai tài khoản trong `config/accounts.yaml`. Mỗi tài khoản có **thư mục profile
Chrome riêng** (bắt buộc) và thường có **project Flow riêng**:

```yaml
accounts:
  - id: acc1
    label: "tài khoản chính"
    profile_dir: .secrets/browser_profile          # profile bạn đã đăng nhập
    workspace_url: "https://labs.google/fx/tools/flow/project/xxxxx"

  - id: acc2
    label: "tài khoản phụ"
    # không khai profile_dir → tự dùng .secrets/profiles/acc2
    workspace_url: "https://labs.google/fx/tools/flow/project/yyyyy"
```

### Thêm một tài khoản — ba bước

```powershell
# 1. Bỏ ghi chú khối acc2 trong config/accounts.yaml
# 2. Đăng nhập cho nó
python scripts/login_flow.py --account acc2
# 3. Kiểm tra
python scripts/login_flow.py --list
```

### Hai chế độ

```powershell
# Dự phòng (max_parallel: 1) — một tài khoản một lúc, hết credit thì tự chuyển
python scripts/run_video_gen.py

# Song song — 3 tài khoản cùng chạy, 3 cửa sổ Chrome riêng
python scripts/run_video_gen.py --parallel 3

# Chỉ dùng vài tài khoản, để dành cái còn lại cho việc khác
python scripts/run_video_gen.py --accounts acc1,acc2
```

Đặt cố định trong `config/video_gen.yaml` nếu không muốn gõ cờ mỗi lần:

```yaml
execution:
  max_parallel: 3
```

### Chuyện gì xảy ra khi một tài khoản hết credit

Đây là phần đáng đọc kỹ, vì nó quyết định bạn có mất prompt nào không:

1. Tài khoản bị đánh dấu `exhausted`, loại khỏi lần chạy này.
2. Prompt đang dở được **trả lại hàng đợi** — không mất, **không** bị tính là hỏng.
3. Worker đóng Chrome, xin một tài khoản khác, chạy tiếp từ đúng prompt đó.
4. Không còn tài khoản nào → worker dừng, và các prompt còn sót được **báo cáo
   rõ ràng** là chưa chạy kèm lý do, chứ không im lặng biến mất.

Một prompt chỉ được chuyển tài khoản tối đa `max_account_switches_per_spec` lần
(mặc định = số tài khoản), để một prompt hỏng vì lý do riêng của nó không đi
vòng quanh mọi tài khoản mãi mãi.

Cuối mỗi lần chạy bạn sẽ thấy bảng này:

```
  Tài khoản:
    acc1         exhausted  4 prompt  (tài khoản chính)
                 └─ Flow báo lỗi: You've run out of credits
    acc2         ready      8 prompt  (tài khoản phụ)
```

### Vì sao song song theo *tài khoản*, không phải theo *prompt*

Phía Google, **mỗi tài khoản có một hàng đợi render**. Bắn nhiều prompt cùng lúc
vào một tài khoản không làm nó nhanh hơn, mà còn khiến việc ghép "clip nào của
prompt nào" trở nên bất định — tức là rủi ro tải về file sai. Hai tài khoản khác
nhau mới là hai hàng đợi thật sự độc lập.

Nên: **số worker chạy thật = min(`max_parallel`, số tài khoản đang bật)**. Đặt
`--parallel 8` với 2 tài khoản vẫn chỉ chạy 2 worker, và đó là hành vi đúng.

### Nhiều job khác nhau chạy đồng thời

Hai cách, chọn theo nhu cầu:

```powershell
# Cách 1 — một tiến trình, một hàng đợi chung, mọi tài khoản cùng ăn việc.
# Tốt nhất khi bạn chỉ muốn "làm cho xong đống prompt này thật nhanh".
python scripts/run_video_gen.py --parallel 3

# Cách 2 — hai tiến trình độc lập, mỗi cái một bộ prompt và một nhóm tài khoản.
# Tốt khi hai job có mức ưu tiên hoặc lịch chạy khác nhau.
python scripts/run_video_gen.py --prompts config/prompts/du-lich.yaml --accounts acc1
python scripts/run_video_gen.py --prompts config/prompts/san-pham.yaml --accounts acc2,acc3
```

Cách 2 an toàn vì hai tiến trình không dùng chung tài khoản nào — và sổ trạng
thái ghi theo lối nguyên tử nên chúng không giẫm lên nhau.

> Với `--headful`, chạy song song N tài khoản sẽ mở N cửa sổ Chrome. Khi đã tin
> vào thiết lập rồi thì đặt `headless: true` cho gọn máy.

---

## 6. Cơ chế tiết kiệm credit

Đây là phần khiến module này *hiệu quả*, đáng hiểu rõ:

Mỗi `VideoSpec` có một **vân tay** — mã băm của mọi trường ảnh hưởng tới video
đầu ra (prompt, model, tỉ lệ, độ phân giải, thời lượng, seed, số output, backend).
Vân tay được ghi vào `output/video_gen/_state.json` ngay sau **mỗi** prompt hoàn thành.

Hệ quả trong thực tế:

| Bạn làm gì | Lần chạy sau |
|---|---|
| Chạy lại y nguyên | Không sinh lại gì cả |
| Ctrl+C giữa chừng | Giữ nguyên phần đã xong, chạy tiếp phần còn lại |
| Sửa `notes` / `tags` | Không sinh lại (không ảnh hưởng video) |
| Sửa nội dung prompt | Chỉ sinh lại prompt đó |
| Xoá file mp4 đi | Sinh lại đúng file đó |
| Đổi `id` | Coi như prompt mới → sinh lại |
| `--force` | Sinh lại bất chấp |

Muốn xoá sạch dấu vết: xoá `output/video_gen/_state.json`.

---

## 7. Ghép clip thành một file — module `video_export`

```powershell
# Xem trước: in ra đúng từng lệnh ffmpeg sẽ chạy, không mã hoá gì
python scripts/run_video_export.py --dry-run

# Ghép mọi clip đã sinh thành một video ngang 1080p
python scripts/run_video_export.py

# Bản dọc cho TikTok/Shorts, cắt cho kín khung thay vì thêm viền
python scripts/run_video_export.py --aspect 9:16 --fit crop

# Chỉ vài clip, theo đúng thứ tự bạn muốn
python scripts/run_video_export.py --clips bien-hoang-hon,san-pham-xoay

# Chỉ định file đích, bỏ âm thanh
python scripts/run_video_export.py -o output/export/gioi-thieu.mp4 --no-audio
```

### Hai con đường, tự chọn

| Tình huống | Cách làm | Chi phí |
|---|---|---|
| Mọi clip đã đồng nhất và đúng khung đích | nối byte (`-c copy`) | **vài giây**, không mất chút chất lượng nào |
| Có clip lệch tỉ lệ / fps / âm thanh | chuẩn hoá từng clip rồi nối | một lần mã hoá cho mỗi clip |

`encode.mode: auto` (mặc định) tự chọn. Bạn không phải nghĩ về nó — nhưng log
luôn nói rõ nó chọn gì và **vì sao**:

```
Kế hoạch: Các clip không đồng nhất (kích thước khác nhau: 1080x1080, 1280x720,
1920x1080; fps khác nhau: 24, 30; 2/3 clip có âm thanh, số còn lại không)
-> chuẩn hoá về 1920x1080@24fps.
```

Đặt `encode.mode: never` nếu bạn muốn nó **báo lỗi** thay vì âm thầm mã hoá lại.

### Clip lệch tỉ lệ thì xử lý thế nào

```
  letterbox (mặc định)              crop
  ┌──────────────────┐              ┌──────────────────┐
  │▓▓▓▓┌────────┐▓▓▓▓│              │  ┌────────────┐  │
  │▓▓▓▓│ khung  │▓▓▓▓│              │▒▒│   khung    │▒▒│
  │▓▓▓▓│  1:1   │▓▓▓▓│              │▒▒│    1:1     │▒▒│
  │▓▓▓▓└────────┘▓▓▓▓│              │  └────────────┘  │
  └──────────────────┘              └──────────────────┘
  ▓ = viền đen thêm vào             ▒ = phần bị cắt mất
  KHÔNG mất hình                    Kín khung, mất rìa
```

### Thứ tự ghép = thứ tự cảnh

Khoá `sources.order` quyết định trình tự trong video cuối:

- **`pipeline`** (mặc định) — giữ nguyên thứ tự `video_gen` đưa sang, tức **thứ tự
  bạn khai prompt**. Thường chính là thứ tự kịch bản bạn nghĩ trong đầu. Chạy độc
  lập không có pipeline thì tự rơi về `filename`, và nói rõ trong log.
- **`filename`** — sắp theo tên file a-z.
- **`explicit`** — theo đúng danh sách `order_explicit`. Clip không có trong danh
  sách bị loại (kèm cảnh báo). Tương đương cờ `--clips`.

### Vài chi tiết đã xử lý sẵn

- **Clip không có tiếng** được chèn khoảng lặng dài đúng bằng nó. Không có bước
  này, bộ ghép của ffmpeg sẽ vỡ vì các đoạn lệch bộ luồng.
- **`on_exists: suffix`** (mặc định) — file đích đã có thì ghi ra `_02`, `_03`…
  **Không bao giờ ghi đè** khi bạn chưa yêu cầu.
- **`+faststart`** — chỉ mục đưa lên đầu file, phát được ngay khi vừa tải.
- **`setsar=1`** — thiếu nó, một số trình phát kéo giãn hình dù kích thước đã đúng.
- **File tạm được dọn** sau khi xong (`keep_temp: true` để giữ lại mà soi).

---

## 8. Lấy ảnh Pinterest — module `image_crawl`

```powershell
# Đăng nhập một lần (rất nên làm — xem giải thích bên dưới)
python scripts/login_pinterest.py

# Xem kế hoạch, không mở trình duyệt
python scripts/run_image_crawl.py --query "brutalist architecture" --dry-run

# Lấy top 10 ảnh
python scripts/run_image_crawl.py --query "vietnamese street food"

# Gom kho ứng viên lớn hơn để chọn lọc kỹ hơn
python scripts/run_image_crawl.py -q "minimal poster" --top 20 --pool 300

# Chạy chậm hơn nữa khi thấy Pinterest phản ứng gắt
python scripts/run_image_crawl.py -q "..." --slow
```

### ⚠ Đọc kỹ chỗ này: "top 10 nhiều yêu thích nhất" có một điều kiện

**Pinterest không phải lúc nào cũng trả về số lượt lưu.** Module xử lý bằng cách
luôn báo cáo **cơ sở xếp hạng** nó thật sự đã dùng:

| `ranking_basis` | Nghĩa là gì |
|---|---|
| `saves` | Có số lượt lưu thật → **đúng nghĩa "nhiều yêu thích nhất"** |
| `reactions` | Không có lượt lưu, xếp theo số biểu cảm |
| `search_order` | **Không có số liệu nào.** Đây là thứ tự Pinterest tự xếp — vốn có tính tới tương tác, nhưng ta *không đo được* |

Khi rơi vào `search_order`, module in cảnh báo rõ ràng ra log và ra bảng kết quả,
và ghi vào `manifest.json`. Nó **không** gọi một danh sách theo thứ tự tìm kiếm
là "top theo lượt thích" — làm vậy là đưa cho bạn một con số không có thật.

Muốn tăng khả năng lấy được số lượt lưu: **đăng nhập** (`login_pinterest.py`) và
để `candidate_pool` cao. Khách vãng lai bị chặn sớm nên thường không kịp nhận
được dữ liệu có số liệu.

### Ba nguồn dữ liệu, thử lần lượt

| # | Nguồn | Có lượt lưu? |
|---|---|---|
| 1 | `__PWS_DATA__` — trạng thái Pinterest nhúng sẵn trong HTML | ✅ |
| 2 | JSON trang tự tải về khi bạn cuộn | ✅ |
| 3 | Bóc từ DOM (phương án cuối) | ❌ |

Nguồn 2 đáng chú ý: ta **đọc lại chính những phản hồi trình duyệt đã tải**, không
gọi thêm API nào. Nghĩa là không tạo thêm một lượt truy cập nào ngoài những gì
việc xem trang vốn đã sinh ra.

### "Chậm rãi tránh bị ban" — cụ thể là gì

Toàn bộ trong [humanize.py](modules/image_crawl/humanize.py), chỉnh ở khối
`pacing:` của config:

- **Nghỉ ngẫu nhiên**, không phải hằng số — nghỉ đúng 2,000s mỗi lần còn lộ liễu
  hơn là không nghỉ
- **Cuộn từng nấc 300–800px** rồi dừng, không nhảy thẳng xuống đáy
- **Cứ 6 nhịp dừng lâu 4–10s**, như đang xem một tấm ảnh
- **15% số lần cuộn ngược lên** — người thật hay lướt quá rồi kéo lại
- **Chuột di chuyển theo đường**, không dịch chuyển tức thời rồi bấm
- **Nghỉ 1–3s giữa hai lượt tải ảnh**
- **Trần thời lượng phiên 900s** — cái phanh chống cấu hình sai biến thành phiên
  cào hàng giờ mà bạn không để ý

Nói thẳng: cái này **giảm** rủi ro bị chặn và là phép lịch sự với máy chủ người
ta, nhưng **không đảm bảo** gì cả. Pinterest còn nhìn dấu vết trình duyệt, địa
chỉ IP và hành vi tài khoản. `--slow` nhân đôi mọi khoảng nghỉ nếu bạn thấy cần.

### Ghi công tác giả

Mỗi thư mục kết quả có `manifest.json` giữ `pin_url`, `image_url`, `saves` và
tiêu đ← của từng tấm. **Ảnh tải về thuộc bản quyền người đăng** — file này để bạn
truy lại nguồn và ghi công khi dùng.

---

## 9. Khi Google đổi giao diện

Đây là kịch bản hỏng **thường gặp nhất và duy nhất đáng lo** của backend trình duyệt.

Triệu chứng — trong log xuất hiện:

```
Không tìm thấy phần tử 'submit_button'. Đã thử 6 selector: ...
Ảnh chụp + HTML để debug: output/_debug
```

Quy trình sửa, **không cần đụng tới một dòng Python nào**:

```powershell
python scripts/inspect_flow.py
```

Script sẽ:
1. In bảng **ĐƯỢC / HỎNG** cho từng khoá trong `config/flow_selectors.yaml` — bạn
   thấy ngay cái nào vỡ.
2. Bật chế độ soi: bạn bấm chuột vào phần tử thật trên trang (ô prompt, nút gửi,
   thẻ clip, nút tải…), nó in ra selector dùng được cho từng cái.

Chép selector mới vào `config/flow_selectors.yaml`, đặt cái **bền nhất lên đầu**
danh sách. Xong.

Thứ tự bền vững, ưu tiên từ trên xuống:

```yaml
submit_button:
  - 'role=button[name="Generate"i]'      # 1. vai trò ARIA + nhãn  ← bền nhất
  - 'button:has-text("Generate")'        # 2. chữ người dùng thấy
  - 'button[aria-label*="Send"i]'        # 3. aria-label
  - 'button.xJ2k9'                       # 4. class CSS  ← dễ vỡ nhất, để cuối
```

Mỗi khoá là một **danh sách ứng viên**, thử lần lượt tới khi có cái khớp — nên
một selector chết không làm sập cả hệ.

---

## 10. Vì sao có hai backend

| | `flow_browser` *(đang dùng)* | `gemini_api` |
|---|---|---|
| Trả tiền bằng | Gói thuê bao Google AI Pro | Billing của Google Cloud / AI Studio |
| Ổn định | Vỡ khi Google đổi UI | Rất ổn định |
| Chạy nền | Được (`headless: true`) | Được |
| Tình trạng | Hoàn chỉnh | **Đã viết, chưa chạy thử thực tế** |

**Điểm cần biết rõ về tiền:** gói Google AI Pro **không** cấp quota Veo cho API
key — đó là hai túi tiền khác nhau. Đó chính là lý do backend trình duyệt được
làm trước, đúng như bạn chọn.

Đổi backend chỉ là một dòng trong `config/video_gen.yaml` (hoặc cờ `--backend`):

```yaml
backend: gemini_api
```

Interface `VideoBackend` (`backends/base.py`) đảm bảo phần còn lại của hệ thống
— resume, retry, manifest, log — không h← thay đổi.

---

## 11. Giao diện sơ đồ — nối module thành job

```powershell
python scripts/run_ui.py
```

Trình duyệt tự mở. Kéo module từ cột trái vào khung, nối chúng lại, đặt tên job
rồi bấm **Lưu** → ghi ra `config/jobs/<tên>.yaml`.

```
┌─ Module có sẵn ─┐  ┌──────── Khung vẽ ────────┐  ┌─ Chi tiết ─┐
│ 🎬 Sinh video   │  │   ┌────────┐             │  │ Nhãn       │
│ 🎞 Ghép video   │  │   │🎬 Sinh │──┐          │  │ File config│
│ 🖼 Lấy ảnh      │  │   └────────┘  │          │  │            │
│                 │  │               ▼          │  │ Ghi đè:    │
│   ← kéo sang    │  │   ┌────────┐  ┌────────┐ │  │ video.     │
│                 │  │   │🖼 Ảnh  │─▶│🎞 Ghép │ │  │  aspect... │
└─────────────────┘  └──────────────────────────┘  └────────────┘
```

**Thao tác:** kéo module vào khung để thêm · kéo từ **chấm phải** sang **chấm
trái** của nút khác để nối · `Delete` xoá thứ đang chọn · lăn chuột để phóng to ·
kéo nền để di chuyển · **Vừa khung** để canh lại.

### Cạnh nghĩa là gì — điểm dễ hiểu nhầm nhất

Cạnh chỉ quy định **thứ tự chạy** ("chạy sau"), **không** phải đường truyền dữ
liệu. Dữ liệu đi riêng qua `ctx.shared`, theo các khoá đã đặt tên — chính là hai
thẻ `vào:` / `ra:` hiện trên mỗi nút.

Tách hai thứ này khiến bạn nối tuỳ ý mà ngữ nghĩa vẫn rõ: kéo một cạnh chỉ có
nghĩa "chạy sau cái kia", còn dữ liệu tự tìm thấy nhau qua khoá.

### Ba nút kiểm soát

| Nút | Làm gì |
|---|---|
| **Kiểm tra** | Soát sơ đồ: vòng lặp, id trùng, cạnh hỏng, và **cảnh báo khi một module cần dữ liệu mà chưa nút nào trước nó tạo ra**. Hiện luôn thứ tự chạy đã tính. |
| **Chạy thử** | Chạy cả job ở chế độ dry-run rồi trả log về. An toàn tuyệt đối: không mở trình duyệt, không gọi ffmpeg, không tốn credit. |
| **Lưu** | Ghi ra YAML. Sửa tay file đó cũng được — nó chỉ là YAML thường. |

Vòng lặp bị chặn **ngay lúc kéo cạnh**, không đợi tới lúc kiểm tra.

### Vì sao không có nút "Chạy thật"

Bấm một nút trên trang web mà đốt credit Gemini hoặc mở phiên cào Pinterest là
cái bẫy. Chạy thật luôn đi qua dòng lệnh, nơi bạn thấy log trực tiếp và Ctrl+C
được bất cứ lúc nào:

```powershell
python scripts/run_job.py --list            # xem có những job nào
python scripts/run_job.py demo --dry-run    # xem sẽ chạy gì
python scripts/run_job.py demo              # chạy thật
```

Giao diện hiện sẵn câu lệnh đó sau mỗi lần chạy thử để bạn chép.

### Ghi đè tham số ngay trên sơ đồ

Chọn một nút → mục **Ghi đè tham số** → chọn tham số → sửa giá trị. Danh sách
tham số được **đọc thẳng từ lớp cấu hình Pydantic** của module (94 tham số cho
cả ba module hiện có), nên nó không bao giờ lệch với code.

Ghi đè lưu dưới dạng khoá có dấu chấm và đè lên file cấu hình lúc chạy:

```yaml
overrides:
  video.target_aspect_ratio: "9:16"
  execution.max_parallel: 3
```

Nhờ vậy một job có thể dùng chung `config/video_gen.yaml` mà vẫn chạy khác nhau
ở từng nút — không cần nhân bản file cấu hình.

### Thêm module mới vào bảng chọn

Thêm một `ModuleSpec` vào `REGISTRY` trong [core/registry.py](core/registry.py).
**Không phải sửa một dòng JavaScript nào** — giao diện tự đọc danh mục:

```python
ModuleSpec(
    name="upload_youtube",
    title="Đăng YouTube",
    description="...",
    module_path="modules.upload_youtube.module:UploadModule",
    config_path="modules.upload_youtube.config:UploadConfig",
    default_config_file="config/upload_youtube.yaml",
    reads=("final_video",),      # ← quyết định thẻ "vào" trên nút
    writes=("youtube_url",),     # ← quyết định thẻ "ra"
    accent="#ef4444", icon="📤",
)
```

---

## 12. Hợp đồng module (viết module mới)

Hợp đồng nằm ở `core/module.py`. Ba module hiện có đều theo đúng khuôn này:

```python
ctx = ModuleContext(run_id=..., workdir=..., logger=...)

VideoGenModule(gen_cfg).execute(ctx)
# ctx.shared["video_gen"]["videos"] == ["output/video_gen/xxx/xxx_01.mp4", ...]

VideoExportModule(export_cfg).execute(ctx)
# đọc ctx.shared["video_gen"]["videos"], giữ nguyên thứ tự,
# rồi ghi ctx.shared["video_export"]["final_video"]

UploadModule(up_cfg).execute(ctx)   # module tương lai: đọc final_video
```

`ctx.shared` là kênh truyền dữ liệu giữa các module. `ModuleResult.status`
(`success` / `partial` / `failed` / `skipped`) là thứ job runner dựa vào để
quyết định có chạy module kế tiếp hay không.

Chú ý cách `video_export` được viết: nó đọc `ctx.shared` **nếu có**, còn không
thì quét đĩa. Nhờ vậy nó vừa chạy được trong job vừa chạy được một mình bằng
`scripts/run_video_export.py`. Mọi module mới nên theo lối này.

**Viết module mới:** kế thừa `BaseModule`, cài đặt `run(ctx) -> ModuleResult`,
rồi đăng ký vào `core/registry.py` để nó xuất hiện trên sơ đồ. Chỉ vậy thôi.

---

## 13. Giới hạn cần biết trước

Nói thẳng để bạn không mất thời gian đoán:

- **Selector trong `config/flow_selectors.yaml` chưa được kiểm chứng trên tài
  khoản thật của bạn.** Chúng được viết theo cấu trúc giao diện Flow tại thời
  điểm dựng module. Lần chạy đầu tiên rất có thể phải chỉnh qua `inspect_flow.py`
  — hệ thống được thiết kế sẵn cho việc đó, nên nó là chuyện thường, không phải sự cố.
- **`results_order` phải do bạn xác nhận bằng mắt** ở lần chạy đầu (Bước 4).
  Không có cách nào đoán đúng chắc chắn từ code.
- **Trong một tài khoản, luôn tuần tự một prompt một lúc.** Song song hoá diễn ra
  ở mức tài khoản (mục 5). Đây là giới hạn có chủ đích, không phải thiếu sót.
- **Backend `gemini_api` chưa chạy thử thực tế.** Cần bật billing mới kiểm chứng được.
- **Cơ chế đổi tài khoản khi hết credit đã được kiểm chứng bằng test với backend
  giả, chưa gặp tường credit thật.** Logic điều phối là đúng; điều chưa biết là
  Flow hiển thị thông báo hết credit bằng chữ gì. Nếu lần đầu gặp mà nó không tự
  chuyển tài khoản, thêm từ khoá trong thông báo đó vào `_ERROR_SIGNATURES` ở
  [flow_browser.py](modules/video_gen/backends/flow_browser.py) — một dòng.
- **Tự động hoá giao diện web nằm ở vùng xám của điều khoản dịch vụ Google.**
  Dùng ở mức hợp lý cho công việc của chính bạn; đừng chạy với cường độ bất thường.
- **`video_export` chỉ nối clip, không có chuyển cảnh.** Cắt thẳng từ clip này
  sang clip kế tiếp. Muốn mờ dần, nhạc nền, phụ đề hay chèn logo thì đó là việc
  của những module riêng — chưa làm.
- **Chuẩn hoá tỉ lệ là mã hoá lại.** Video ra sẽ khác về mặt điểm ảnh so với clip
  gốc. Đặt `encode.crf` nhỏ hơn (18) nếu bạn thấy chất lượng hụt; clip gốc vẫn
  còn nguyên trong `output/video_gen/` nên không mất gì.
- **`image_crawl` chưa chạy thật trên Pinterest.** Lớp trình duyệt đã được kiểm
  bằng Chrome thật (mở, cuộn, rê chuột, bóc DOM, nâng cấp URL ảnh — tất cả đạt),
  và phần bóc JSON được kiểm bằng dữ liệu mẫu. Nhưng **hình dạng JSON thật của
  Pinterest hôm nay thì chưa ai xác nhận**. Lần chạy đầu hãy để `--headful` và
  xem log báo `ranking_basis` là gì. Nếu ra `search_order`, xem mục 8.
- **Tự động hoá Pinterest cũng nằm trong vùng xám điều khoản dịch vụ**, y như
  Google Flow. Nhịp chậm là phép lịch sự với máy chủ, không phải lá chắn.
- **Ảnh Pinterest có bản quyền của người đăng.** `manifest.json` giữ đường dẫn
  pin gốc để bạn truy nguồn; dùng lại thì tự chịu trách nhiệm ghi công/xin phép.

---

## 14. Kiểm thử

```powershell
python -m pytest tests/ -v
```

138 test, chia bảy nhóm:

| File | Phủ cái gì |
|---|---|
| `test_specs.py` | thứ tự ưu tiên khi trộn tham số, bắt lỗi gõ sai tên khoá, tính ổn định của vân tay resume |
| `test_accounts.py` | kho tài khoản, hàng đợi prompt, các lỗi cấu hình tài khoản gây hỏng ngầm |
| `test_runner.py` | **hết credit thì chuyển tài khoản**, chia việc song song, ghi sổ trạng thái từ nhiều luồng |
| `test_export_plan.py` | tính khung hình, chọn nối byte hay mã hoá lại, chuỗi bộ lọc letterbox/crop, chèn khoảng lặng |
| `test_image_extract.py` | bóc pin dù Pinterest đổi chỗ dữ liệu, và **cơ sở xếp hạng có trung thực không** |
| `test_humanize.py` | khoảng nghỉ ngẫu nhiên đúng khoảng, cuộn từng nấc, trần thời lượng phiên |
| `test_job.py` | thứ tự tô-pô, bắt vòng lặp, và **ghi đè từ UI có thật sự tới được config của module không** |

Ba thủ pháp giúp phần khó nhất vẫn test được mà không cần tài nguyên thật:

- `test_runner.py` dùng một **backend giả** tuân thủ nguyên interface
  `VideoBackend`. Tình huống "tài khoản hết credit giữa batch" — thứ bạn không
  thể chờ để thử bằng tay — được dựng lại trong 0,1 giây, và cái được kiểm là
  logic điều phối thật chứ không phải một bản mô phỏng.
- `plan.py` của `video_export` là **hàm thuần tuý** (không tiến trình con, không
  đĩa). Nên toàn bộ quyết định về mã hoá kiểm được **không cần có ffmpeg**.
- `test_humanize.py` **thay `time.sleep` bằng hàm ghi sổ**. Nhờ vậy kiểm được
  một phiên nghỉ tổng cộng 15 phút mà bộ test chạy xong trong 0,2 giây.

Phần chạm tài nguyên thật cố ý **không** test tự động: backend trình duyệt cần
Chrome và tài khoản thật, còn việc ffmpeg mã hoá đúng hay không thì `--dry-run`
(in ra nguyên lệnh) cộng với `ffprobe` trên file kết quả mới là cách kiểm chứng
đúng đắn.

