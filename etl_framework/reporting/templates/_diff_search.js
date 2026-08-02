(function (global) {
  'use strict';
  // ── Shared mismatch search engine ────────────────────────────────────────
  //
  // CANONICAL SOURCE. Two surfaces consume this file and must behave
  // identically, so it lives in exactly one place:
  //
  //   * the downloadable HTML report -- ReportGenerator reads this file and
  //     inlines it into report.html.j2 (the report must stay self-contained,
  //     so it cannot link out to a script);
  //   * the live Compare tab -- scripts/build-html.js copies this file to
  //     frontend/features/diff-search.js, which index.html loads.
  //
  // Edit here only. tests/unit/test_diff_search_sync.py fails the build if the
  // generated frontend copy drifts.
  //
  // Query syntax (all matching is case-insensitive substring):
  //
  //   amount            match any field
  //   col:amount        scope to one field
  //   "two words"       phrase
  //   -type:missing     exclude
  //
  // Multiple terms AND together. Fields: test/query, col/column, type,
  // key/keys, src/source, tgt/target, val/value (either side). An unrecognised
  // prefix is NOT treated as a field -- "http://host" searches for the literal
  // text, so values containing a colon still work.

  var FIELD_ALIASES = {
    test: 'test', query: 'test',
    col: 'col', column: 'col',
    type: 'type',
    key: 'key', keys: 'key',
    pair: 'pair',
    src: 'src', source: 'src',
    tgt: 'tgt', target: 'tgt',
    val: 'val', value: 'val'
  };

  // Multi-file compares tag every row with the file pair it came from, nested
  // under this reserved key inside key_values. It is pairing metadata, not part
  // of the row's identity, so it is split out for display and given its own
  // search field instead of sitting inside the key JSON.
  var PAIR_KEY = '__pair__';

  // A token is an optional '-', an optional field prefix, then either a quoted
  // phrase or a run of non-space characters.
  var TOKEN_RE = /-?(?:[a-zA-Z]+:)?(?:"[^"]*"|'[^']*'|[^\s]+)/g;

  function stripQuotes(text) {
    var first = text.charAt(0);
    var last = text.charAt(text.length - 1);
    if (text.length >= 2 && ((first === '"' && last === '"') || (first === "'" && last === "'"))) {
      return text.slice(1, -1);
    }
    return text;
  }

  function parseDiffQuery(input) {
    var tokens = String(input == null ? '' : input).match(TOKEN_RE) || [];
    var terms = [];
    for (var i = 0; i < tokens.length; i++) {
      var token = tokens[i];
      var negate = false;
      if (token.charAt(0) === '-' && token.length > 1) {
        negate = true;
        token = token.slice(1);
      }
      var field = 'any';
      var scoped = /^([a-zA-Z]+):([\s\S]*)$/.exec(token);
      if (scoped && Object.prototype.hasOwnProperty.call(FIELD_ALIASES, scoped[1].toLowerCase())) {
        field = FIELD_ALIASES[scoped[1].toLowerCase()];
        token = scoped[2];
      }
      var text = stripQuotes(token).toLowerCase();
      if (!text) continue;
      terms.push({ field: field, text: text, negate: negate });
    }
    return terms;
  }

  function normalizeKeyText(keyValues) {
    if (keyValues == null) return '';
    if (typeof keyValues === 'string') return keyValues;
    try {
      return JSON.stringify(keyValues);
    } catch (e) {
      return String(keyValues);
    }
  }

  // key_values arrives as an object from the API and as a JSON string from the
  // report's row dataset; accept both.
  function parseKeyValues(keyValues) {
    if (typeof keyValues === 'string') {
      try {
        return JSON.parse(keyValues);
      } catch (e) {
        return null;
      }
    }
    return keyValues && typeof keyValues === 'object' ? keyValues : null;
  }

  function splitPairKey(keyValues) {
    var parsed = parseKeyValues(keyValues);
    if (!parsed || parsed[PAIR_KEY] == null) return { pair: null, rest: keyValues };
    var rest = {};
    for (var name in parsed) {
      if (name !== PAIR_KEY && Object.prototype.hasOwnProperty.call(parsed, name)) {
        rest[name] = parsed[name];
      }
    }
    return { pair: parsed[PAIR_KEY], rest: rest };
  }

  // "region=west" -- the pair's own key, rendered the way the report's file-pair
  // rollup renders it, so the two read alike.
  function diffPairLabel(keyValues) {
    var pair = splitPairKey(keyValues).pair;
    if (pair == null) return '';
    if (typeof pair !== 'object') return String(pair);
    var parts = [];
    for (var name in pair) {
      if (Object.prototype.hasOwnProperty.call(pair, name)) parts.push(name + '=' + pair[name]);
    }
    return parts.join(', ');
  }

  // The row's own key with the pairing metadata removed.
  function diffKeyWithoutPair(keyValues) {
    return normalizeKeyText(splitPairKey(keyValues).rest);
  }

  // Accepts either an API mismatch row or the flattened shape the report's row
  // dataset carries. Returns every searchable field pre-lowercased.
  function diffSearchFields(row) {
    row = row || {};
    var rawKey = row.key_values !== undefined ? row.key_values : row.key;
    var fields = {
      test: String(row.test_name || row.test || ''),
      col: String(row.column_name || row.column || ''),
      type: String(row.mismatch_type || row.type || ''),
      // key: and pair: are disjoint so each scopes to what the row actually
      // displays; a bare term still searches both via `any`.
      key: diffKeyWithoutPair(rawKey),
      pair: diffPairLabel(rawKey),
      src: row.source_value == null ? '' : String(row.source_value),
      tgt: row.target_value == null ? '' : String(row.target_value)
    };
    fields.val = fields.src + ' ' + fields.tgt;
    fields.any = [
      fields.test, fields.col, fields.type, fields.key, fields.pair, fields.src, fields.tgt,
    ].join(' ');
    var lowered = {};
    for (var name in fields) {
      if (Object.prototype.hasOwnProperty.call(fields, name)) lowered[name] = fields[name].toLowerCase();
    }
    return lowered;
  }

  function matchesDiffQuery(fields, terms) {
    for (var i = 0; i < terms.length; i++) {
      var term = terms[i];
      var haystack = fields[term.field] !== undefined ? fields[term.field] : fields.any;
      var hit = haystack.indexOf(term.text) !== -1;
      if (term.negate ? hit : !hit) return false;
    }
    return true;
  }

  // Text of the positive terms only -- excluded terms have nothing to highlight.
  function diffQueryNeedles(terms) {
    var needles = [];
    for (var i = 0; i < terms.length; i++) {
      if (!terms[i].negate) needles.push(terms[i].text);
    }
    return needles;
  }

  // ── Match highlighting ───────────────────────────────────────────────────
  // Walks text nodes rather than rewriting innerHTML, so highlighting composes
  // with the char-level diff markup already inside the value panels instead of
  // clobbering it.

  function clearDiffHighlights(root) {
    if (!root) return;
    var marks = root.querySelectorAll('mark.q-hit');
    for (var i = 0; i < marks.length; i++) {
      var mark = marks[i];
      mark.parentNode.replaceChild(document.createTextNode(mark.textContent), mark);
    }
    if (root.normalize) root.normalize();
  }

  function highlightTextNode(node, needles) {
    var text = node.nodeValue;
    if (!text) return;
    var lower = text.toLowerCase();
    var hits = [];
    for (var i = 0; i < needles.length; i++) {
      var needle = needles[i];
      var from = 0;
      var at = lower.indexOf(needle, from);
      while (at !== -1) {
        hits.push([at, at + needle.length]);
        from = at + needle.length;
        at = lower.indexOf(needle, from);
      }
    }
    if (!hits.length) return;
    hits.sort(function (a, b) { return a[0] - b[0]; });
    // Overlapping or duplicate needles collapse into one <mark> so the text is
    // never emitted twice.
    var merged = [hits[0]];
    for (var j = 1; j < hits.length; j++) {
      var last = merged[merged.length - 1];
      if (hits[j][0] <= last[1]) last[1] = Math.max(last[1], hits[j][1]);
      else merged.push(hits[j]);
    }
    var frag = document.createDocumentFragment();
    var cursor = 0;
    for (var k = 0; k < merged.length; k++) {
      if (merged[k][0] > cursor) frag.appendChild(document.createTextNode(text.slice(cursor, merged[k][0])));
      var mark = document.createElement('mark');
      mark.className = 'q-hit';
      mark.textContent = text.slice(merged[k][0], merged[k][1]);
      frag.appendChild(mark);
      cursor = merged[k][1];
    }
    if (cursor < text.length) frag.appendChild(document.createTextNode(text.slice(cursor)));
    node.parentNode.replaceChild(frag, node);
  }

  function highlightDiffMatches(root, needles) {
    if (!root || !needles || !needles.length) return;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var nodes = [];
    var node = walker.nextNode();
    while (node) {
      nodes.push(node);
      node = walker.nextNode();
    }
    for (var i = 0; i < nodes.length; i++) highlightTextNode(nodes[i], needles);
  }

  global.parseDiffQuery = parseDiffQuery;
  global.diffSearchFields = diffSearchFields;
  global.diffPairLabel = diffPairLabel;
  global.diffKeyWithoutPair = diffKeyWithoutPair;
  global.matchesDiffQuery = matchesDiffQuery;
  global.diffQueryNeedles = diffQueryNeedles;
  global.clearDiffHighlights = clearDiffHighlights;
  global.highlightDiffMatches = highlightDiffMatches;
})(typeof window !== 'undefined' ? window : this);
