import express from "express";
import path from "path";
import { fileURLToPath } from "url";
import fs from "fs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
const PORT = process.env.PORT || 3000;
const PDF_PATH = path.join(__dirname, "event-report.pdf");

app.get("/", (req, res) => {
  res.send(`
    <!DOCTYPE html>
    <html>
      <head>
        <title>Event Report PDF</title>
        <style>
          body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 600px;
            margin: 50px auto;
            padding: 20px;
            text-align: center;
          }
          .download-btn {
            display: inline-block;
            padding: 12px 24px;
            background: #0066cc;
            color: white;
            text-decoration: none;
            border-radius: 6px;
            font-weight: 600;
            margin-top: 20px;
          }
          .download-btn:hover {
            background: #0052a3;
          }
          .status {
            margin: 20px 0;
            padding: 12px;
            background: #f0f0f0;
            border-radius: 6px;
          }
        </style>
      </head>
      <body>
        <h1>Event Report Infographic</h1>
        <div class="status">
          ${fs.existsSync(PDF_PATH)
            ? '✅ PDF is ready for download'
            : '⚠️ PDF not found. Please check if generation completed successfully.'}
        </div>
        <a href="/download" class="download-btn">Download PDF</a>
      </body>
    </html>
  `);
});

app.get("/download", (req, res) => {
  if (fs.existsSync(PDF_PATH)) {
    res.download(PDF_PATH, "event-report.pdf", (err) => {
      if (err) {
        console.error("Error downloading PDF:", err);
        res.status(500).send("Error downloading PDF");
      }
    });
  } else {
    res.status(404).send("PDF not found. Please check if generation completed successfully.");
  }
});

app.get("/health", (req, res) => {
  res.json({
    status: "ok",
    pdfExists: fs.existsSync(PDF_PATH)
  });
});

app.listen(PORT, () => {
  console.log(`🚀 Server running on port ${PORT}`);
  console.log(`📄 PDF status: ${fs.existsSync(PDF_PATH) ? 'Found' : 'Not found'}`);
});
