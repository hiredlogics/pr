/**
 * P7.5 screenshot harness — writes static HTML fixtures and (when Playwright
 * is available) PNGs at 390 / 430 / 768 / 1440 for:
 *   A successful, B blocked, C validation-blocked, D paired comparison.
 *
 * Run: node frontend/trace-screenshots/generate.mjs
 */
import { mkdir, writeFile, readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const out = join(__dirname, "out");
await mkdir(out, { recursive: true });

const css = await readFile(join(__dirname, "../app/globals.css"), "utf8");

const scenarios = {
  successful: {
    title: "A — Successful appeal trace",
    body: `
      <div class="shell"><div class="stack">
        <div class="card stack"><h1>Your appeal letter</h1><p class="lede">Check the letter below…</p></div>
        <div class="trace-console" data-testid="case-trace-console">
          <header class="trace-sticky">
            <div class="trace-sticky-row"><h2>Case Intelligence Trace</h2><span class="trace-chip trace-chip-pass">RELEASED</span></div>
            <p class="trace-synthetic">TEST CASE / SYNTHETIC DATA</p>
          </header>
          <section class="trace-card stack">
            <h3>Summary</h3>
            <ul class="trace-health">
              <li><span>Extraction</span><span class="trace-chip trace-chip-pass">PASS</span></li>
              <li><span>Ground merge</span><span class="trace-chip trace-chip-pass">PASS</span></li>
              <li><span>Claim Plan</span><span class="trace-chip trace-chip-pass">PASS</span></li>
              <li><span>Validation</span><span class="trace-chip trace-chip-pass">PASS</span></li>
            </ul>
          </section>
          <ol class="trace-pipeline">
            <li><div class="trace-pipeline-row"><span class="trace-chip trace-chip-pass">PASS</span><span class="trace-pipeline-name">POFA + ANPR MERGED</span></div></li>
          </ol>
        </div>
      </div></div>`,
  },
  blocked: {
    title: "B — Could not write appeal",
    body: `
      <div class="shell"><div class="stack">
        <div class="card stack">
          <h1>We could not write an appeal we can stand behind</h1>
          <p class="lede">From the notice and the answers you gave…</p>
        </div>
        <div class="trace-console">
          <header class="trace-sticky">
            <div class="trace-sticky-row"><h2>Case Intelligence Trace</h2><span class="trace-chip trace-chip-blocked">BLOCKED</span></div>
          </header>
          <section class="trace-card trace-why stack">
            <h3>Why this case stopped</h3>
            <p>Blocking stage: <span class="trace-chip trace-chip-fail">FAIL</span> GROUND_MERGE</p>
            <div class="trace-integrity-fail"><strong>GROUND INTEGRITY FAILURE</strong>
              <p>POFA_POSTAL_LATE existed before merge but disappeared from the final set. No INVALIDATES relationship exists.</p>
            </div>
          </section>
        </div>
      </div></div>`,
  },
  validation: {
    title: "C — Validation-blocked trace",
    body: `
      <div class="shell"><div class="stack">
        <div class="card stack"><h1>Something went wrong while preparing your appeal</h1></div>
        <div class="trace-console">
          <h2>Case Intelligence Trace</h2>
          <ul class="trace-health">
            <li><span>VAL-PARTICULARS</span><span class="trace-chip trace-chip-fail">FAIL</span></li>
            <li><span>VAL-PLACEHOLDER</span><span class="trace-chip trace-chip-fail">FAIL</span></li>
            <li><span>VAL-COVERAGE</span><span class="trace-chip trace-chip-warn">WARNING</span></li>
          </ul>
        </div>
      </div></div>`,
  },
  compare: {
    title: "D — Paired run comparison",
    body: `
      <div class="shell"><div class="stack">
        <div class="trace-console">
          <h2>Case Intelligence Trace</h2>
          <div class="trace-compare">
            <div class="trace-compare-col"><h4>RUN A</h4><ul class="trace-list"><li>PoFA ✓</li><li>Multiple Visit —</li></ul></div>
            <div class="trace-compare-col"><h4>RUN B</h4><ul class="trace-list"><li>PoFA ✓</li><li>Multiple Visit ✓</li></ul></div>
            <div class="trace-compare-change"><h4>CHANGE</h4><ul class="trace-list"><li>+ Multiple Visit</li><li>No removed grounds</li></ul></div>
          </div>
        </div>
      </div></div>`,
  },
};

const widths = [390, 430, 768, 1440];

for (const [key, sc] of Object.entries(scenarios)) {
  const html = `<!doctype html><html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>${sc.title}</title><style>${css}
body{background:#fff;font-family:system-ui,sans-serif}
.shell{max-width:34rem;margin:0 auto;padding:16px}
.stack{display:grid;gap:16px}
.card{border:1px solid #ece7e9;border-radius:12px;padding:16px}
.lede{color:#5d6270}
</style></head><body>${sc.body}</body></html>`;
  await writeFile(join(out, `${key}.html`), html);
}

let playwrightOk = false;
try {
  const { chromium } = await import("playwright");
  const browser = await chromium.launch();
  for (const [key] of Object.entries(scenarios)) {
    for (const w of widths) {
      const page = await browser.newPage({ viewport: { width: w, height: 900 } });
      await page.goto(`file://${join(out, `${key}.html`).replace(/\\/g, "/")}`);
      await page.screenshot({ path: join(out, `${key}-${w}.png`), fullPage: true });
      await page.close();
    }
  }
  await browser.close();
  playwrightOk = true;
} catch (e) {
  await writeFile(
    join(out, "SCREENSHOTS.md"),
    `# Screenshots\n\nHTML fixtures written for A–D.\nPlaywright not available (${e.message}).\n\nOpen each HTML at widths 390 / 430 / 768 / 1440, or:\n\nnpx playwright install chromium\nnode frontend/trace-screenshots/generate.mjs\n`,
  );
}

console.log(JSON.stringify({ out, html: Object.keys(scenarios), playwrightOk, widths }, null, 2));
