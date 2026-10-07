# ⚡ Pool Intelligence

**Sistem intelijensi LP untuk pool DLMM (fokus Meteora): dari discovery memecoin sampai prediksi range, Monte Carlo, analisis risiko, dan keputusan MASUK / TUNGGU / REBALANCE.**

> **Status: READY TO USE.** Semua jalur sudah aktif dan teruji: discovery DexScreener, Review dengan OHLCV asli (GeckoTerminal), fee APR asli (Meteora), Monte Carlo bootstrap, dan dashboard ter-deploy. Baca [Keterbatasan](#keterbatasan-yang-diketahui) sebelum pakai modal nyata.

---

## Daftar isi

1. [Apa ini & tujuannya](#apa-ini--tujuannya)
2. [Cara kerja inti](#cara-kerja-inti)
3. [Instalasi](#instalasi)
4. [Pemakaian](#pemakaian)
   - [scanner.py](#1-scannerpy--radar-discovery)
   - [dlmm_cli.py](#2-dlmm_clipy--engine-prediksi-lengkap)
   - [Dashboard lokal](#3-dashboard-lokal-web_terminalpy)
   - [Deploy ke Vercel](#4-deploy-ke-vercel)
5. [Data sources](#data-sources)
6. [Testing](#testing)
7. [Keterbatasan yang diketahui](#keterbatasan-yang-diketahui)
8. [Dokumen terkait](#dokumen-terkait)

---

## Apa ini & tujuannya

Pool Intelligence bukan coin picker. Projek ini menjawab satu pertanyaan untuk seorang LP:

> **Kalau saya pasang likuiditas di pool ini dengan range X, seberapa besar peluang fee-nya, seberapa cepat range-nya jebol, dan layak tidak modalnya?**

Pipeline-nya:

```
DexScreener discovery          ← cari kandidat (filter ONLY, tanpa formula)
        ↓
Candidate scanner              ← gate: liquidity, volume, aktivitas
        ↓
Review (per pool):
  ├─ Live snapshot (Jalur B)   ← Merton jump-diffusion, selalu jalan
  └─ Historical OHLCV (Jalur A)← bootstrap Monte Carlo, bila ≥30 candle asli
        ↓
Range / Monte Carlo / Risk     ← range bawah-atas, P(jebol), fee vs IL
        ↓
Decision                       ← MASUK / TUNGGU / REBALANCE
```

**File utama:**

| File | Peran |
|---|---|
| `scanner.py` | Discovery DexScreener, filter kandidat, Review pool (`scan()`, `review_pair()`) |
| `dlmm_lp_engine.py` | Engine prediksi: math → regime → range → bin → liquidity → Monte Carlo → risk → rebalance |
| `dlmm_cli.py` | CLI untuk menjalankan engine dari file candle JSON |
| `static/index.html` | Dashboard satu file (radar + halaman Review) |
| `api/index.py` | Handler Vercel untuk `/api/scan`, `/api/review`, `/api/healthz` |
| `web_terminal.py` | Server HTTP lokal untuk dashboard + API yang sama |
| `vercel.json` | Rewrite route ke handler API dan dashboard |
| `FORMULA_ENGINE.md` | Dokumen rumus lengkap (status formula, kontrak probabilitas, POOL-INTEL-4) |
| `DLMM_ENGINE.md` | Catatan engine DLMM & daftar pekerjaan sebelum pakai modal |

Engine versi: **`POOL-INTEL-4`** (jalur historis aktif di Review + kontrak probabilitas seragam).

---

## Cara kerja inti

### 1. Discovery via DexScreener

`scan()` mengambil kandidat dari DexScreener (semua chain), lalu menerapkan gate — **tanpa formula apa pun di tahap ini** (peran scanner = `FILTER_ONLY`):

- Group per `chain + base token`, pilih pair dengan likuiditas tertinggi.
- Likuiditas ≥ **$10.000**.
- Minimal **1 buy dalam 1 jam** dan **volume 1h > 0**.
- Diurutkan berdasarkan aktivitas (tx 1h → vol 1h → likuiditas → tx 6h → vol 6h).
- Kandidat muda (target segar 1–3 hari) diprioritaskan; token tua di-penalti.

Hasilnya: daftar target (token, chain, DEX, age, vol, buy/sell, likuiditas) yang siap di-Review.

### 2. Prediksi range (dua jalur, tidak boleh dicampur)

**Jalur A — Historical (primary bila ≥30 candle OHLCV asli tersedia):**

Candle asli diambil otomatis dari **GeckoTerminal** (aggregate cascade 15→5→1 menit, tanpa API key, tanpa candle sintetis). Rumus inti:

```
center = harga × (1 - mrw) + fair_value(72 bar) × mrw
width  = ATR% × Trend × Volatility × Entropy × Mean-Reversion multiplier
skew   = clamp(0.12 × (volume_pressure + liquidity_force), ±0.20)
lower  = center × (1 - width × (1 + skew))
upper  = center × (1 + width × (1 - skew))
```

`width` dibatasi 1%–90% dan harga range dijaga selalu > 0 (ATR% memecoin bisa >100%). Output: `range.lower`, `range.upper`, `range.width_pct`, regime (`CHOPPY`/`NORMAL`/`TRENDING`/`EXTREME`), dan ATR/z-score/entropy asli. Confidence: **`MEDIUM · REAL OHLCV`**.

**Jalur B — Live snapshot (fallback & pembanding):**

Kalau candle asli < 30 (kuota habis / pool sangat baru), Review tetap jalan memakai snapshot DexScreener:

```
width = clamp(0.018 + 0.22 × max(|h1|,|h6|,|h24|)/100, 0.025, 0.55)
```

ATR/entropy/z-score dikosongkan (`null`) — tidak pernah diisi angka palsu. Confidence: **`LOW · LIVE SNAPSHOT`**.

### 3. Monte Carlo

Dua model, selalu dilabeli mana yang primary:

| Model | Data | Kapan | Confidence |
|---|---|---|---|
| **Bootstrap** (Jalur A) | log-return OHLCV asli | ≥ 30 candle | MEDIUM |
| **Merton jump-diffusion** (Jalur B) | snapshot live (m5/h1/h6/h24, buy ratio, turnover, likuiditas) | selalu (jadi `monte_carlo_merton` saat Jalur A aktif) | LOW |

Output keduanya: `p_below` / `p_inside` / `p_above` (posisi harga di **akhir** horizon), `p_out_of_range` = `p_ever_out_of_range` (probabilitas **pernah** menyentuh batas), kuantil P5/P50/P95, harga ekspektasi, dan `mean_first_escape_bars` (rata-rata waktu jebol pertama).

**Kontrak probabilitas (penting):**

- `p_below + p_inside + p_above = 1`.
- `p_out_of_range` **sama dengan** `p_ever_out_of_range` dan dipakai oleh risk/rebalance/keputusan.
- **Jangan jumlahkan** `p_out_of_range` dengan below/inside/above.

### 4. Analisis risiko

- **Risk score 0–100** dari P(out-of-range), volatilitas, z-score, trend, mean-reversion.
- **IL proxy** (benchmark constant-product, bukan IL DLMM on-chain) dari harga ekspektasi terminal.
- **Fee yield horizon** memakai **fee APR asli** pool Meteora (lewat `dlmm.datapi.meteora.ag`) bila pool-nya Meteora.
- **Fee/IL ratio** — catatan otomatis bila fee proyeksi belum menutup IL.
- **Rebalance engine** — YES/NO + urgency (NONE/MEDIUM/HIGH) dengan alasan: mendekati boundary, regime EXTREME, volatilitas melonjak, P(out-of-range) tinggi, tekanan volume + trend.

### 5. Pengambilan keputusan

`_final_intelligence` menggabungkan survival range, fee opportunity, range quality, dan directional safety menjadi:

- **MASUK** — kondisi layak buka posisi.
- **TUNGGU** — terlalu berisiko / belum stabil (default untuk kandidat segar).
- **REBALANCE** — dipakai saat posisi aktif sudah ada.

Selalu disertai `confidence`, `data_quality` (sumber data, jumlah candle nyata, fee aktual atau tidak), alasan, dan peringatan.

---

## Instalasi

**Syarat:**

- **Python 3.10+** — itu saja. Seluruh projek memakai **standard library saja** (tidak ada `pip install`, tidak ada `requirements.txt`, tidak perlu API key).
- **Node.js 18+** — hanya untuk menjalankan test UI (`test_dashboard_ui.js`). Opsional.

```bash
git clone https://github.com/LoOp575/Pool.git
cd Pool

# verifikasi instalasi
python3 -m unittest          # 23 test engine + scanner
node test_dashboard_ui.js    # test harness dashboard (opsional)
```

Endpoint yang dipakai semuanya publik tanpa autentikasi: DexScreener, GeckoTerminal, Meteora datapi.

---

## Pemakaian

### 1. `scanner.py` — radar discovery

Scan penuh, output JSON ke stdout:

```bash
python3 scanner.py > scan.json
```

Atau lebih fleksibel lewat Python (signature: `scan(limit=None, meteora=None)`):

```bash
# 20 kandidat teratas, hanya pool Meteora DLMM
python3 -c "
import json
from scanner import scan
print(json.dumps(scan(20, True), indent=2))
"
```

Field penting hasil `scan()`:

- `rows[]` — kandidat: `base`, `chain`, `dex`, `pair`, `price`, `age_days`, `volume_1h/6h/24h`, `buys_1h`, `liquidity`, `meteora`, `url`.
- `funnel` — berapa pair tersaring di tiap gate (`discovered → chains → liquidity → volume → activity → final`).
- `filters` — konfigurasi gate yang dipakai.

Review satu pool dari CLI (jalankan Jalur A + B, sama seperti tombol REVIEW di dashboard):

```bash
python3 -c "
import json
from scanner import review_pair
r = review_pair('PAIR_ADDRESS_POOL_METERA', horizon_bars=96, mc_paths=2000)
print(json.dumps({k: r[k] for k in ('confidence','range','monte_carlo','risk','decision')}, indent=2))
"
```

### 2. `dlmm_cli.py` — engine prediksi lengkap

Menjalankan seluruh pipeline (`analyze`) dari file candle JSON — berguna untuk backtest manual, eksperimen rumus, atau saat kamu sudah punya active bin on-chain.

**Format input** — JSON array candle (minimal 30):

```json
[
  {"timestamp": 1690000000, "open": 0.0000121, "high": 0.0000125, "low": 0.0000118, "close": 0.0000123, "volume": 15420.5}
]
```

**Perintah:**

```bash
python3 dlmm_cli.py candles.json \
  --active-bin 74213 \
  --bin-step-bps 200 \
  --fee-apr 7058 \
  --horizon 96 \
  --paths 2000 \
  --bar-minutes 15 \
  --distribution SPOT
```

| Argumen | Wajib | Default | Keterangan |
|---|---|---|---|
| `json` (posisi) | ✅ | — | path file candle JSON |
| `--active-bin` | ✅ | — | active bin pool (on-chain; **jangan ditebak dari harga**) |
| `--bin-step-bps` | ✅ | — | bin step pool, mis. `200` = 2% per bin |
| `--fee-apr` | ❌ | `0` | fee APR pool (persen), untuk fee yield & fee/IL ratio |
| `--horizon` | ❌ | `24` | horizon simulasi dalam bar |
| `--paths` | ❌ | `2000` | jumlah jalur Monte Carlo |
| `--distribution` | ❌ | auto | `SPOT` / `CURVE` / `BID-ASK` (auto pilih bila tidak diisi) |
| `--bar-minutes` | ❌ | `15` | interval 1 candle dalam menit (untuk konversi horizon → hari) |

Output: laporan teks (`format_report`) + JSON lengkap (`math`, `regime`, `range`, `bins`, `liquidity`, `monte_carlo`, `risk`, `rebalance`).

### 3. Dashboard lokal (`web_terminal.py`)

```bash
python3 web_terminal.py --host 0.0.0.0 --port 8000
```

Buka `http://localhost:8000`. Alur pemakaian:

1. Klik **SCAN** — memuat target dari DexScreener (gate yang sama dengan `scanner.py`).
2. Klik salah satu baris target — kartu **SELECTED** menampilkan age, volume, buy/sell, likuiditas.
3. Klik **REVIEW** (atau kartu SELECTED) — halaman Review penuh: keputusan, kenapa, range LP + posisi harga, probabilitas turun/tetap/naik, waktu jebol, MATH HISTORICAL (bila ada candle asli), nilai untuk LP, fee APR/fee yield/IL proxy.

Endpoint lokal:

| Endpoint | Parameter | Keterangan |
|---|---|---|
| `GET /` | — | dashboard HTML |
| `GET /api/scan` | `limit` (1–100, default 40), `meteora=1` | radar discovery |
| `GET /api/review` | `pair` (wajib), `horizon` (1–192, default 96), `paths` (200–5000, default 2000) | Review satu pool |
| `GET /healthz` | — | `{"ok": true}` |

### 4. Deploy ke Vercel

Projek ini sudah terkonfigurasi untuk Vercel (Python serverless + static):

- **`vercel.json`** me-rewrite:
  - `/api/:path*` → `/api/index.py` (handler serverless)
  - `/` → `/static/index.html` (dashboard)
  - `/:path*` → `/static/:path*`
- **`api/index.py`** menyediakan endpoint produksi:

| Endpoint | Parameter |
|---|---|
| `GET /api/scan` | — |
| `GET /api/review` | `pair` (wajib), `horizon` (1–192), `paths` (200–5000) |
| `GET /api/healthz` | — |

Langkah deploy:

```bash
# dari root repo, dengan Vercel CLI yang sudah login
vercel            # preview
vercel --prod     # produksi
```

Tidak ada build step, tidak ada dependency eksternal, tidak ada env var yang perlu diisi — cukup push repo ke GitHub lalu hubungkan di dashboard Vercel (atau lewat Freebuff Deploy). Route handler memakai pola `class handler(BaseHTTPRequestHandler)` sehingga juga kompatibel dengan pola Vercel Python umum.

---

## Data sources

| Sumber | Dipakai untuk | Auth |
|---|---|---|
| **DexScreener** | discovery kandidat, snapshot live (harga, vol, tx, likuiditas) | publik |
| **GeckoTerminal** | OHLCV candle asli untuk Jalur A (aggregate 15→5→1 menit, min 30 candle) | publik, tanpa API key |
| **Meteora datapi** (`dlmm.datapi.meteora.ag`) | fee APR/APY asli, `bin_step`, `base_fee` pool Meteora | publik |

---

## Testing

```bash
python3 -m unittest          # 23 test: engine, Monte Carlo, kontrak probabilitas, jalur historis scanner
node test_dashboard_ui.js    # harness DOM dashboard: render radar + Review payload
```

Cakupan test: ATR/entropy/bin pipeline, range memecoin selalu positif, distribusi terminal Monte Carlo, timing escape pertama, fee yield berbasis horizon, OHLCV parse/cascade fetch, Review jalur historis (monkeypatch), kontrak probabilitas `p_out == p_ever`, dan render UI.

---

## Keterbatasan yang diketahui

Projek **siap dipakai untuk analisa dan screening**, dengan batas-batas berikut yang memang disengaja:

1. **Bin plan tidak dihitung di Review.** `bin_step` diketahui dari Meteora, tetapi `active bin` harus dibaca on-chain — engine tidak pernah menebak active bin dari harga. Karena itu payload Review memuat `bin: null` + `bin_note`. Bin plan lengkap tersedia lewat `dlmm_cli.py` bila kamu supply `--active-bin` sendiri.
2. **Kuota GeckoTerminal publik ~26 call review/menit.** Kalau kuota habis atau fetch gagal, Review otomatis fallback ke Jalur B (snapshot Merton, confidence turun ke `LOW · LIVE SNAPSHOT`). Tidak pernah ada candle sintetis — data Historical selalu `null`, bukan angka karangan.
3. **Belum ada backtest.** Threshold, multiplier, dan fee model belum divalidasi terhadap data historis panjang. `DLMM_ENGINE.md` mewajibkan: backtest bobot/threshold, validasi fee model, dan cocokkan bin math dengan pool target **sebelum modal nyata**.
4. **IL adalah proxy constant-product**, bukan perhitungan IL DLMM on-chain yang sebenarnya. Token A/B weights pada distribusi likuiditas adalah bobot inventory-side ter-normalisasi, bukan jumlah liquidity on-chain.
5. **Probabilitas Merton (Jalur B) adalah probabilitas skenario LOW-confidence**, bukan probabilitas historis yang terestimasi. Hanya Jalur A (OHLCV asli) yang memberi kalibrasi historis, dan itu pun `MEDIUM` — bukan jaminan.
6. **Verifikasi visual browser tidak dilakukan otomatis** — UI diuji lewat harness DOM berbasis Node, bukan browser sungguhan.

**Disclaimer:** hasil Review adalah probabilitas, bukan kepastian harga. Ini alat bantu analisa LP, bukan nasihat finansial.

---

## Dokumen terkait

- **[`FORMULA_ENGINE.md`](FORMULA_ENGINE.md)** — arsitektur formula, status tiap rumus, detail Jalur A/B, kontrak probabilitas, dan definisi engine `POOL-INTEL-4`.
- **[`DLMM_ENGINE.md`](DLMM_ENGINE.md)** — ringkasan engine DLMM, formula range width, aturan bin mapping, dan checklist sebelum pakai modal.
