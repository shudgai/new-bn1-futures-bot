const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
const script = html.slice(html.lastIndexOf('<script>') + 8, html.lastIndexOf('</script>'));
let options;
let response;
const timers = [];
const errors = [];
vm.runInNewContext(script, {
    Vue: { createApp(config) { options = config; return { mount() {} }; } },
    localStorage: { getItem() { return null; } },
    fetch: async () => ({ ok: true, json: async () => response }),
    console: { error(...args) { errors.push(args); } },
    setTimeout(callback) { timers.push(callback); },
});
const app = options.data();
for (const [name, method] of Object.entries(options.methods)) app[name] = method.bind(app);

async function main() {
    response = {
        ticker_updated_at: 100,
        positions: [{ symbol: 'CAP/USDT', locked_phase: 1, mark_price: 98, unrealized_pnl: -14 }],
        tickers: { 'CAP/USDT': 98 },
        unrealized_pnl: -14, estimated_net_unrealized_pnl: -14.5, balance: 182,
        market_data_stale: false, ticker_age_seconds: { 'CAP/USDT': 0.1 },
    };
    await app.fetchPrices();
    assert.equal(errors.length, 0, 'Vue 3 polling must not call removed $set');
    assert.equal(app.bot.unrealized_pnl, -14);
    assert.equal(app.bot.positions[0].mark_price, 98);
    assert.equal(app.lockedProfitBump['CAP/USDT'], true);
    timers.pop()();
    assert.equal(app.lockedProfitBump['CAP/USDT'], false);

    response = { ...response, ticker_updated_at: 101, unrealized_pnl: -21,
        estimated_net_unrealized_pnl: -21.5,
        positions: [{ symbol: 'CAP/USDT', locked_phase: 1, mark_price: 97, unrealized_pnl: -21 }],
        tickers: { 'CAP/USDT': 97 } };
    await app.fetchPrices();
    assert.equal(app.bot.unrealized_pnl, -21, 'P&L must follow the next live response');
    assert.equal(app.bot.positions[0].mark_price, 97);
    assert.equal(timers.length, 0, 'Unchanged phases do not flash again');

    response = { ...response, ticker_updated_at: 99, unrealized_pnl: 0,
        estimated_net_unrealized_pnl: 0, market_data_stale: true,
        positions: [], tickers: {}, realized_pnl: -18 };
    await app.fetchStatus();
    assert.equal(app.bot.realized_pnl, -18, 'Full status still updates the realized ledger');
    assert.equal(app.bot.unrealized_pnl, -21, 'Older status must not rewind live P&L');
    assert.equal(app.bot.estimated_net_unrealized_pnl, -21.5);
    assert.equal(app.bot.positions[0].mark_price, 97);
    assert.equal(app.bot.market_data_stale, false);
    await app.fetchPrices();
    assert.equal(app.bot.unrealized_pnl, -21, 'Older price response must also be rejected');
    assert.equal(errors.length, 0);
    console.log('Dashboard refresh: PASS (Vue 3 polling, changing P&L, phase flash, stale responses)');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
