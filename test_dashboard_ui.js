// UI smoke test: runs the dashboard <script> against realistic /api payloads
// with a stub DOM. Fails (exit 1) when the script does not parse or the
// dashboard does not render scan + review output.
// Run: node test_dashboard_ui.js [path/to/index.html]
const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync(process.argv[2] || 'static/index.html', 'utf8');
const code = html.split('<script>')[1].split('</script>')[0];

const elements = {};
function el(id) {
  return {
    id, textContent: '', innerHTML: '', value: '',
    style: {}, classList: { add() {}, remove() {} },
    onclick: null, addEventListener() {},
  };
}

const SCAN = {
  count: 2,
  rows: [
    { token: 'T1', chain: 'solana', base: 'BOB', name: 'Bob Cat', quote: 'SOL', pair: 'pair1', dex: 'meteora-dlmm',
      price: 0.001, age_h: 30, age_days: 1.25, volume_1h: 120000, volume_6h: 500000, volume_24h: 1500000,
      buys_1h: 310, sells_1h: 180, buy_ratio_1h: 0.6324, liquidity: 80000, h1: 5.2, h6: 11, h24: 25, meteora: true },
    { token: 'T2', chain: 'solana', base: 'MOON', name: 'No Age Moon', quote: 'SOL', pair: 'pair2', dex: 'raydium',
      price: 0.00002, age_h: null, age_days: null, volume_1h: 40000, volume_6h: 90000, volume_24h: 300000,
      buys_1h: 120, sells_1h: 90, buy_ratio_1h: 0.5714, liquidity: 25000, h1: -3.1, h6: 8, h24: 40, meteora: false },
  ],
  funnel: { discovered: 10, discovery: { pairs: 10, chains: { solana: 10 }, errors: [] } },
  discovery_status: 'OK',
};

const REVIEW = {
  price: 0.001,
  history: 140,
  data_source: 'DexScreener + GeckoTerminal',
  regime: 'NORMAL',
  range: { lower: 0.0009, center: 0.001, upper: 0.00112, width_pct: 0.11 },
  risk: { score: 41.2, out_of_range: 0.22, fee_yield: 0.031, il_proxy: -0.008, fee_apr: 0.42 },
  rebalance: { rebalance: false, urgency: 'NONE', reasons: [] },
  analysis: {
    lp_score: 72.5, meme_score: 61, fresh_score: 68,
    lp_components: { fee_potential: 78, range_quality_proxy: 66, directional_safety: 71 },
    fresh_reasons: ['young <3d'], meme_reasons: ['meme keyword'],
  },
  math: {
    price: 0.001, atr: 2.4e-7, atr_pct: 0.00024, volatility: 0.02,
    volatility_ratio: 1.3, z_score: 0.42, trend_strength: 0.51,
    volume_pressure: 0.2, entropy: 0.62, liquidity_force: 0.1,
    mean_reversion_force: -0.12, rvol: 1.4,
  },
  live_metrics: { h1: 5.2, h6: 11, h24: 25, v1: 120000, liquidity: 80000, buy_ratio: 0.6324, vol_liq: 1.5, buy_pressure: 0.26 },
  monte_carlo: {
    p_below: 0.22, p_inside: 0.56, p_above: 0.22, p_survive_range: 0.78,
    p_ever_out_of_range: 0.22, p05: 0.00088, p50: 0.00101, p95: 0.00115,
    mean_first_escape_bars: 30, paths: 400, horizon_minutes: 1440,
    model: 'bootstrap Monte Carlo (historical log-returns)',
  },
  intelligence: {
    decision: 'MASUK', score: 74.3, regime: 'NORMAL',
    action_reason: 'fee opportunity cukup kuat dengan range yang masih bertahan',
    reasons: ['aktivitas volume mendukung peluang fee'], warnings: [],
    data_quality: { confidence: 'MEDIUM', real_ohlcv_available: true, historical_candles: 140 },
  },
};

const sandbox = {
  console, Date, Math, Number, JSON, String, Error, encodeURIComponent, isNaN, parseFloat, parseInt,
  setInterval: () => 0, clearInterval: () => 0, setTimeout: () => 0,
  document: {
    getElementById(id) {
      if (id === 'mode') return null; // the dashboard must tolerate a missing element
      if (!elements[id]) elements[id] = el(id);
      return elements[id];
    },
  },
  fetch: (url) => {
    const isReview = String(url).startsWith('/api/review');
    return Promise.resolve({ ok: true, json: () => Promise.resolve(isReview ? REVIEW : SCAN) });
  },
};

vm.createContext(sandbox);
vm.runInContext(code, sandbox, { filename: 'dashboard.js' });

const assert = (cond, msg) => { if (!cond) { console.error('FAIL: ' + msg); process.exit(1); } console.log('ok - ' + msg); };

(async () => {
  await new Promise(r => setTimeout(r, 30));
  assert(String(elements.status.textContent).startsWith('updated'), 'auto load() ran on page init');
  assert(elements.rows.innerHTML.includes('BOB') && elements.rows.innerHTML.includes('MOON'), 'table rows rendered');
  assert(elements.selected.innerHTML.includes('BOB'), 'first row auto-selected');
  sandbox.selectPool(1);
  assert(elements.selected.innerHTML.includes('MOON'), 'row with null age_days selected without crash');
  assert(elements.selected.innerHTML.includes('AGE ?'), 'unknown age rendered safely');
  assert(!String(elements.status.textContent).startsWith('error'), 'no render error');

  await sandbox.review('pair1', 'BOB / SOL');
  await new Promise(r => setTimeout(r, 30));
  const rv = elements.review.innerHTML;
  assert(rv.includes('KEPUTUSAN POOL'), 'review header rendered');
  assert(rv.includes('MEME IDENTITY') && rv.includes('FRESH ENTRY'), 'memecoin scores shown in review');
  assert(rv.includes('RANGE LP'), 'range card rendered');
  assert(rv.includes('MASUK'), 'decision rendered');
  assert(rv.includes('Lebar 11.0%'), 'range width rendered as a real percentage');
  assert(rv.includes('DALAM 24 JAM'), 'horizon label derived from horizon_minutes');
  assert(rv.includes('bootstrap Monte Carlo'), 'simulation model shown');
  assert(rv.includes('140 candle asli'), 'real candle count shown');
  assert(rv.includes('MATH HISTORICAL'), 'historical math card rendered when OHLCV available');
  assert(rv.includes('YA · 140 CANDLE'), 'data quality shows real OHLCV');
  assert(rv.includes('Fee APR pool') && rv.includes('42.0%'), 'actual pool fee APR rendered');
  assert(rv.includes('Fee yield horizon') && rv.includes('3.1%'), 'fee yield rendered');
  console.log('ALL DASHBOARD CHECKS PASSED');
  process.exit(0);
})().catch(e => { console.error('FAIL: ' + e.stack); process.exit(1); });
