import puppeteer from "puppeteer";
import path from "path";

const OUTPUT_PATH = path.resolve("event-report.png");

(async () => {
  const browser = await puppeteer.launch({ headless: true });
  const page = await browser.newPage();

  // Set viewport to a nice wide size for the dashboard
  await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 2 });

  await page.goto("http://localhost:5173", { waitUntil: "networkidle0" });

  // Wait a moment for any fonts/images to finish loading
  await new Promise((r) => setTimeout(r, 1500));

  // Screenshot the full page
  await page.screenshot({ path: OUTPUT_PATH, fullPage: true });

  console.log(`Screenshot saved to: ${OUTPUT_PATH}`);
  await browser.close();
})();
