/** @type {import('tailwindcss').Config} */

// The app is dark-only. Rather than hand-editing 672 hardcoded light color
// classes across 14 partials, the palette itself is retargeted onto the
// CSS-variable triplets defined in frontend/styles.css :root.
//
// The slate ramp is INVERTED, which is safe because each step is used for
// exactly one role and there are no collisions between surface-use and
// text-use:
//     50  bg only            400/500  text only  -> muted
//     100 border + bg        600/700  text only  -> body text
//     200 border only        800/900  text only  -> strongest text
// Three call sites fall outside that pattern and are fixed by hand in the
// markup instead of distorting the mapping (see Task 5).
//
// Hue families follow the same shape: -50/-100 tint, -200/-300 edge,
// -400 and up the readable light variant.
const surface = (v) => `rgb(var(${v}) / <alpha-value>)`;

const hue = (name) => ({
  50:  surface(`--c-${name}-tint`),
  100: surface(`--c-${name}-tint`),
  200: surface(`--c-${name}-edge`),
  300: surface(`--c-${name}-edge`),
  400: surface(`--c-${name}`),
  500: surface(`--c-${name}`),
  600: surface(`--c-${name}`),
  700: surface(`--c-${name}`),
  800: surface(`--c-${name}`),
  900: surface(`--c-${name}`),
  950: surface(`--c-${name}`),
});

module.exports = {
  content: [
    './frontend/**/*.html',
    './frontend/**/*.js',
  ],
  theme: {
    extend: {
      colors: {
        white: surface('--c-raised'),
        black: 'rgb(0 0 0 / <alpha-value>)',
        slate: {
          50:  surface('--c-panel-2'),
          100: surface('--c-border-1'),
          200: surface('--c-border-2'),
          300: surface('--c-border-2'),
          400: surface('--c-muted'),
          500: surface('--c-muted'),
          600: surface('--c-text-soft'),
          700: surface('--c-text-soft'),
          800: surface('--c-text'),
          900: surface('--c-text'),
          950: surface('--c-text'),
        },
        rose:    hue('rose'),
        emerald: hue('emerald'),
        amber:   hue('amber'),
        red:     hue('red'),
        indigo:  hue('indigo'),
        orange:  hue('orange'),
        sky:     hue('sky'),
      },
    },
  },
  plugins: [],
};
