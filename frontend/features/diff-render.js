(function (global) {
  'use strict';
  // Character-level diff rendering for mismatch value cells (Compare tab).
  //
  // Colour contract -- keep in step with the same rules in
  // etl_framework/reporting/templates/report.html.j2, which renders the
  // downloadable HTML report from the same mismatch rows:
  //
  //   * Red marks the side that is WRONG -- the side missing the row/column, or
  //     the characters dropped from a drifted value. It never means "the source
  //     side" and green never means "the target side".
  //   * Presence rows (missing_in_target and friends) store the literal
  //     sentinels "present"/"missing" as their values. Those are labels, not
  //     data: char-diffing them painted the word "present" red and "missing"
  //     green, i.e. exactly backwards. Render an explicit marker instead.

  function escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // Mismatch types whose values are presence sentinels rather than real data.
  const PRESENCE_TYPES = {
    missing_in_target: 1,
    missing_in_source: 1,
    missing_columns: 1,
    extra_columns: 1,
  };

  function isPresenceType(type) {
    return !!PRESENCE_TYPES[type];
  }

  function isAbsentValue(value) {
    const s = String(value == null ? '' : value).toLowerCase();
    return s === 'missing' || s === 'absent';
  }

  function renderPresence(value) {
    return isAbsentValue(value)
      ? '<span class="presence-absent">&#8709; absent</span>'
      : '<span class="presence-present">&#9679; present</span>';
  }

  function charDiff(a, b) {
    const n = a.length, m = b.length;
    if (n === 0) return b.split('').map(c => ({ text: c, op: '+' }));
    if (m === 0) return a.split('').map(c => ({ text: c, op: '-' }));
    const dp = [];
    for (let i = 0; i <= n; i++) { dp[i] = new Uint16Array(m + 1); }
    for (let i = 1; i <= n; i++) {
      for (let j = 1; j <= m; j++) {
        dp[i][j] = a[i - 1] === b[j - 1]
          ? dp[i - 1][j - 1] + 1
          : Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
    const ops = [];
    let i = n, j = m;
    while (i > 0 || j > 0) {
      if (i > 0 && j > 0 && a[i - 1] === b[j - 1]) {
        ops.push({ text: a[i - 1], op: '=' }); i--; j--;
      } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
        ops.push({ text: b[j - 1], op: '+' }); j--;
      } else {
        ops.push({ text: a[i - 1], op: '-' }); i--;
      }
    }
    ops.reverse();
    const merged = [];
    for (const { text, op } of ops) {
      if (merged.length && merged[merged.length - 1].op === op) merged[merged.length - 1].text += text;
      else merged.push({ text, op });
    }
    return merged;
  }

  function renderSrc(rawA, rawB, mismatchType) {
    if (isPresenceType(mismatchType)) return renderPresence(rawA);
    if (rawA == null) return '<span class="null-val">NULL</span>';
    if (rawB == null) return escHtml(String(rawA));
    if (!isNaN(rawA) && !isNaN(rawB) && isFinite(rawA) && isFinite(rawB)) {
      return escHtml(String(rawA));
    }
    const sa = String(rawA), sb = String(rawB);
    if (sa.length > 500 || sb.length > 500) return escHtml(sa.slice(0, 500)) + '…';
    return charDiff(sa, sb).map(({ text, op }) =>
      op === '-' ? `<span class="diff-del">${escHtml(text)}</span>` :
      op === '=' ? escHtml(text) : ''
    ).join('');
  }

  function renderTgt(rawA, rawB, mismatchType) {
    if (isPresenceType(mismatchType)) return renderPresence(rawB);
    if (rawB == null) return '<span class="null-val">NULL</span>';
    if (rawA == null) return escHtml(String(rawB));
    if (!isNaN(rawA) && !isNaN(rawB) && isFinite(rawA) && isFinite(rawB)) {
      return escHtml(String(rawB));
    }
    const sa = String(rawA), sb = String(rawB);
    if (sa.length > 500 || sb.length > 500) return escHtml(sb.slice(0, 500)) + '…';
    return charDiff(sa, sb).map(({ text, op }) =>
      op === '+' ? `<span class="diff-ins">${escHtml(text)}</span>` :
      op === '=' ? escHtml(text) : ''
    ).join('');
  }

  // Alpine resolves x-html expressions against the component scope first and the
  // global scope after, so these stay plain globals rather than component methods.
  global.isPresenceType = isPresenceType;
  global.isAbsentValue = isAbsentValue;
  global.renderPresence = renderPresence;
  global.renderSrc = renderSrc;
  global.renderTgt = renderTgt;
})(window);
