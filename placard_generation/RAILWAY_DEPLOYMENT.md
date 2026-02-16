# Railway Deployment Guide

This guide explains how to deploy the event report infographic generator to Railway and download the PDF without hosting a live website.

## Overview

The deployment process:
1. Builds your React infographic
2. Generates a PDF using Puppeteer
3. Serves a simple download page

## Prerequisites

- A [Railway](https://railway.app) account (free tier works)
- Railway CLI installed (optional, can also use GitHub integration)

## Deployment Methods

### Method 1: Deploy via GitHub (Recommended)

1. **Push your code to GitHub**
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin <your-repo-url>
   git push -u origin main
   ```

2. **Create a new project on Railway**
   - Go to [railway.app](https://railway.app)
   - Click "New Project"
   - Select "Deploy from GitHub repo"
   - Choose your repository

3. **Railway will automatically**
   - Detect the `nixpacks.toml` configuration
   - Install Chromium for Puppeteer
   - Run `npm run generate:pdf` during build
   - Start the server with `npm run serve`

4. **Access your PDF**
   - Once deployed, Railway will give you a URL (e.g., `your-app.up.railway.app`)
   - Visit that URL and click "Download PDF"

### Method 2: Deploy via Railway CLI

1. **Install Railway CLI**
   ```bash
   npm install -g @railway/cli
   ```

2. **Login to Railway**
   ```bash
   railway login
   ```

3. **Initialize and deploy**
   ```bash
   railway init
   railway up
   ```

4. **Get your deployment URL**
   ```bash
   railway domain
   ```

## Local Testing

Before deploying, test the PDF generation locally:

1. **Install dependencies**
   ```bash
   npm install
   ```

2. **Generate PDF locally**
   ```bash
   npm run generate:pdf
   ```
   This will create `event-report.pdf` in your project directory.

3. **Test the server**
   ```bash
   npm run serve
   ```
   Then visit `http://localhost:3000` and try downloading the PDF.

## Configuration Files

- **nixpacks.toml** - Railway build configuration, installs Chromium
- **generate-pdf.mjs** - Script that builds the app and generates PDF
- **server.mjs** - Express server that serves the download page
- **.puppeteerrc.cjs** - Puppeteer configuration for Railway

## Troubleshooting

### PDF generation fails on Railway

Check the build logs for Chromium-related errors. The `nixpacks.toml` should automatically install Chromium, but if it fails:
- Ensure `nixpacks.toml` is in the root directory
- Check that Puppeteer version is compatible (currently `^24.37.3`)

### Server starts but PDF not found

The PDF is generated during the build phase. Check:
- Build logs show "✅ PDF saved to: ..." message
- The `generate:pdf` script completed successfully

### Chromium not found error

The `generate-pdf.mjs` script tries to find Chromium automatically. If it fails, you can set the environment variable on Railway:
```
PUPPETEER_EXECUTABLE_PATH=/nix/store/.../chromium
```

## Cost Considerations

- Railway's free tier should be sufficient for this use case
- The server only needs to run to serve downloads (no ongoing processing)
- You can stop the service when not needed and redeploy when you want a fresh PDF

## Updating the PDF

To regenerate the PDF with updated content:

1. Make changes to `App.tsx` or other components
2. Commit and push to GitHub
3. Railway will automatically rebuild and regenerate the PDF

## Alternative: One-time Generation

If you only want to generate the PDF once without keeping a server running:

1. Deploy to Railway as described above
2. Download the PDF from the deployment URL
3. Delete the Railway project

This gives you the PDF without any ongoing hosting costs.
