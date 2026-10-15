import {beforeEach,describe,expect,it} from 'vitest'
import {useEditorStore} from './store'

describe('mask operation and shape selection', () => {
  beforeEach(() => useEditorStore.setState({view:'mask',tool:'brush',maskOperation:'brush',layers:{original:false,detection:false,clean:false,translated:false,mask:false}}))

  it.each(['rectangle','polygon','lasso'] as const)('keeps the erase operation while selecting %s and the shape while changing operation', shape => {
    useEditorStore.getState().setMaskOperation('eraser')
    expect(useEditorStore.getState().tool).toBe('eraser')
    useEditorStore.getState().setTool(shape)
    expect(useEditorStore.getState().maskOperation).toBe('eraser')
    useEditorStore.getState().setMaskOperation('brush')
    expect(useEditorStore.getState().tool).toBe(shape)
    expect(useEditorStore.getState().maskOperation).toBe('brush')
    expect(useEditorStore.getState().layers.detection).toBe(false)
  })

  it('supports navigation and freehand without changing the operation implicitly', () => {
    useEditorStore.getState().setMaskOperation('eraser')
    useEditorStore.getState().setTool('pan')
    expect(useEditorStore.getState().maskOperation).toBe('eraser')
    useEditorStore.getState().setTool('select')
    useEditorStore.getState().setMaskOperation('eraser')
    expect(useEditorStore.getState().tool).toBe('eraser')
    useEditorStore.getState().setTool('brush')
    expect(useEditorStore.getState().maskOperation).toBe('brush')
  })

  it('still enables detection geometry for ordinary non-mask drawing tools', () => {
    useEditorStore.getState().setView('translated')
    useEditorStore.getState().toggleLayer('detection')
    useEditorStore.getState().setTool('rectangle')
    expect(useEditorStore.getState().layers.detection).toBe(true)
  })

  it.each(['original','clean','translated','comparison','mask'] as const)('defaults the Mask overlay off in %s view', view => {
    useEditorStore.getState().toggleLayer('mask')
    expect(useEditorStore.getState().layers.mask).toBe(true)
    useEditorStore.getState().setView(view)
    expect(useEditorStore.getState().layers.mask).toBe(false)
  })
})
