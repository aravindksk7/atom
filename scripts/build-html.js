#!/usr/bin/env node
'use strict';
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const TEMPLATE = path.join(ROOT, 'frontend', 'index.template.html');
const OUTPUT = path.join(ROOT, 'frontend', 'index.html');
// Allow an optional trailing \r so the marker still matches when the template
// uses CRLF line endings (this repo checks out CRLF via core.autocrlf=true).
const INCLUDE_RE = /^<!-- INCLUDE: (.+?) -->\r?$/;

function build() {
  const template = fs.readFileSync(TEMPLATE, 'utf8');
  // Detect and preserve the template's line-ending convention so the rebuilt
  // file matches the committed frontend/index.html byte-for-byte.
  const eol = template.includes('\r\n') ? '\r\n' : '\n';
  const lines = template.split(/\r\n|\n/);
  let includeCount = 0;
  const out = lines.map((line) => {
    const match = line.match(INCLUDE_RE);
    if (!match) return line;
    includeCount += 1;
    const partialPath = path.join(ROOT, 'frontend', match[1]);
    return fs.readFileSync(partialPath, 'utf8').replace(/\r?\n$/, '');
  });
  fs.writeFileSync(OUTPUT, out.join(eol));
  console.log(`Built ${OUTPUT} from ${TEMPLATE} + ${includeCount} partials`);
}

build();
