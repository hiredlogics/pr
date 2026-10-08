// Paste into the browser console on the site (or run via automation). Uses the
// public /api proxy only. Results land in window.__evalResults and localStorage
// (so a page reload can resume: re-run and finished ids are skipped).
// Run it from a static page (e.g. /favicon.ico) so dev-server reloads don't kill it.
window.__evalRun = async (cases, concurrency = 3) => {
  const J = { 'Content-Type': 'application/json' };
  const call = async (path, opts = {}) => {
    const r = await fetch('/api/' + path, opts);
    const t = await r.text(); let b; try { b = JSON.parse(t); } catch { b = { raw: t.slice(0, 300) }; }
    return { status: r.status, body: b };
  };
  const answerFor = (c, q) => {
    if (q.type === 'upload') return null;
    for (const [re, a] of Object.entries(c.answers)) {
      if (new RegExp(re, 'i').test(q.text)) {
        if (q.type === 'choice') {
          const opt = (q.options || []).find(o => o.toUpperCase() === String(a).toUpperCase());
          if (opt) return opt;
          continue;
        }
        if (q.type === 'bool') return /^y/i.test(a);
        if (q.type === 'int') { const n = parseInt(a, 10); return isNaN(n) ? null : n; }
        return a;
      }
    }
    return null;
  };
  const runOne = async (c) => {
    const res = { id: c.id, title: c.title, asked: [], error: null };
    try {
      const cr = await call('cases', { method: 'POST' });
      const id = cr.body.case_id; res.case_id = id;
      const fd = new FormData(); fd.append('files', new Blob([c.notice], { type: 'text/plain' }), 'notice.txt');
      const up = await call(`cases/${id}/files`, { method: 'POST', body: fd });
      res.upload_state = up.body.state;
      if (up.status >= 400) { res.state = 'UPLOAD_' + up.status; res.detail = JSON.stringify(up.body).slice(0, 300); return res; }
      if (['NO_APPEAL_RIGHT', 'CLASSIFICATION_FAILED'].includes(up.body.state)) { res.state = up.body.state; res.stop = up.body.stop_code; return res; }
      const conf = await call(`cases/${id}/confirmation`);
      const details = conf.body.details || [];
      res.read = Object.fromEntries(details.filter(d => !/keeper_name|keeper_address/.test(d.name)).map(d => [d.name, d.value]));
      let p = (await call(`cases/${id}/confirm`, { method: 'POST', headers: J, body: JSON.stringify({ corrections: {}, confirmed: details.filter(d => d.value).map(d => d.name), narrative: c.account }) })).body;
      for (let i = 0; i < 6 && p && (p.questions || []).length && !['NO_APPEAL_RIGHT'].includes(p.state); i++) {
        const answers = {}; let skip = false;
        for (const q of p.questions) {
          const a = answerFor(c, q); res.asked.push(q.text.slice(0, 80) + ' => ' + (a === null ? '(skip)' : a));
          if (a === null) skip = true; else answers[q.fact] = a;
        }
        p = (await call(`appeal/${id}`, { method: 'POST', headers: J, body: JSON.stringify({ answers, skip: skip && !Object.keys(answers).length }) })).body;
      }
      res.state = p.state; res.outcome = p.outcome || null; res.grounds = p.grounds || [];
      res.notices = (p.notices || []).map(n => n.code);
      res.letter = p.letter || '';
      res.reason = p.outcome_message || p.stop_reason || null;
    } catch (e) { res.error = String(e); }
    return res;
  };
  const score = (c, r) => {
    const e = c.expect, L = (r.letter || '').toLowerCase(), why = [];
    if (e.state && r.state !== e.state) why.push(`state ${r.state} != ${e.state}`);
    if (e.outcome === 'RELEASED' && r.state !== 'RELEASED') why.push(`not released (${r.state}/${r.outcome})`);
    if (e.outcome_in && !e.outcome_in.includes(r.state === 'RELEASED' ? 'RELEASED' : r.outcome)) why.push(`outcome ${r.state}/${r.outcome}`);
    for (const s of e.letter_has || []) if (r.letter && !L.includes(s.toLowerCase())) why.push(`missing "${s}"`);
    if ((e.letter_any || []).length && r.letter && !e.letter_any.some(s => L.includes(s.toLowerCase()))) why.push(`none of ${JSON.stringify(e.letter_any)}`);
    for (const s of e.letter_not || []) if (L.includes(s.toLowerCase())) why.push(`should not say "${s}"`);
    if (e.notice_code && !(r.notices || []).includes(e.notice_code)) why.push(`no notice ${e.notice_code}`);
    if (r.error) why.push('error ' + r.error);
    return why;
  };
  const KEY = '__evalResults';
  let saved = []; try { saved = JSON.parse(localStorage.getItem(KEY) || '[]').filter(r => !r.error); } catch {}
  window.__evalResults = saved;
  const doneIds = new Set(saved.map(r => r.id));
  const queue = cases.filter(c => !doneIds.has(c.id));
  const worker = async () => { while (queue.length) { const c = queue.shift(); const r = await runOne(c); r.fail = score(c, r); r.pass = !r.fail.length; window.__evalResults.push(r); try { localStorage.setItem(KEY, JSON.stringify(window.__evalResults)); } catch {} } };
  window.__evalDone = false;
  await Promise.all(Array.from({ length: concurrency }, worker));
  window.__evalDone = true;
  return window.__evalResults.length;
};
