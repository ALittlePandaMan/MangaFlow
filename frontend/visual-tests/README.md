# Four-language layout gallery

This is a development-only entry point. The production Vite entry remains `index.html`; the gallery is not bundled into the app.

Run `npm run dev -- --host 127.0.0.1 --port 5174`, then open `/visual-tests/index.html?screen=inspector&locale=en&state=empty` through the browser testing tool. All API methods used by the gallery are replaced with in-memory fixtures before rendering. Synthetic titles, long identifiers, diagnostics, tasks, and text regions do not access the user's projects or a translation provider.

- `screen`: projects, settings, editor, setup; append `-loading`, `-error`, or supported `-empty`/`-busy` variants. Also inspector, mask, context, activity, toast, prompt, blocking, loading, reader.
- `locale`: zh-CN, zh-TW, en, ja.
- `state`: normal, empty, multiple, busy, previous, error, failed, content, long, unknown (depending on screen).
- `panel`: inspector width, e.g. 240 or 320.
- `theme`: light or dark.

Use `createLayoutAuditor` from `browser-audit.mjs` with the documented browser tab and CDP capability. Its measurements are read-only DOM geometry, not internal app state. It checks horizontal overflow, vertically clipped controls, inspector control alignment, inaccessible controls, navigation/action overlap, compressed icons, and words split inside UI labels. Results are flushed to `measurements.jsonl` after every state, so an interrupted run can resume. The gallery needs no additional browser driver.

The core matrix is four languages × 32 states × 3 viewports (320×740, 768×900, 1440×900), plus four languages × 10 settings pages × 3 viewports. Interactive checks cover project dialogs, providers, recommendations, editor menus, and property popovers. Boundary checks additionally cover 240px inspectors, 390px phones, 844×390 landscape, 960/1100/1600/1920/2100px editor widths, dark themes, and reading errors.

Run `node visual-tests/summarize-audit.mjs <results-directory>` to verify matrix coverage and summarize the latest result per state. A reported overflow is evidence to inspect, not an automatic reason to hide content. Canvas panning and offscreen `content-visibility:auto` thumbnail estimates are excluded; long identifiers are intentionally ellipsized only where the full value remains available in a tooltip or dropdown. Inspect representative screenshots in addition to geometry; jsdom cannot establish visual layout correctness.

`npm run visual:typecheck` validates the gallery and app types. `npm test`, `npm run lint`, `npm run i18n:check`, and `npm run build` remain independent regression gates.
