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
// Looser sniff pattern: anything that looks like it's trying to be an INCLUDE
// marker but doesn't match the strict form above (e.g. stray trailing space,
// missing closing "-->") must fail the build loudly rather than pass through
// as literal HTML content.
const INCLUDE_SNIFF_RE = /^<!-- INCLUDE:/;

function build() {
  const template = fs.readFileSync(TEMPLATE, 'utf8');
  // Detect and preserve the template's line-ending convention so the rebuilt
  // file matches the committed frontend/index.html byte-for-byte.
  const eol = template.includes('\r\n') ? '\r\n' : '\n';
  const lines = template.split(/\r\n|\n/);
  let includeCount = 0;
  const out = lines.map((line) => {
    const match = line.match(INCLUDE_RE);
    if (!match) {
      if (INCLUDE_SNIFF_RE.test(line)) {
        throw new Error(
          `Malformed INCLUDE marker (looks like a marker but doesn't match ` +
          `the required "<!-- INCLUDE: partials/<file>.html -->" form): ${JSON.stringify(line)}`
        );
      }
      return line;
    }
    includeCount += 1;
    const partialPath = path.join(ROOT, 'frontend', match[1]);
    return fs.readFileSync(partialPath, 'utf8').replace(/\r?\n$/, '');
  });

  const partialsDir = path.join(ROOT, 'frontend', 'partials');
  const expectedCount = fs.readdirSync(partialsDir).filter((f) => f.endsWith('.html')).length;
  if (includeCount !== expectedCount) {
    throw new Error(
      `INCLUDE marker count (${includeCount}) does not match the number of ` +
      `.html partials on disk (${expectedCount}) in ${partialsDir}. ` +
      `A marker was likely dropped, duplicated, or malformed.`
    );
  }

  fs.writeFileSync(OUTPUT, out.join(eol));
  console.log(`Built ${OUTPUT} from ${TEMPLATE} + ${includeCount} partials`);
}

build();
