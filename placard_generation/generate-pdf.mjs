import puppeteer from "puppeteer";
import path from "path";
import { exec } from "child_process";
import { promisify } from "util";
import http from "http";
import fs from "fs";
import { fileURLToPath } from "url";
import { execSync } from "child_process";

const execAsync = promisify(exec);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUTPUT_PATH = path.resolve("event-report.pdf");
const DIST_DIR = path.resolve("dist");
const PORT = 3000;

// Find Chromium executable on Railway/Nix systems
function findChromiumPath() {
  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    return process.env.PUPPETEER_EXECUTABLE_PATH;
  }

  // Try to find chromium in /nix/store (Railway uses Nix)
  try {
    const result = execSync('which chromium || find /nix/store -name chromium -type f 2>/dev/null | head -1', {
      encoding: 'utf8',
      stdio: ['pipe', 'pipe', 'ignore']
    }).trim();
    if (result) {
      return result;
    }
  } catch (e) {
    // Fall back to default
  }

  return undefined; // Let Puppeteer use its default
}

// Simple static file server
function createServer() {
  return http.createServer((req, res) => {
    const filePath = path.join(
      DIST_DIR,
      req.url === "/" ? "index.html" : req.url
    );

    fs.readFile(filePath, (err, data) => {
      if (err) {
        res.writeHead(404);
        res.end("Not found");
        return;
      }

      const ext = path.extname(filePath);
      const contentTypes = {
        ".html": "text/html",
        ".js": "application/javascript",
        ".css": "text/css",
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".svg": "image/svg+xml",
      };

      res.writeHead(200, { "Content-Type": contentTypes[ext] || "text/plain" });
      res.end(data);
    });
  });
}

// Hard ceiling on total runtime: this script is run unattended by the cron
// pipeline, and a wedged Chromium/build must never hang the pipeline forever.
const WATCHDOG_MS = 10 * 60 * 1000;
setTimeout(() => {
  console.error(`⏰ Watchdog: PDF generation exceeded ${WATCHDOG_MS / 60000} minutes, exiting`);
  process.exit(1);
}, WATCHDOG_MS).unref();

(async () => {
  console.log("🔨 Building the React app...");
  await execAsync("npm run build", { timeout: 5 * 60 * 1000, maxBuffer: 16 * 1024 * 1024 });

  console.log("🚀 Starting local server...");
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.on("error", reject); // e.g. EADDRINUSE from an overlapping run
    server.listen(PORT, resolve);
  });

  console.log(`📄 Generating PDF from http://localhost:${PORT}...`);

  const chromiumPath = findChromiumPath();
  if (chromiumPath) {
    console.log(`🔍 Using Chromium at: ${chromiumPath}`);
  }

  const browser = await puppeteer.launch({
    headless: true,
    executablePath: chromiumPath,
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-dev-shm-usage",
      "--disable-gpu",
    ],
  });

  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 2 });
  await page.goto(`http://localhost:${PORT}`, { waitUntil: "networkidle0" });

  // Wait for fonts and images to load
  await new Promise((r) => setTimeout(r, 1500));

  // Get the actual content dimensions to create a single-page PDF like the PNG version
  const contentDimensions = await page.evaluate(() => {
    return {
      width: document.documentElement.scrollWidth,
      height: document.documentElement.scrollHeight,
    };
  });

  console.log(`📐 Content dimensions: ${contentDimensions.width}x${contentDimensions.height}px`);

  // Convert pixels to inches (96 DPI standard) for PDF
  const widthInInches = contentDimensions.width / 96;
  const heightInInches = contentDimensions.height / 96;

  // Generate PDF with custom page size to fit all content on one page
  await page.pdf({
    path: OUTPUT_PATH,
    width: `${widthInInches}in`,
    height: `${heightInInches}in`,
    printBackground: true,
    margin: {
      top: 0,
      right: 0,
      bottom: 0,
      left: 0,
    },
  });

  console.log(`✅ PDF saved to: ${OUTPUT_PATH}`);

  await browser.close();
  server.close();

  console.log("🎉 Done!");
})();
