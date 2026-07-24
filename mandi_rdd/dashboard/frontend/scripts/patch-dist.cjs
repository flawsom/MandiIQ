#!/usr/bin/env node
/**
 * Post-build script — patches absolute asset paths in the Vite-built index.html
 * to relative paths so Streamlit's declare_component can serve them correctly.
 *
 * Vite 6 ignores `base: "./"` for module scripts and emits
 *   <script type="module" crossorigin src="/assets/foo.js">
 * which breaks when Streamlit serves the component from a subdirectory.
 *
 * This script replaces absolute paths (`/assets/...`) with relative paths
 * (`./assets/...`) for both src="" and href="" attributes.
 *
 * Usage (called from package.json build script):
 *   tsc && vite build && node scripts/patch-dist.cjs
 */

const fs = require("fs");
const path = require("path");

const distHtml = path.resolve(__dirname, "..", "dist", "index.html");

if (!fs.existsSync(distHtml)) {
  console.error("  [patch-dist] dist/index.html not found — skipping");
  process.exit(0);
}

let html = fs.readFileSync(distHtml, "utf8");

// Replace absolute paths in both src="" and href="" attributes
const before = html;
html = html.replace(/["']\/(assets\/)/g, (match) => {
  // match is either '"/assets/' or "'/assets/"
  return match[0] + "./" + match.slice(2);
});

if (html === before) {
  console.log("  [patch-dist] No absolute paths found — nothing to patch");
} else {
  fs.writeFileSync(distHtml, html, "utf8");
  console.log("  [patch-dist] Patched absolute paths → relative paths in dist/index.html");
}
