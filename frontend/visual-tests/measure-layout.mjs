/* global document, innerWidth, innerHeight, getComputedStyle */
/** Pure DOM reads. Run in a real browser, not jsdom. Canvas panning and
 * intentionally ellipsized user/model identifiers are not layout failures. */
export function measureLocalizationLayout() {
  const visible=element=>{
    if (!element.getClientRects().length || element.closest('[hidden],.sr-only,.konvajs-content') || element.querySelector(':scope > .konvajs-content')) return false
    const style=getComputedStyle(element)
    return style.visibility!=='hidden' && style.display!=='none'
  }
  const describe=element=>({tag:element.tagName,cls:typeof element.className==='string'?element.className:'svg',text:(element.innerText||element.getAttribute('aria-label')||'').replace(/\s+/g,' ').slice(0,100)})
  const all=[...document.querySelectorAll('body *')].filter(visible)
  const overflow=all.filter(element=>element.clientWidth>0 && element.scrollWidth>element.clientWidth+2 && !['auto','scroll','hidden','clip'].includes(getComputedStyle(element).overflowX) && !element.matches('input,textarea,canvas,svg,path')).map(element=>({...describe(element),width:element.clientWidth,scroll:element.scrollWidth}))
  const clipped=all.filter(element=>element.matches('button,summary,[role="option"],label') && element.clientHeight>0 && element.scrollHeight>element.clientHeight+3 && !['auto','scroll'].includes(getComputedStyle(element).overflowY) && !(getComputedStyle(element).contentVisibility==='auto' && (element.getBoundingClientRect().top>innerHeight || element.getBoundingClientRect().bottom<0))).map(element=>({...describe(element),height:element.clientHeight,scroll:element.scrollHeight}))
  const alignment=[]
  for (const row of document.querySelectorAll('.inspector-font-row,.inspector-size-row,.inspector-alignment-row,.inspector-stroke-row,.inspector-extra-fields,.inspector-transform-fields,.inspector-coordinate-fields')) {
    const fields=[...row.children].filter(visible).map(element=>({label:element.getBoundingClientRect(),control:element.querySelector('.number-control,button[aria-haspopup],.inspector-alignment-buttons')?.getBoundingClientRect(),text:element.childNodes[0]?.textContent})).filter(field=>field.control)
    for (let i=0;i<fields.length;i++) for (let j=i+1;j<fields.length;j++) {
      const a=fields[i],b=fields[j]
      if (Math.min(a.label.right,b.label.right)-Math.max(a.label.left,b.label.left)>1) continue
      if (Math.min(a.label.bottom,b.label.bottom)-Math.max(a.label.top,b.label.top)<20) continue
      if (Math.abs(a.control.top-b.control.top)>2) alignment.push({row:row.className,labels:[a.text,b.text],tops:[a.control.top,b.control.top]})
    }
  }
  const unreachable=[]
  for (const element of all.filter(element=>element.matches('button,input,textarea,a[href],summary,[role="option"]'))) {
    const box=element.getBoundingClientRect()
    let canScrollY=false,canScrollX=false
    for (let parent=element.parentElement;parent;parent=parent.parentElement) {
      const style=getComputedStyle(parent),bounds=parent.getBoundingClientRect()
      if (['auto','scroll'].includes(style.overflowY) && parent.scrollHeight>parent.clientHeight+1) canScrollY=true
      if (['auto','scroll'].includes(style.overflowX) && parent.scrollWidth>parent.clientWidth+1) canScrollX=true
      const vertical=!canScrollY && ['hidden','clip'].includes(style.overflowY) && (box.bottom>bounds.bottom+2||box.top<bounds.top-2)
      const horizontal=!canScrollX && ['hidden','clip'].includes(style.overflowX) && (box.right>bounds.right+2||box.left<bounds.left-2)
      if (vertical||horizontal) {unreachable.push({...describe(element),ancestor:parent.className,vertical,horizontal});break}
    }
  }
  const nav=document.querySelector('.app-navigation')?.getBoundingClientRect()
  const actions=document.querySelector('.app-header-slot:not(:empty)')?.getBoundingClientRect()
  const headerOverlap=innerWidth>=960 && nav && actions && nav.right>actions.left-4 && nav.left<actions.right ? {navigationRight:nav.right,actionsLeft:actions.left}:null
  const iconShrink=all.filter(element=>element.tagName.toLowerCase()==='svg' && Number(element.getAttribute('width'))>=14 && element.getBoundingClientRect().width<12).map(describe)
  const splitWords=[]
  const walker=document.createTreeWalker(document.body,4)
  for(let node=walker.nextNode();node;node=walker.nextNode()){
    const parent=node.parentElement
    if(!parent||!visible(parent)||!parent.closest('button,label,summary,h1,h2,h3,p')||parent.closest('.truncate,input,textarea,code,pre,[role="option"],[role="alert"],#global-dialog-message'))continue
    for(const match of node.textContent.matchAll(/\b[A-Za-z]{5,20}\b/g)){
      const range=document.createRange();range.setStart(node,match.index);range.setEnd(node,match.index+match[0].length)
      const boxes=[...range.getClientRects()]
      if(boxes.length>1&&Math.max(...boxes.map(box=>box.top))-Math.min(...boxes.map(box=>box.top))>3)splitWords.push({...describe(parent),word:match[0]})
    }
  }
  return {locale:document.documentElement.lang,screen:document.documentElement.dataset.auditScreen,state:document.documentElement.dataset.auditState,width:innerWidth,height:innerHeight,bodyWidth:document.body.scrollWidth,textLength:(document.body.innerText||'').trim().length,overflow,clipped,alignment,unreachable,headerOverlap,iconShrink,splitWords}
}
