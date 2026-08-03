const fs = require('fs');
const path = require('path');

const files = process.argv.slice(2);

if (!files.length) {
  console.error('Usage: node scripts/a11y-label-ids.js <html-file> [...]');
  process.exit(1);
}

const slug = (value) =>
  value
    .toLowerCase()
    .replace(/&amp;/g, ' and ')
    .replace(/<[^>]+>/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '') || 'field';

const hasAttr = (tag, attr) => new RegExp(`\\s${attr}(?:=|\\s|>)`).test(tag);

const splitTag = (tag) => {
  const close = tag.endsWith('/>') ? ' />' : '>';
  return { body: tag.slice(0, -close.length), close };
};

const addAttr = (tag, name, value) => {
  if (hasAttr(tag, name)) return tag;
  const { body, close } = splitTag(tag);
  return `${body} ${name}="${value}"${close}`;
};

const xForTemplateRe = /<template\b[^>]*\bx-for\s*=\s*(['"])[\s\S]*?\1[^>]*>[\s\S]*?<\/template>/g;
const pairRe = /<label\b([^>]*\bclass\s*=\s*(['"])[^'"]*\bfield-label\b[^'"]*\2[^>]*)>([^<]*)<\/label>(\s*)(<(?:input|select|textarea)\b[^>]*>)/g;

for (const file of files) {
  const source = fs.readFileSync(file, 'utf8');
  const skipped = [];
  const masked = source.replace(xForTemplateRe, (match) => {
    skipped.push(match.slice(0, 120).replace(/\s+/g, ' ').trim());
    return '\u0000'.repeat(match.length);
  });
  let output = source;
  let changed = 0;
  const used = new Set([...source.matchAll(/\sid\s*=\s*"([^"]+)"/g)].map((m) => m[1]));
  const fileSlug = slug(path.basename(file, '.html').replace(/^tab-/, ''));

  output = output.replace(pairRe, (match, labelAttrs, quote, text, between, controlTag, offset) => {
    if (masked.charCodeAt(offset) === 0 || hasAttr(`<label ${labelAttrs}>`, 'for') || hasAttr(controlTag, 'id')) {
      return match;
    }

    const base = `a11y-${fileSlug}-${slug(text)}`;
    let id = base;
    let n = 2;
    while (used.has(id)) id = `${base}-${n++}`;
    used.add(id);
    changed += 1;
    return `<label ${labelAttrs} for="${id}">${text}</label>${between}${addAttr(controlTag, 'id', id)}`;
  });

  if (output !== source) fs.writeFileSync(file, output, 'utf8');
  console.log(`${file}: ${changed} label pair(s) updated; ${skipped.length} x-for template(s) skipped`);
  for (const item of skipped) console.log(`  skipped: ${item}`);
}
