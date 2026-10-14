import {useCallback, useEffect, useRef, useState} from 'react'
import {api} from '../../../services/api'
import type {EditorHistoryState} from '../../../types'

export function useEditorHistory(projectId: string | undefined) {
  const [state,setState] = useState<EditorHistoryState | null>(null)
  const currentId = useRef(projectId)
  currentId.current = projectId
  const generation = useRef(0)
  const retry = useRef<number | null>(null)
  const accept = useCallback((next: EditorHistoryState) => {
    if (next.project_id !== currentId.current) return
    generation.current++
    setState(next)
  },[])
  const refresh = useCallback(async () => {
    const id = currentId.current, version = ++generation.current
    if (!id) return null
    try {
      const next = await api.history.state(id)
      if (currentId.current !== id || version !== generation.current) return null
      setState(next)
      if (retry.current !== null) window.clearTimeout(retry.current)
      // Completion and the final history checkpoint can cross in-flight HTTP
      // requests. Refresh a pending checkpoint rather than showing stale undo.
      if (next.pending) retry.current = window.setTimeout(() => {void refresh()},1000)
      return next
    } catch {return null}
  },[])
  useEffect(() => {
    const counter = generation
    void refresh()
    return () => {
      counter.current++
      if (retry.current !== null) window.clearTimeout(retry.current)
    }
  },[projectId,refresh])
  return {state:state?.project_id === projectId ? state : null,refresh,accept}
}
