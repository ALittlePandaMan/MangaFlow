import {ChevronDown, Eye, EyeOff, Folder, Layers3} from 'lucide-react'
import {useCallback, useId, useLayoutEffect, useMemo, useRef, useState} from 'react'
import type {MouseEvent as ReactMouseEvent} from 'react'
import type {ImagePage, TextRegion} from '../../../types'
import {cn, scrollbarClass} from '../../../ui'
import {useEditorStore} from '../store'
import {TextTypesettingPanel} from './TextTypesettingPanel'
import {RegionLayerRow} from './RegionLayerRow'

const regionNameOrder=new Intl.Collator('zh-CN',{numeric:true,sensitivity:'base'})

interface Props {
  page?:ImagePage | null
  regions?:TextRegion[]
  region?:TextRegion
  selectedRegions:TextRegion[]
  selectedCount:number
  fontOptions:string[]
  busyAction?:string | null
  onUpdate:(id:string,patch:Partial<TextRegion>) => void
  onUpdateMany?:(changes:Array<{id:string;patch:Partial<TextRegion>}>) => void
  onAction:(action:string,options?:Record<string,unknown>) => void
}

export function RegionProperties({page,regions,region,selectedRegions,selectedCount,fontOptions,busyAction,onUpdate,onUpdateMany,onAction}:Props) {
  const [layersExpanded,setLayersExpanded]=useState(true)
  const layerListId=useId()
  const layers=useEditorStore(state => state.layers), toggleLayer=useEditorStore(state => state.toggleLayer)
  const targets=selectedRegions.length ? selectedRegions : region && selectedCount === 1 ? [region] : []
  const sourceItems=regions || targets
  // Sorting is display-only: never change OCR reading order or the caller's array.
  const items=useMemo(() => [...sourceItems].sort((a,b) => regionNameOrder.compare(a.region_key,b.region_key)),[sourceItems])
  const selected=new Set(targets.map(item => item.id)), selectionKey=targets.map(item => item.id).join(':')
  const list=useRef<HTMLDivElement>(null), anchor=useRef<string | null>(null)
  const latest=useRef({items,onUpdate,onUpdateMany,onAction,busyAction})
  latest.current={items,onUpdate,onUpdateMany,onAction,busyAction}
  const update=useCallback((id:string,patch:Partial<TextRegion>) => latest.current.onUpdate(id,patch),[])
  const updateMany=useCallback((changes:Array<{id:string;patch:Partial<TextRegion>}>) => {
    if(latest.current.onUpdateMany) latest.current.onUpdateMany(changes)
    else changes.forEach(change => latest.current.onUpdate(change.id,change.patch))
  },[])
  const selectRow=useCallback((id:string,event:ReactMouseEvent<HTMLButtonElement>) => {
    if(latest.current.busyAction) return
    const store=useEditorStore.getState(), additive=event.ctrlKey || event.metaKey
    if(event.shiftKey && anchor.current) {
      const ids=latest.current.items.map(item => item.id), start=ids.indexOf(anchor.current), end=ids.indexOf(id)
      if(start>=0 && end>=0) {const range=ids.slice(Math.min(start,end),Math.max(start,end)+1);store.selectMany(additive ? [...new Set([...store.selectedIds,...range])] : range);return}
    }
    store.select(id,additive);anchor.current=id
  },[])
  const deleteRow=useCallback(() => latest.current.onAction('delete'),[])
  useLayoutEffect(() => {
    if(!layersExpanded || selectedCount !== 1) return
    const container=list.current
    const current=container?.querySelector<HTMLElement>('[data-region-row].is-selected')
    if(!container || !current) return
    // Compact/short panels scroll as a whole; taller desktop panels scroll only the list.
    const scroller=getComputedStyle(container).overflowY === 'visible' ? container.parentElement : container
    if(!scroller) return
    const box=current.getBoundingClientRect(), bounds=scroller.getBoundingClientRect()
    if(box.top<bounds.top) scroller.scrollTop+=box.top-bounds.top
    else if(box.bottom>bounds.bottom) scroller.scrollTop+=Math.min(box.bottom-bounds.bottom,box.top-bounds.top)
  },[selectionKey,selectedCount,layersExpanded])
  return <aside className="region-inspector">
    <TextTypesettingPanel regions={targets} fontOptions={fontOptions} busy={!!busyAction} onChange={updateMany}/>
    <h3 className="inspector-section-heading inspector-layer-heading"><button type="button" className="inspector-section-toggle" aria-expanded={layersExpanded} aria-controls={layerListId} onClick={() => setLayersExpanded(value => !value)}><Layers3 size={15}/><span>图层</span><small>{items.length+(page ? 1 : 0)}</small><ChevronDown size={15} className="inspector-section-chevron"/></button></h3>
    <div id={layerListId} ref={list} hidden={!layersExpanded} className={cn('inspector-layer-list',scrollbarClass)}>
      <div className="inspector-group-row"><Folder size={17}/><span className="inspector-layer-title"><strong>文本</strong><small>文本组</small></span><span className="inspector-layer-opacity">100%</span><button type="button" className="inspector-icon-button inspector-eye" aria-label={layers.translated ? '隐藏文本组' : '显示文本组'} disabled={!!busyAction} onClick={() => toggleLayer('translated')}>{layers.translated ? <Eye size={16}/> : <EyeOff size={16}/>}</button></div>
      {items.map(item => <RegionLayerRow key={item.id} region={item} selected={selected.has(item.id)} expanded={selectedCount===1 && selected.has(item.id)} disabled={!!busyAction} onSelect={selectRow} onUpdate={update} onDelete={deleteRow}/>)}
      {!items.length && <p className="inspector-empty">暂无文本区域，识别或创建文本框后会显示在这里。</p>}
      {page && <div className="inspector-image-row"><Layers3 size={17}/><span className="inspector-layer-title"><strong>Mask</strong><small>蒙版叠加</small></span><button type="button" className="inspector-icon-button inspector-eye" disabled={!!busyAction} aria-label={layers.mask ? '隐藏 Mask 图层' : '显示 Mask 图层'} aria-pressed={!!layers.mask} onClick={() => toggleLayer('mask')}>{layers.mask ? <Eye size={16}/> : <EyeOff size={16}/>}</button></div>}
    </div>
  </aside>
}
