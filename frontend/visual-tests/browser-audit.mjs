import fs from 'node:fs/promises'
import {URLSearchParams} from 'node:url'
import {measureLocalizationLayout} from './measure-layout.mjs'

/** Invoke through the documented browser tool, not a second browser driver.
 * Every record is flushed immediately so interrupted runs can be resumed. */
export function createLayoutAuditor({tab,cdp,directory,measure=measureLocalizationLayout,base='http://127.0.0.1:5174/visual-tests/index.html'}) {
  const hasIssue=result=>result.overflow.length||result.clipped.length||result.alignment.length||result.unreachable.length||result.headerOverlap||result.iconShrink?.length||result.splitWords?.length
  const summary=result=>({locale:result.locale,screen:result.screen,state:result.state,tab:result.tab,kind:result.kind,width:result.width,height:result.height,overflow:result.overflow.slice(0,5),clipped:result.clipped.slice(0,5),alignment:result.alignment,unreachable:result.unreachable.slice(0,5),headerOverlap:result.headerOverlap,iconShrink:result.iconShrink,splitWords:result.splitWords})
  const record=async(extra={})=>{
    // Native Range geometry is not exposed by the reduced read-only DOM API.
    // This expression reads only the rendered DOM in the local audit origin.
    const evaluation=await cdp.send('Runtime.evaluate',{expression:'('+measure.toString()+')()',returnByValue:true})
    if(evaluation.exceptionDetails || !evaluation.result?.value)throw new Error('Layout measurement did not complete: '+JSON.stringify(evaluation.exceptionDetails))
    const result=evaluation.result.value
    Object.assign(result,extra,{timestamp:new Date().toISOString()})
    await fs.mkdir(directory,{recursive:true})
    await fs.appendFile(directory+'/measurements.jsonl',JSON.stringify(result)+'\n')
    return result
  }
  const viewport=async(width,height=900)=>cdp.send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<700})
  const navigate=async(screen,locale,state='normal',extra={})=>{
    const query=new URLSearchParams({screen,locale,state,...extra})
    await tab.goto(base+'?'+query)
    await tab.getAXState({emit:false})
    if (screen==='settings') await tab.playwright.locator('.settings-primary').first().waitFor({state:'visible'})
    if (screen==='setup') await tab.playwright.locator('.settings-recommendations').waitFor({state:'visible'})
    if (screen==='editor') await tab.playwright.locator('.editor-workspace').waitFor({state:'visible'})
    return query.toString()
  }
  const screens=async(locale,width,height,cases)=>{
    await viewport(width,height)
    const results=[]
    for (const {screen,state='normal',...extra} of cases) {
      const testCase=await navigate(screen,locale,state,extra)
      results.push(await record({kind:'screen',testCase}))
    }
    return results.filter(hasIssue).map(summary)
  }
  const settings=async(locale,width,height=900)=>{
    await viewport(width,height)
    await navigate('settings',locale)
    if(width<768)await tab.playwright.locator('.app-navigation button').click()
    const labels=await tab.playwright.locator('#settings-navigation .settings-nav-item').allTextContents()
    const results=[]
    for(const label of labels){
      if(width<768&&await tab.playwright.locator('.app-navigation button').getAttribute('aria-expanded')!=='true')await tab.playwright.locator('.app-navigation button').click()
      await tab.playwright.locator('#settings-navigation .settings-nav-item').filter({hasText:label}).click()
      await tab.playwright.getByRole('heading',{level:1,name:label,exact:true}).waitFor({state:'visible'})
      for(const element of await tab.playwright.locator('details > summary').all())if(await element.isVisible()&&!await element.evaluate(node=>node.parentElement.open))await element.click()
      await tab.getAXState({emit:false})
      results.push(await record({tab:label,kind:'settings-expanded'}))
    }
    return results.filter(hasIssue).map(summary)
  }
  const dropdowns=async(scope,extra={})=>{
    const results=[]
    for(const trigger of await scope.locator('button[aria-haspopup="listbox"]').all()){
      if(!await trigger.isVisible()||!await trigger.isEnabled())continue
      const label=await trigger.getAttribute('aria-label')
      await trigger.click()
      await tab.playwright.getByRole('listbox').waitFor({state:'visible'})
      await tab.getAXState({emit:false})
      results.push(await record({kind:'dropdown',label,...extra}))
      await trigger.press('Escape')
    }
    return results
  }
  const projectDialogs=async(locale,width,actions=['create','import','edit','delete'],menus=true)=>{
    await viewport(width,900);await navigate('projects',locale)
    const results=[]
    for(const action of actions){
      if(action==='create'||action==='import')await tab.playwright.locator('.page-heading > div:last-child > button').nth(action==='create'?1:0).click()
      else await tab.playwright.getByRole('article').first().locator('button').nth(action==='edit'?1:2).click()
      await tab.playwright.getByRole('dialog').waitFor({state:'visible'})
      await tab.getAXState({emit:false});results.push(await record({kind:'project-dialog',action}))
      if(menus)results.push(...await dropdowns(tab.playwright.getByRole('dialog'),{action}))
      await tab.playwright.locator('[data-dialog-close]').click()
    }
    return results.filter(hasIssue).map(summary)
  }
  const settingsProviders=async(locale,width,start=0,end=5,from=0,to=Infinity,menus=true)=>{
    await viewport(width,900);await navigate('settings',locale)
    if(width<768)await tab.playwright.locator('.app-navigation button').click()
    const labels=(await tab.playwright.locator('#settings-navigation .settings-nav-item').allTextContents()).slice(start,end)
    const results=[]
    for(const label of labels){
      if(width<768&&await tab.playwright.locator('.app-navigation button').getAttribute('aria-expanded')!=='true')await tab.playwright.locator('.app-navigation button').click()
      await tab.playwright.locator('#settings-navigation .settings-nav-item').filter({hasText:label}).click()
      const provider=tab.playwright.locator('.settings-content button[aria-haspopup="listbox"]').filter({visible:true}).first()
      await provider.click()
      const choices=await tab.playwright.getByRole('option').allTextContents()
      await provider.press('Escape')
      for(const choice of choices.slice(from,to)){
        await provider.click();await tab.playwright.getByRole('option',{name:choice,exact:true}).click()
        await tab.getAXState({emit:false})
        results.push(await record({kind:'settings-provider',tab:label,provider:choice}))
        if(menus)results.push(...await dropdowns(tab.playwright.locator('.settings-content'),{tab:label,provider:choice}))
      }
    }
    return results.filter(hasIssue).map(summary)
  }
  const recommendations=async(locale,width,start=0,end=6)=>{
    await viewport(width,900);await navigate('settings',locale)
    if(width<768)await tab.playwright.locator('.app-navigation button').click()
    await tab.playwright.locator('.settings-install').click()
    await tab.playwright.locator('.recommendation-navigation').waitFor({state:'visible'})
    const results=[]
    for(const step of (await tab.playwright.locator('.recommendation-navigation button').all()).slice(start,end)){
      await step.click()
      const label=await step.innerText()
      for(const candidate of await tab.playwright.locator('.recommendation-candidate-select').all()){
        await candidate.click()
        for(const guide of await tab.playwright.locator('.recommendation-guide > summary').all())if(await guide.isVisible()&&!await guide.evaluate(node=>node.parentElement.open))await guide.click()
        await tab.getAXState({emit:false});results.push(await record({kind:'recommendation',tab:label}))
      }
      results.push(...await dropdowns(tab.playwright.getByRole('dialog'),{tab:label}))
    }
    return results.filter(hasIssue).map(summary)
  }
  return {hasIssue,summary,record,viewport,navigate,screens,settings,dropdowns,projectDialogs,settingsProviders,recommendations}
}
