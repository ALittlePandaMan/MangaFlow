// Node-only build contract; keep Node tooling out of browser TypeScript types.
import {readFileSync} from 'node:fs'
import {resolve} from 'node:path'
import {cwd} from 'node:process'
import {expect, it} from 'vitest'

it('keeps the current fitted-text cache version aligned with the backend renderer', () => {
  const root=cwd()
  const backend=readFileSync(resolve(root,'../backend/app/services/rendering/pillow_renderer.py'),'utf8')
  const frontend=readFileSync(resolve(root,'src/features/editor/lib/perspectiveText.ts'),'utf8')
  const expected=Number(backend.match(/^RENDER_OUTPUT_VERSION = (\d+)$/m)?.[1])
  expect(Number.isFinite(expected)).toBe(true)
  expect(Number(frontend.match(/PERSPECTIVE_RENDER_OUTPUT_VERSION = (\d+)/)?.[1])).toBe(expected)
})
