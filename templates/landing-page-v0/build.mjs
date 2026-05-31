#!/usr/bin/env node
// Minimal build: read passport.json, copy static/ → dist/, substitute
// `{{key}}` placeholders in text files.
// Special cases:
//   {{faqs}}  — rendered as <details>/<summary> accordion from passport.faqs[]
//   {{year}}  — current four-digit year (not stored in passport)

import fs from 'node:fs';
import path from 'node:path';

const ROOT = process.cwd();
const SRC = path.join(ROOT, 'static');
const DST = path.join(ROOT, 'dist');
const PASSPORT = path.join(ROOT, 'passport.json');

if (!fs.existsSync(PASSPORT)) {
  console.error(`error: ${PASSPORT} not found`);
  process.exit(1);
}
const passport = JSON.parse(fs.readFileSync(PASSPORT, 'utf8'));

const TEXT_EXT = new Set(['.html', '.htm', '.xml', '.txt', '.css', '.js', '.json', '.svg']);

function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function renderFaqs(faqs) {
  if (!Array.isArray(faqs) || faqs.length === 0) return '';
  return faqs.map(f =>
    `  <details>\n    <summary>${escapeHtml(f.question)}</summary>\n    <p>${escapeHtml(f.answer)}</p>\n  </details>`
  ).join('\n');
}

function substitute(content) {
  return content.replace(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g, (full, key) => {
    if (key === 'faqs') return renderFaqs(passport.faqs);
    if (key === 'year') return String(new Date().getFullYear());
    const v = passport[key];
    if (v === undefined || v === null) return '';
    return typeof v === 'string' ? v : JSON.stringify(v);
  });
}

function walk(dir) {
  const out = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...walk(p));
    else if (e.isFile()) out.push(p);
  }
  return out;
}

fs.rmSync(DST, { recursive: true, force: true });
fs.mkdirSync(DST, { recursive: true });

if (!fs.existsSync(SRC)) {
  console.error(`error: ${SRC} not found`);
  process.exit(1);
}

let textCount = 0;
let copyCount = 0;
for (const src of walk(SRC)) {
  const rel = path.relative(SRC, src);
  const dst = path.join(DST, rel);
  fs.mkdirSync(path.dirname(dst), { recursive: true });
  const ext = path.extname(src).toLowerCase();
  if (TEXT_EXT.has(ext)) {
    fs.writeFileSync(dst, substitute(fs.readFileSync(src, 'utf8')));
    textCount++;
  } else {
    fs.copyFileSync(src, dst);
    copyCount++;
  }
}

console.log(`build: ${textCount} substituted, ${copyCount} copied → ${path.relative(ROOT, DST)}/`);
