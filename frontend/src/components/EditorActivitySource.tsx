import {useId, useLayoutEffect, useState} from 'react'
import type {CanvasTaskListProps} from '../features/editor/components/CanvasTaskList'
import {useActivityPanelSource} from '../features/editor/lib/activityPanelStore'

/** Keep the panel in the canvas corner while its renderer survives route changes. */
export function EditorActivitySource({activity, pages, pendingLabel, scope, batch, onCancelTask}: CanvasTaskListProps) {
  const owner = useId()
  const [target, setTarget] = useState<HTMLDivElement | null>(null)
  useLayoutEffect(() => {
    if (target) useActivityPanelSource.setState({source: {owner, target,
      props: {activity, pages, pendingLabel, scope, batch, onCancelTask}}})
  }, [owner, target, activity, pages, pendingLabel, scope, batch, onCancelTask])
  useLayoutEffect(() => () => {
    if (useActivityPanelSource.getState().source?.owner === owner) useActivityPanelSource.setState({source: null})
  }, [owner])
  return <div ref={setTarget} className="contents"/>
}
