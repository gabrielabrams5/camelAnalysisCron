const { join } = require('path');

/**
 * @type {import("puppeteer").Configuration}
 */
module.exports = {
  // Only skip download on Railway (when RAILWAY_ENVIRONMENT is set)
  // Locally, let Puppeteer download Chrome automatically
  skipDownload: !!process.env.RAILWAY_ENVIRONMENT,
};
