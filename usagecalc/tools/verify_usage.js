// Drive the usage dashboard in a real browser and check that what it SHOWS
// agrees with what its data SAYS. Reading the JSON alone cannot catch a panel
// that renders the right number in the wrong scope, or a selector that changes
// a caption and not the figures beneath it - both of which have happened.
//
//   node verify_usage.js [path-or-url-to-dashboard]
//
// Defaults to usage/usage-dashboard.html under the current directory, which is
// where `usage-calc init` puts it.
const { chromium } = require('playwright');

function resolve(arg) {
  if (!arg) return 'file:///' + process.cwd().replace(/\\/g, '/') + '/usage/usage-dashboard.html';
  if (/^(https?|file):/.test(arg)) return arg;
  const path = require('path');
  return 'file:///' + path.resolve(arg).replace(/\\/g, '/');
}

(async () => {
  const browser = await chromium.launch({ channel: 'msedge' });
  const base = resolve(process.argv[2]);
  let fail = 0;

  for (const theme of ['light', 'dark']) {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    const errs = [], pageErrs = [], bad = [];
    page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
    page.on('pageerror', e => pageErrs.push(e.message));
    page.on('response', r => { if (r.status() >= 400) bad.push(r.status() + ' ' + r.url()); });

    await page.goto(base + '?scoutTheme=' + theme, { waitUntil: 'networkidle' });
    await page.waitForTimeout(400);

    const r = await page.evaluate(() => {
      const txt = document.body.innerText;
      const ids = ['hero','chanTable','dual','initBars','effortBars','cfStats','timeTable',
                   'activeBars','turnBars','turnTable','modelTable','agentTable','energyTable',
                   'outStats','perUnit','missTable','instrument','weak','wrongclaim','foot',
                   'daysub','dayBars','dayTable','daynote','fleetsub','fleetTable','fleetnote'];
      const empty = ids.filter(i => {
        const el = document.getElementById(i);
        return !el || el.innerHTML.trim().length < 20;
      });
      return {
        theme: document.documentElement.getAttribute('data-theme'),
        chars: txt.length,
        cards: document.querySelectorAll('.card').length,
        tables: document.querySelectorAll('table').length,
        rows: document.querySelectorAll('tbody tr').length,
        barsCount: document.querySelectorAll('.bar').length,
        segs: document.querySelectorAll('.seg div').length,
        empty,
        nan: (txt.match(/NaN|undefined|Infinity|\[object|null%|\$NaN/g) || []),
        leaked: (txt.match(/<span|&lt;|&mdash;|&rsquo;|&amp;/g) || []),
        zeroDollar: (txt.match(/\$0\.00\b/g) || []).length,
        hasWithdrawn: txt.includes('That is withdrawn'),
        bg: getComputedStyle(document.body).backgroundColor,
        font: getComputedStyle(document.body).fontFamily.slice(0, 20)
      };
    });

    // The day panel earns its own checks. A generic empty/NaN sweep cannot see
    // a day table that is populated and WRONG, and three specific ways for it
    // to be wrong are cheap to rule out:
    //   1. the rows must reconcile to the totals the rest of the page reports;
    //   2. the cut-off selector must actually change the output - a control
    //      that renders identical numbers at every setting is not a control;
    //   3. model hours must NOT change with the cut-off, because they are a
    //      union of measured intervals and have nothing to do with idle time.
    // (3) is the one that would catch the person and model columns being
    // swapped, which no amount of formatting inspection would notice.
    const days = await page.evaluate(async () => {
      const sel = document.getElementById('dayCut');
      if (!sel) return { missing: true };
      const read = () => {
        const f = document.querySelector('#dayTable tfoot tr');
        const c = Array.from(f.querySelectorAll('td')).map(x => x.textContent.trim());
        return { req: c[1], cost: c[3], eng: parseFloat(c[4]), mod: parseFloat(c[5]),
                 per: parseFloat(c[6]) };
      };
      const out = {};
      for (const o of Array.from(sel.options)) {
        sel.value = o.value;
        sel.dispatchEvent(new Event('change'));
        out[o.value] = read();
      }
      sel.value = String(DATA.days.default_cutoff_s);
      sel.dispatchEvent(new Event('change'));
      const D = DATA.days.rows;
      return {
        cutoffs: out,
        bars: document.querySelectorAll('#dayBars .bar').length,
        segs: document.querySelectorAll('#dayBars .fill.stack i').length,
        tRows: document.querySelectorAll('#dayTable tbody tr').length,
        srcDays: D.length,
        srcReq: D.reduce((a, r) => a + r.requests, 0),
        totReq: DATA.totals.requests,
        srcNano: D.reduce((a, r) => a + r.nano_aiu, 0),
        totNano: DATA.totals.nano_aiu,
        identity: D.every(r => Object.values(r.times).every(
          t => Math.abs(t.engaged_s - t.model_s - t.person_s) < 0.2 && t.person_s >= 0)),
        ordered: D.every((r, i) => i === 0 || r.date > D[i - 1].date),
        zone: !!(DATA.days.zone && DATA.days.zone.name),
        union: DATA.time.inference_union_s,
        // Raw seconds, not the two-decimal HOURS shown in the footer. The
        // footer rounds to 36-second resolution, which is coarse enough to
        // hide a real double-counting bug - it hid a 12-second one.
        modelBySeconds: DATA.days.cutoffs.map(c =>
          D.reduce((a, r) => a + (r.times[String(c)] || {model_s: 0}).model_s, 0))
      };
    });

    const dayProblems = [];
    if (days.missing) {
      dayProblems.push('no #dayCut selector on the page');
    } else {
      const vals = Object.values(days.cutoffs);
      if (days.bars !== days.srcDays) dayProblems.push(`${days.bars} bars for ${days.srcDays} days`);
      if (days.tRows !== days.srcDays) dayProblems.push(`${days.tRows} table rows for ${days.srcDays} days`);
      if (days.segs !== days.srcDays * 2) dayProblems.push(`${days.segs} segments, expected ${days.srcDays * 2}`);
      if (days.srcReq !== days.totReq) dayProblems.push(`daily requests ${days.srcReq} != total ${days.totReq}`);
      if (days.srcNano !== days.totNano) dayProblems.push(`daily nano-AIU ${days.srcNano} != total ${days.totNano}`);
      if (!days.identity) dayProblems.push('engaged = model + person does not hold on every row');
      if (!days.ordered) dayProblems.push('days are not in ascending date order');
      if (!days.zone) dayProblems.push('no local zone reported with the daily split');
      if (new Set(vals.map(v => v.eng)).size < 2)
        dayProblems.push('engaged hours identical at every cut-off - the selector does nothing');
      if (new Set(vals.map(v => v.mod)).size !== 1)
        dayProblems.push('model hours MOVE with the idle cut-off - they must not');
      const spread = Math.max(...days.modelBySeconds) - Math.min(...days.modelBySeconds);
      if (spread > 1)
        dayProblems.push(`model seconds spread ${spread.toFixed(1)}s across cut-offs, must be 0`);
      if (Math.abs(days.modelBySeconds[0] - days.union) > 1)
        dayProblems.push(`daily model ${days.modelBySeconds[0].toFixed(1)}s != union ${days.union}s`);
      if (new Set(vals.map(v => v.req)).size !== 1 || new Set(vals.map(v => v.cost)).size !== 1)
        dayProblems.push('requests or cost changed with the idle cut-off');
    }

    // The fleet card has TWO legitimate states and both must be checked. With
    // no contributions it names the sibling repositories and admits the gap;
    // with contributions it merges them. A verifier that only knew one state
    // would pass forever on the other - the same "test that passes everywhere"
    // failure this project keeps cataloguing.
    const fleet = await page.evaluate(() => {
      // DATA is a top-level const in a classic script, which is script-scoped
      // and NOT a property of window. Reading window.DATA returns undefined on
      // a perfectly healthy page - and it did, in both themes and both modes,
      // which is the signature of a broken probe rather than a broken page.
      const D = (typeof DATA !== 'undefined') ? DATA : null;
      if (!D) return { missing: true };
      const rows = [...document.querySelectorAll('#fleetTable tbody tr')].length;
      const cols = [...document.querySelectorAll('#fleetTable thead th')]
                     .map(t => t.textContent.trim());
      const txt = ($('fleetsub').textContent + ' ' + $('fleetnote').textContent);
      if (D.fleet) {
        const F = D.fleet, tm = F.time;
        return {
          mode: 'merged', rows, cols, txt,
          srcCount: F.sources.length,
          reqAdd: F.sources.reduce((a, s) => a + s.requests, 0) === F.totals.requests,
          nanoAdd: F.sources.reduce((a, s) => a + s.nano_aiu, 0) === F.totals.nano_aiu,
          // Wall time can never exceed work time: it is a union of the same
          // intervals. If it does, the merge is adding what it should unite.
          wallOk: tm.model_wall_s <= tm.model_work_s + 0.001,
          concOk: Math.abs((tm.model_work_s - tm.model_wall_s) - tm.concurrent_s) < 0.5,
          // Merged model time must still be invariant to the idle cut-off.
          modFlat: new Set(tm.cutoffs.map(c => tm.times[String(c)].model_s)).size === 1,
          identity: tm.cutoffs.every(c => {
            const v = tm.times[String(c)];
            return Math.abs(v.engaged_s - v.model_s - v.person_s) < 0.2 && v.person_s >= 0;
          }),
          // The idle cut-off belongs to the PERSON, so it must be taken over
          // the pooled stream. Cutting each machine separately reports LESS
          // engaged time - it turns a gap the person spent turning to the
          // other screen into a pause. Pooled must therefore be >= per-machine
          // at every cut-off, and the day card must agree with this card.
          pooled: tm.cutoffs.every(c => {
            const v = tm.times[String(c)];
            return v.engaged_per_machine_s === undefined ||
                   v.engaged_s >= v.engaged_per_machine_s - 0.5;
          }),
          bridged_s: tm.times[String(tm.default_cutoff_s)].engaged_s -
                     (tm.times[String(tm.default_cutoff_s)].engaged_per_machine_s || 0),
          engLeSum: true,
        };
      }
      const S = D.siblings || { rows: [] };
      return {
        mode: 'siblings', rows, cols, txt,
        srcCount: S.rows.length,
        named: S.rows.every(r => r.name && r.name.length > 3),
        saysFloor: /floor/i.test(txt),
        saysNotMeasured: [...document.querySelectorAll('#fleetTable tbody td')]
                           .some(td => /not measured/i.test(td.textContent)),
      };
    });

    const fleetProblems = [];
    if (fleet.missing) {
      fleetProblems.push('no DATA on the page');
    } else if (fleet.mode === 'merged') {
      if (fleet.rows !== fleet.srcCount)
        fleetProblems.push(`${fleet.rows} rows for ${fleet.srcCount} sources`);
      if (!fleet.reqAdd) fleetProblems.push('merged requests do not sum');
      if (!fleet.nanoAdd) fleetProblems.push('merged cost does not sum');
      if (!fleet.wallOk) fleetProblems.push('model WALL time exceeds model WORK time');
      if (!fleet.concOk) fleetProblems.push('concurrent seconds != work - wall');
      if (!fleet.modFlat) fleetProblems.push('merged model time moves with the cut-off');
      if (!fleet.identity) fleetProblems.push('merged engaged = model + person fails');
      if (!fleet.pooled)
        fleetProblems.push('engaged time is cut per machine, not per person');
      if (!/additive/i.test(fleet.txt))
        fleetProblems.push('merged card does not state the additivity rule');
    } else {
      if (fleet.rows !== fleet.srcCount)
        fleetProblems.push(`${fleet.rows} rows for ${fleet.srcCount} siblings`);
      if (!fleet.srcCount) fleetProblems.push('no sibling repositories listed');
      if (!fleet.named) fleetProblems.push('a sibling row has no repository name');
      if (!fleet.saysFloor)
        fleetProblems.push('the card does not say the totals are a FLOOR');
      if (!fleet.saysNotMeasured)
        fleetProblems.push('unmeasured columns are not labelled "not measured"');
    }

    // --- the repository selector ---------------------------------------------
    // ONE selector, mounted into every panel that has a scope. Three ways this
    // goes wrong quietly and none of them look like a broken page:
    //   1. the selectors drift apart, so the day chart and the ledger beside it
    //      describe different repositories under one heading;
    //   2. a panel that cannot serve a selection shows the PRIMARY repository's
    //      numbers under a sibling's label, which reads as a perfectly normal
    //      card full of plausible figures;
    //   3. the selector changes nothing at all, and is decoration.
    const scope = await page.evaluate(async () => {
      const D = (typeof DATA !== 'undefined') ? DATA : null;
      const sels = [...document.querySelectorAll('.repoScope')];
      if (!D || !D.scopes || sels.length === 0) return { absent: true };
      const S = D.scopes;
      const day = document.getElementById('dayScope');
      const pick = async v => {
        day.value = v; day.dispatchEvent(new Event('change'));
        await new Promise(r => setTimeout(r, 80));
        const f = [...document.querySelectorAll('#dayTable tfoot td')].map(t => t.textContent);
        return {
          values: [...document.querySelectorAll('.repoScope')].map(e => e.value),
          rows: document.querySelectorAll('#dayTable tbody tr').length,
          req: f.length ? +f[1].replace(/[^0-9]/g, '') : null,
          turns: f.length ? +f[2].replace(/[^0-9]/g, '') : null,
          cost: f.length ? +f[3].replace(/[^0-9.]/g, '') : null,
          model: f.length ? +f[5] : null,
          sub: document.getElementById('daysub').textContent,
          outSub: (document.getElementById('outsub') || {}).textContent || '',
          outStats: (document.getElementById('outStats') || {}).textContent || '',
          planHidden: (document.getElementById('planscopenote') || {}).hidden,
          outHidden: (document.getElementById('outscopenote') || {}).hidden,
        };
      };
      const main = S.entries.find(e => e.kind === 'main');
      const sib = S.entries.find(e => e.kind === 'sibling');
      const out = {
        nsel: sels.length,
        needs: sels.map(e => e.dataset.need),
        known: [...new Set(S.entries.flatMap(e => Object.keys(e)))],
        offered: sels.map(e => [...e.options].map(o => o.value).join(',')),
        want: S.entries.map(e => e.key).join(','),
        mainKey: main ? main.key : null,
        sibKey: sib ? sib.key : null,
        all: await pick('all'),
        wantAllReq: D.fleet ? D.fleet.totals.requests : D.totals.requests,
        wantAllTurns: D.fleet ? D.fleet.totals.turns : D.totals.turns,
        wantAllCost: D.fleet ? D.fleet.totals.usd : D.totals.usd,
        wantAllModel: D.fleet ? D.fleet.time.model_wall_s / 3600 : null,
        wantMainReq: D.totals.requests,
      };
      if (main) out.main = await pick(main.key);
      if (sib) out.sib = await pick(sib.key);
      await pick('all');
      return out;
    });

    const scopeProblems = [];
    if (!scope.absent) {
      // every panel offers the SAME list - a panel with its own list is two
      // controls wearing one name
      scope.offered.forEach((o, k) => {
        if (o !== scope.want)
          scopeProblems.push(`selector ${k} offers ${o} not ${scope.want}`);
      });
      if (scope.needs.some(n => !n))
        scopeProblems.push('a selector does not declare which capability it needs');
      // NOT uniqueness. Several panels legitimately need the same capability -
      // the hero, the counterfactual and the day chart all need "usage" - and
      // asserting one selector per capability only ever described how many
      // panels happened to exist when the check was written. What matters is
      // that a declared capability is one the scope entries actually carry,
      // because a selector needing a field nobody publishes can never hide.
      // The list is read FROM the payload so it cannot go stale here.
      scope.needs.forEach((n, k) => {
        if (n && !scope.known.includes(n))
          scopeProblems.push(`selector ${k} needs "${n}", which no scope entry declares ` +
                             `(entries carry: ${scope.known.join(', ')})`);
      });
      if (scope.all.req !== scope.wantAllReq)
        scopeProblems.push(`merged ${scope.all.req} req != fleet ${scope.wantAllReq}`);
      // The turn bug this check exists for reported one turn per REQUEST.
      if (scope.all.turns !== scope.wantAllTurns)
        scopeProblems.push(`merged ${scope.all.turns} turns != fleet ${scope.wantAllTurns}`);
      if (Math.abs(scope.all.cost - scope.wantAllCost) > 0.05)
        scopeProblems.push(`merged cost ${scope.all.cost} != fleet ${scope.wantAllCost}`);
      if (scope.wantAllModel !== null && Math.abs(scope.all.model - scope.wantAllModel) > 0.02)
        scopeProblems.push(`merged model ${scope.all.model} h != union ${scope.wantAllModel.toFixed(2)} h`);
      if (/this store|this repository|this one/i.test(scope.all.sub))
        scopeProblems.push('merged caption still says "this store/this repository/this one"');

      if (scope.main) {
        if (!scope.main.values.every(v => v === scope.mainKey))
          scopeProblems.push(`selectors out of sync: ${scope.main.values.join(',')}`);
        if (scope.main.req !== scope.wantMainReq)
          scopeProblems.push(`main scope ${scope.main.req} req != session ${scope.wantMainReq}`);
        if (!scope.main.planHidden)
          scopeProblems.push('plan card warns on the repository it CAN serve');
        if (!scope.main.outHidden)
          scopeProblems.push('outputs card warns on the repository it CAN serve');
      }
      if (scope.sib) {
        if (!scope.sib.values.every(v => v === scope.sibKey))
          scopeProblems.push(`selectors out of sync: ${scope.sib.values.join(',')}`);
        if (scope.sib.req === scope.all.req)
          scopeProblems.push('the selector changes nothing - one repository reads as all of them');
        if (scope.sib.planHidden)
          scopeProblems.push('plan card does not admit it has no plan for a sibling');
        if (scope.sib.outHidden)
          scopeProblems.push('outputs card does not admit what it could not measure');
        if (!/not measured/i.test(scope.sib.outStats))
          scopeProblems.push('unmeasured output columns are not labelled "not measured"');
        if (scope.main && scope.sib.outStats === scope.main.outStats)
          scopeProblems.push('SILENT FALLBACK: sibling shows the main repository\'s output figures');
        if (!scope.sib.outSub.includes(scope.sibKey))
          scopeProblems.push('outputs caption does not name the selected repository');
      }
    }

    // --- the cost panels answer the question their heading asks -----------------
    // The failure this exists for shipped: the "at a glance" band showed all five
    // repositories, and the cost panels two inches below it showed ONE. Both were
    // arithmetically correct and the page contradicted itself, because a panel
    // that reads a primary-only field looks exactly like a panel that reads a
    // pooled one - same shape, same units, plausible numbers, no error anywhere.
    //
    // So the assertions are about WHICH POPULATION a number covers, which is not
    // observable from the number itself. They are: the fleet splits reconcile to
    // the fleet bill; the per-repository cards sum to the "all" card; the band
    // does not move when the selector does; and nothing reads as unmeasured that
    // the selection can in fact measure.
    const cost = await page.evaluate(async () => {
      const D = (typeof DATA !== 'undefined') ? DATA : null;
      if (!D || !D.fleet || !D.scopes) return { absent: true };
      const F = D.fleet, keys = D.scopes.entries.map(e => e.key);
      const sum = (rows, f) => rows.reduce((a, r) => a + f(r), 0);
      const hero = document.getElementById('heroScope');
      const band = document.getElementById('igmoneylg');
      const read = async v => {
        if (!hero) return null;
        hero.value = v; hero.dispatchEvent(new Event('change'));
        await new Promise(r => setTimeout(r, 80));
        const s = [...document.querySelectorAll('#hero .stat b')].map(b => b.textContent.trim());
        return {
          usd: /^\$/.test(s[0] || '') ? +s[0].replace(/[^0-9.]/g, '') : null,
          req: /\d/.test(s[1] || '') ? +s[1].replace(/[^0-9]/g, '') : null,
          raw: s,
          band: band ? band.innerText : null,
        };
      };
      const per = {};
      for (const k of keys) if (k !== 'all') per[k] = await read(k);
      const all = await read('all');
      await read('all');
      return {
        bill: F.totals.usd,
        byModel: F.models ? sum(F.models, r => r.usd) : null,
        byChannel: F.channels ? sum(F.channels, r => r.usd) : null,
        srcSum: sum(F.sources, r => r.usd),
        bandMoney: band
          ? (band.innerText.match(/\$[\d,]+\.\d\d/g) || [])
              .reduce((a, s) => a + +s.replace(/[^0-9.]/g, ''), 0) || null
          : null,
        bandModels: (() => {
          const el = document.getElementById('igmodels');
          if (!el) return null;
          const v = [...el.querySelectorAll('.igm .v')]
            .map(n => (n.textContent.match(/\$[\d,]+\.\d\d/) || [])[0])
            .filter(Boolean)
            .map(s => +s.replace(/[^0-9.]/g, ''));
          return v.length ? v.reduce((a, b) => a + b, 0) : null;
        })(),
        all, per,
        contradictions: [...document.querySelectorAll('#hero .stat')]
          .filter(s => {
            const b = (s.querySelector('b') || {}).textContent || '';
            const e = (s.querySelector('em') || {}).textContent || '';
            return b.trim() !== '\u2014' && /^not measured$/i.test(e.trim());
          })
          .map(s => (s.querySelector('span') || {}).textContent),
        usable: keys.filter(k => k !== 'all' &&
          (D.scopes.entries.find(e => e.key === k) || {}).usage),
      };
    });

    const costProblems = [];
    if (!cost.absent) {
      // A split that does not add up to the bill is a split of some OTHER
      // population - which is exactly the bug, seen from the inside.
      if (cost.byModel !== null && Math.abs(cost.byModel - cost.bill) > 0.02)
        costProblems.push(`models sum $${cost.byModel.toFixed(2)} != fleet $${cost.bill.toFixed(2)}`);
      if (cost.byChannel !== null && Math.abs(cost.byChannel - cost.bill) > 0.02)
        costProblems.push(`channels sum $${cost.byChannel.toFixed(2)} != fleet $${cost.bill.toFixed(2)}`);
      if (Math.abs(cost.srcSum - cost.bill) > 0.02)
        costProblems.push(`sources sum $${cost.srcSum.toFixed(2)} != fleet $${cost.bill.toFixed(2)}`);

      if (cost.all && cost.all.usd !== null) {
        // The parts must be the whole. A hero still reading one repository under
        // "all" passes every single-scope check and fails only this one.
        const parts = cost.usable.map(k => (cost.per[k] || {}).usd).filter(v => v !== null);
        const partSum = parts.reduce((a, b) => a + b, 0);
        if (parts.length !== cost.usable.length)
          costProblems.push('a repository with usage reports no cost in the hero');
        else if (Math.abs(partSum - cost.all.usd) > 0.05)
          costProblems.push(`hero parts $${partSum.toFixed(2)} != hero all $${cost.all.usd.toFixed(2)}`);
        if (Math.abs(cost.all.usd - cost.bill) > 0.05)
          costProblems.push(`hero all $${cost.all.usd} != fleet $${cost.bill.toFixed(2)}`);
        const reqs = cost.usable.map(k => (cost.per[k] || {}).req).filter(v => v !== null);
        if (reqs.length === cost.usable.length && cost.all.req !== null &&
            reqs.reduce((a, b) => a + b, 0) !== cost.all.req)
          costProblems.push('hero request cards do not sum to the merged card');
      }

      // The band answers "what did this project cost". The shipped bug put a
      // PRIMARY-ONLY figure under that heading, two inches below a pooled one.
      //
      // Note what is NOT asserted here: that the band stays put when the
      // selector moves. The band renders once at load, so that assertion can
      // never fail and would be decoration. What can fail - and did ship - is
      // the band's own arithmetic covering the wrong population, so the money
      // it reports is read back off the page and compared to the fleet bill.
      if (cost.bandMoney !== null) {
        if (Math.abs(cost.bandMoney - cost.bill) > 0.05)
          costProblems.push(`band money $${cost.bandMoney.toFixed(2)} != fleet ` +
                            `$${cost.bill.toFixed(2)} - the band is scoped to ` +
                            'something narrower than its heading claims');
      }
      // The band has TWO panels reading a split, and they fail independently:
      // reverting the models panel alone leaves the money total correct, so the
      // money assertion above cannot see it. One check per panel, because a
      // check that happens to catch a neighbour's bug is luck, not coverage.
      if (cost.bandModels !== null && Math.abs(cost.bandModels - cost.bill) > 0.05)
        costProblems.push(`band models $${cost.bandModels.toFixed(2)} != fleet ` +
                          `$${cost.bill.toFixed(2)} - the model panel covers a ` +
                          'narrower population than the band it sits in');

      // "not measured" is a real answer; a dash where a figure exists is not.
      for (const k of cost.usable) {
        const p = cost.per[k];
        if (p && (p.usd === null || p.req === null))
          costProblems.push(`${k} has usage but the hero shows no figure for it`);
      }

      // A sub-line reading exactly "not measured" under a number that IS
      // measured denies the number above it - the card read "8,767 MODEL
      // REQUESTS / not measured", where the missing quantity was a different
      // one entirely. Every harness passed; only the picture showed it.
      if (cost.contradictions && cost.contradictions.length)
        costProblems.push(`bare "not measured" under a measured figure: ` +
                          cost.contradictions.join(', '));
    }

    // --- one date format ------------------------------------------------------
    // The page carried three at once. Two of them ("1/8", "Aug 1, 2026") are
    // ambiguous or locale-bound, so a reader had to work out the convention per
    // panel before comparing two numbers.
    const dates = await page.evaluate(() => {
      const CANON = /\b\d{1,2}-(January|February|March|April|May|June|July|August|September|October|November|December)-\d{4}\b/g;
      const t = document.body.innerText;
      return {
        good: (t.match(CANON) || []).length,
        iso: (t.match(/\b\d{4}-\d{2}-\d{2}\b/) || [])[0] || null,
        us: (t.match(/\b[A-Z][a-z]{2} \d{1,2}, \d{4}\b/) || [])[0] || null,
        slash: (t.match(/\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{1,2}\/\d{1,2}\b/) || [])[0] || null,
        labels: [...document.querySelectorAll('#dayBars .bar .lab')].map(e => e.textContent.trim()),
        clipped: [...document.querySelectorAll('#dayBars .bar .lab')]
                   .filter(e => e.scrollWidth > e.clientWidth + 1).length,
      };
    });
    const dateProblems = [];
    if (dates.iso) dateProblems.push(`bare ISO date on the page: ${dates.iso}`);
    if (dates.us) dateProblems.push(`US short-month date on the page: ${dates.us}`);
    if (dates.slash) dateProblems.push(`ambiguous numeric date on the page: ${dates.slash}`);
    if (dates.labels.length && !dates.labels.every(l =>
        /^\d{1,2}-(January|February|March|April|May|June|July|August|September|October|November|December)-\d{4}$/.test(l)))
      dateProblems.push(`day labels are not canonical: ${dates.labels.slice(0, 2).join(' | ')}`);
    if (dates.clipped)
      dateProblems.push(`${dates.clipped} day labels are truncated by their column`);
    if (dates.good < 5)
      dateProblems.push(`only ${dates.good} canonical dates rendered - is the formatter wired up?`);

    // --- the plan card ------------------------------------------------------
    // The panel that must NOT be trusted just because it rendered. Three things
    // can go wrong quietly: it can show a completion rate (the metric that reads
    // 100% for everyone), it can leak todo text into a published page, and it
    // can disagree with the day table it is joined to.
    const plan = await page.evaluate(() => {
      // DATA is a top-level const in a classic script: script-scoped, NOT a
      // property of window. `window.DATA` returns undefined and this check then
      // reports "no todo list, card correctly hidden" - a PASSING message from a
      // blind test. It was caught only because the card was visible at the time
      // and the contradiction fired. Had the card been hidden it would have
      // agreed with itself forever.
      const D = (typeof DATA !== 'undefined') ? DATA : null;
      const c = document.getElementById('plancard');
      if (!D || !D.plan) return { absent: true, card: !!c, shown: c ? !c.hidden : false };
      if (!c) return { missingCard: true };
      const txt = c.textContent;
      const rows = [...c.querySelectorAll('tbody tr')];
      const written = rows.reduce((a, tr) => {
        const v = tr.children[3].textContent.replace(/[^0-9]/g, '');
        return a + (v ? +v : 0);
      }, 0);
      return {
        shown: !c.hidden,
        stats: c.querySelectorAll('.stat').length,
        rows: rows.length,
        writtenSum: written,
        srcTotal: D.plan.total,
        srcDayRows: D.days.rows.length,
        unplannedRows: rows.filter(tr => /none written/.test(tr.children[3].textContent)).length,
        srcUnplanned: D.plan.coverage ? D.plan.coverage.unplanned_days.length : 0,
        saysNoRate: /completion rate is deliberately absent/i.test(txt),
        // A percentage that is exactly 100 is the shape of the metric this
        // panel exists to refuse. Flag it wherever it appears here.
        hasHundredPct: /\b100(\.0)?%/.test(txt),
        subsetStated: /where it is measurable/i.test(txt),
      };
    });

    const planProblems = [];
    if (plan.missingCard)
      planProblems.push('payload carries plan data but the page has no plan card');
    if (plan.absent && plan.shown)
      planProblems.push('no plan data, yet the card is visible');
    if (!plan.absent && !plan.missingCard) {
      if (!plan.shown) planProblems.push('plan data present but the card is hidden');
      if (plan.rows !== plan.srcDayRows)
        planProblems.push(`plan table ${plan.rows} rows != ${plan.srcDayRows} day rows`);
      if (plan.writtenSum !== plan.srcTotal)
        planProblems.push(`todos in table ${plan.writtenSum} != ${plan.srcTotal} recorded`);
      if (plan.unplannedRows !== plan.srcUnplanned)
        planProblems.push(`${plan.unplannedRows} unplanned rows shown != ${plan.srcUnplanned}`);
      if (!plan.saysNoRate)
        planProblems.push('the card does not state that completion rate is withheld');
      if (plan.hasHundredPct)
        planProblems.push('a 100% figure appears - that is the metric this card refuses');
      if (!plan.subsetStated)
        planProblems.push('time-open is shown without saying it covers a subset');
    }

    const ok = errs.length === 0 && pageErrs.length === 0 && bad.length === 0 &&
               r.empty.length === 0 && r.nan.length === 0 && r.leaked.length === 0 &&
               r.hasWithdrawn && r.theme === theme && dayProblems.length === 0 &&
               fleetProblems.length === 0 && scopeProblems.length === 0 &&
               costProblems.length === 0 &&
               dateProblems.length === 0 &&
               planProblems.length === 0;
    if (!ok) fail++;
    console.log(`--- ${theme} --- ${ok ? 'PASS' : 'FAIL'}`);
    console.log(`  theme=${r.theme} bg=${r.bg} font=${r.font}`);
    console.log(`  cards=${r.cards} tables=${r.tables} tbody-rows=${r.rows} bars=${r.barsCount} segs=${r.segs} chars=${r.chars}`);
    console.log(`  console=${errs.length} pageerr=${pageErrs.length} http4xx=${bad.length}`);
    console.log(`  empty=[${r.empty}] nan=[${r.nan.slice(0,5)}] leaked=[${[...new Set(r.leaked)].slice(0,5)}]`);
    console.log(`  withdrawal present=${r.hasWithdrawn}  "$0.00" occurrences=${r.zeroDollar}`);
    if (!days.missing) {
      console.log(`  days=${days.srcDays} bars=${days.bars} segs=${days.segs} ` +
                  `model-invariant=${new Set(Object.values(days.cutoffs).map(v=>v.mod)).size === 1} ` +
                  `engaged-range=${Object.values(days.cutoffs).map(v=>v.eng).join('/')}`);
    }
    if (dayProblems.length) console.log('  DAYS ' + dayProblems.join(' | '));
    if (!fleet.missing)
      console.log(`  fleet=${fleet.mode} rows=${fleet.rows}` +
                  (fleet.mode === 'merged'
                    ? ` sums=${fleet.reqAdd && fleet.nanoAdd} wall<=work=${fleet.wallOk} flat=${fleet.modFlat}`
                    : ` floor-stated=${fleet.saysFloor}`));
    if (fleet.mode === 'merged' && fleet.bridged_s)
      console.log(`  pooled-vs-per-machine: +${(fleet.bridged_s/60).toFixed(1)} min bridged`);
    if (fleetProblems.length) console.log('  FLEET ' + fleetProblems.join(' | '));
    if (!scope.absent)
      console.log(`  scope: ${scope.offered[0].split(',').length} repositories, ` +
                  `all=${scope.all.req} req` +
                  (scope.main ? `, ${scope.mainKey}=${scope.main.req} req` : '') +
                  (scope.sib ? `, ${scope.sibKey}=${scope.sib.req} req` : ''));
    if (scopeProblems.length) console.log('  SCOPE ' + scopeProblems.join(' | '));
    if (!cost.absent)
      console.log(`  cost: fleet $${cost.bill.toFixed(2)}, ` +
                  `models $${(cost.byModel || 0).toFixed(2)}, ` +
                  `channels $${(cost.byChannel || 0).toFixed(2)}, ` +
                  `${cost.usable.length} repositories with usage`);
    if (costProblems.length) console.log('  COST ' + costProblems.join(' | '));
    console.log(`  dates: ${dates.good} canonical, 0 legacy formats`);
    if (dateProblems.length) console.log('  DATES ' + dateProblems.join(' | '));
    if (!plan.absent && !plan.missingCard)
      console.log(`  plan: ${plan.rows} day rows, ${plan.writtenSum} todos, ` +
                  `${plan.unplannedRows} unplanned days, rate-withheld=${plan.saysNoRate}`);
    else if (plan.absent)
      console.log('  plan: no todo list for this session (card correctly hidden)');
    if (planProblems.length) console.log('  PLAN ' + planProblems.join(' | '));
    if (errs.length) console.log('  ERR ' + errs.slice(0, 4).join(' | '));
    if (pageErrs.length) console.log('  PAGEERR ' + pageErrs.slice(0, 4).join(' | '));
    await ctx.close();
  }

  await browser.close();
  console.log(fail === 0 ? '\nALL CHECKS PASSED' : `\n${fail} THEME(S) FAILED`);
  process.exit(fail === 0 ? 0 : 1);
})();
