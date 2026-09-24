# Third-party code shipped with this package

The panel's front end is served from the package itself, and five of its files
are third-party bundles rather than our code. They are vendored — kept in the
tree and served from it — rather than fetched from a CDN, because the panel of
an administrative tool should not depend on somebody else's server being up,
and because a page that loads nothing from outside is one fewer place a
deployment has to reason about.

All five are minified, and minification stripped their licence headers. That
is what this file is for: every one of them is MIT, and MIT asks that its
notice travel with the copy.

| File | Project | Licence |
|---|---|---|
| `static/js/tailwind.js` | Tailwind CSS (browser build) — https://tailwindcss.com | MIT, Copyright (c) Tailwind Labs, Inc. |
| `static/js/feather-icons.js` | Feather — https://feathericons.com | MIT, Copyright (c) 2013-2023 Cole Bemis |
| `static/js/aos.js` | AOS — https://michalsnik.github.io/aos/ | MIT, Copyright (c) 2015 Michał Sajnóg |
| `static/modules/js/chart.js` | Chart.js — https://www.chartjs.org | MIT, Copyright (c) Chart.js Contributors |
| `static/modules/js/chartjs-adapter-date-fns.bundle.min.js` | chartjs-adapter-date-fns — https://github.com/chartjs/chartjs-adapter-date-fns | MIT, Copyright (c) Chart.js Contributors |

## The MIT licence

> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALING IN THE
> SOFTWARE.

The licence of this package, whatever it is, does not reach these five files:
they stay under the terms above, and replacing one of them means checking that
its replacement's terms still allow this.
