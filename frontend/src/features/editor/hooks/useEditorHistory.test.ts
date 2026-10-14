import {act, renderHook, waitFor} from '@testing-library/react'
import {beforeEach, describe, expect, it, vi} from 'vitest'
import {api} from '../../../services/api'
import type {EditorHistoryState} from '../../../types'
import {useEditorHistory} from './useEditorHistory'

const state = (project_id:string,count:number):EditorHistoryState => ({project_id,undo_count:count,redo_count:0,undo_id:count || null,
  redo_id:null,undo_label:count ? '编辑' : null,redo_label:null,pending:false})
const deferred = () => {
  let resolve!:(value:EditorHistoryState) => void
  const promise = new Promise<EditorHistoryState>(done => {resolve=done})
  return {promise,resolve}
}
describe('project history status', () => {
  beforeEach(() => vi.spyOn(api.history,'state').mockResolvedValue(state('project-a',1)))
  it('ignores stale responses after switching projects', async () => {
    const first = deferred()
    vi.mocked(api.history.state).mockImplementation(id => id === 'project-a' ? first.promise : Promise.resolve(state(id,3)))
    const {result,rerender} = renderHook(({id}) => useEditorHistory(id),{initialProps:{id:'project-a'}})
    rerender({id:'project-b'})
    await waitFor(() => expect(result.current.state?.undo_count).toBe(3))
    await act(async () => first.resolve(state('project-a',10)))
    expect(result.current.state?.project_id).toBe('project-b')
  })
  it('does not overwrite an accepted undo result with an older refresh', async () => {
    const delayed = deferred()
    const {result} = renderHook(() => useEditorHistory('project-a'))
    await waitFor(() => expect(result.current.state?.undo_count).toBe(1))
    vi.mocked(api.history.state).mockReturnValueOnce(delayed.promise)
    act(() => {void result.current.refresh()})
    act(() => result.current.accept({...state('project-a',0),redo_count:1,redo_id:1,redo_label:'编辑'}))
    await act(async () => delayed.resolve(state('project-a',1)))
    expect(result.current.state?.undo_count).toBe(0)
    expect(result.current.state?.redo_count).toBe(1)
  })
  it('loads persisted history again after remounting', async () => {
    const first = renderHook(() => useEditorHistory('project-a'))
    await waitFor(() => expect(first.result.current.state?.undo_count).toBe(1))
    first.unmount()
    vi.mocked(api.history.state).mockResolvedValue(state('project-a',4))
    const second = renderHook(() => useEditorHistory('project-a'))
    await waitFor(() => expect(second.result.current.state?.undo_count).toBe(4))
  })
})
