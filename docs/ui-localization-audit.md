# Four-language UI layout audit

Verified on 2026-10-21 in Edge using the local development gallery and synthetic fixtures. Languages: Simplified Chinese, Traditional Chinese, English, and Japanese. The gallery replaces its API calls before rendering; it does not install models, translate content, or modify real projects.

## Coverage

- Core matrix: 32 loading, empty, error, busy, normal, and dialog states × four languages × three viewports (320×740, 768×900, 1440×900): 384 checks.
- All ten settings pages, expanded, in the same languages and viewports: 120 checks.
- Additional checks: project dialogs, provider forms and dropdowns, recommendation steps, shortcut categories, editor menus, property dropdowns and color popovers, reader errors, dark themes, 240px inspectors, 390px phones, 844×390 landscape, and editor widths up to 2100px.
- X/Y/Z follow-up: 48 inspector states covering four languages, normal/multiple/empty/busy selections, a 240px panel at 320×740, and 320px panels at tablet and desktop sizes. Browser interactions confirmed that X/Y coordinates and Z rotation commit correctly in the fixture inspector.

The recorded run contains 1,060 measurements and 975 distinct state keys. The summary reports complete core/settings matrices and no remaining detected findings. These are finite checks of the states above, not a guarantee for every device, browser, or possible user string.

## Changes

- Align inspector controls by their lower edge when translated labels wrap; allow color labels to determine their column width.
- Keep icons from shrinking inside localized buttons, and stack mobile editor dock labels under their icons.
- Let project titles and long diagnostics wrap without widening cards or error panels. Keep project actions able to wrap.
- Make setup stages responsive, and keep long dialog bodies scrollable without losing their header or action buttons.
- Constrain immersive-reader errors to the available column width. Preserve full selected values in dropdown tooltips when the trigger text is ellipsized.
- Replace the standalone text-rotation field with X/Y translation coordinates and Z rotation. X/Y move only translated geometry; multi-selection preserves relative spacing, and locked regions are excluded. Z retains explicit user-rotation provenance and existing perspective behavior.

The implementation follows the interface guidelines' content containment and accessible-control guidance, and the React guidelines' approach of deriving selection values during render rather than synchronizing duplicate state.

## Verification and reproduction

- `npm test`: 650 tests passed, zero failed.
- `npm run lint`: zero errors; existing hook/fast-refresh warnings remain.
- `npm run visual:typecheck`, `npm run i18n:check`, and `npm run build`: passed. Vite's existing large-chunk warning remains.
- Local frontend container rebuilt and restarted without restarting the backend or database. The production editor at port 8080 exposes the new X/Y/Z controls.

See `frontend/visual-tests/README.md` for gallery instructions. The ignored local evidence is under `tmp/localization-audit-20261021/final/`: `measurements.jsonl`, `summary.json`, and screenshots. The measurement helper checks overflow, clipping, field alignment, unreachable controls, header overlap, squeezed icons, and internally split English words. Representative screenshots were also inspected; DOM measurements alone do not establish visual quality. Intentional canvas panning and offscreen thumbnail size estimates are excluded.
