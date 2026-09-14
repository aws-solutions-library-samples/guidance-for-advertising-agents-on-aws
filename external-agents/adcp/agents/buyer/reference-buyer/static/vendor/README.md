# Vendored third-party browser assets

## `motion.js` — Motion 13.0.0 (MIT), production UMD build

`npm pack motion` → `package/dist/motion.js`, copied verbatim. Licence in
`motion.LICENSE.md`.

### Why vendored rather than loaded from a CDN

The UI is served from S3 behind CloudFront and has no build step. A `<script>` tag
pointing at a public CDN would put a third party in the load path of an
authenticated page, which is the kind of external runtime dependency this project
avoids elsewhere. Vendoring keeps the asset on the same origin as `index.html`
and makes the version an explicit, reviewable file rather than a moving target.

### Why the UMD build, not the ESM one

`dist/es/index.mjs` is a one-line re-export from the bare specifier
`framer-motion/dom`, so it only resolves under a bundler. `dist/motion.js` is UMD
and attaches `window.Motion`, which is what a plain HTML page needs — one script
tag, no import map, no build.

`dist/motion.dev.js` is the same API unminified (527 KB versus 136 KB). The
production build is used; switch to the dev build temporarily if you need
readable stack traces from inside Motion.

### Upgrading

    cd /tmp && npm pack motion
    tar xzf motion-<version>.tgz
    cp package/dist/motion.js  <this dir>/motion.js
    cp package/LICENSE.md      <this dir>/motion.LICENSE.md

Then check that `window.Motion` still exports `animate` and `stagger`, and that
`deploy_ui.py` still uploads this directory.

### What uses it

`index.html`'s product stack (`renderProductStack`) uses `Motion.animate` and
`Motion.stagger` for the staggered card entrance and the expand/collapse
transition. Every animation is guarded: if `window.Motion` is absent the cards
render in their final state with no animation, so a failed asset load costs the
motion and not the content.
