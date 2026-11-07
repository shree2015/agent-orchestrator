// Print an HTML file to an A4 PDF with page numbers, using puppeteer-core and a local Chromium.
// Usage: node scripts/html_to_pdf.js <in.html> <out.pdf> "<footer text>" [chromium-executable]
const path = require('path');
const root = process.env.NODE_PATH_ROOT || '/opt/npm-tools/node_modules';
const puppeteer = require(path.join(root, 'puppeteer-core'));

(async () => {
  const [input, output, footer, exe] = process.argv.slice(2);
  const browser = await puppeteer.launch({
    executablePath: exe || process.env.CHROMIUM || '/opt/pw-browsers/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu'],
  });
  const page = await browser.newPage();
  await page.goto('file://' + path.resolve(input), { waitUntil: 'networkidle0' });
  await page.pdf({
    path: output, format: 'A4', printBackground: true, displayHeaderFooter: true,
    margin: { top: '15mm', bottom: '17mm', left: '15mm', right: '15mm' },
    headerTemplate: '<div></div>',
    footerTemplate: `<div style="width:100%;font-size:7.5px;color:#666;padding:0 15mm;display:flex;justify-content:space-between;font-family:Carlito,sans-serif">
      <span>${footer}</span><span>page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>`,
  });
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
