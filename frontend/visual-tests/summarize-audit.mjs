import fs from 'node:fs/promises'
import process from 'node:process'
import console from 'node:console'
import {URLSearchParams} from 'node:url'

const directory=process.argv[2]
if(!directory)throw new Error('Pass the directory containing measurements.jsonl')
const records=(await fs.readFile(directory+'/measurements.jsonl','utf8')).trim().split(/\r?\n/).map(JSON.parse)
const locales=['zh-CN','zh-TW','en','ja']
const viewports=[[320,740],[768,900],[1440,900]]
const cases=[['projects'],['projects-empty'],['projects-error'],['projects-loading'],['settings-error'],['settings-loading'],['editor'],['editor-empty'],['editor-error'],['editor-loading'],['editor-busy'],['setup'],['setup-error'],['setup-loading'],['inspector','empty'],['inspector'],['inspector','multiple'],['inspector','busy'],['mask'],['mask','busy'],['context'],['context','previous'],['context','error'],['context','busy'],['activity'],['activity','failed'],['prompt'],['prompt','content'],['prompt','long'],['blocking'],['blocking','unknown'],['loading']]
const key=record=>[record.locale,record.width,record.height,record.screen,record.state,record.kind,record.tab,record.action,record.label,record.provider,new URLSearchParams(record.testCase||'').get('panel'),new URLSearchParams(record.testCase||'').get('theme')].join('|')
const latest=new Map(records.map(record=>[key(record),record]))
const missing=[]
for(const locale of locales)for(const [width,height] of viewports)for(const [screen,state='normal'] of cases){
  if(![...latest.values()].some(record=>record.locale===locale&&record.width===width&&record.height===height&&record.screen===screen&&record.state===state&&record.kind==='screen'&&record.textLength>0&&!new URLSearchParams(record.testCase||'').has('panel')))missing.push({locale,width,height,screen,state})
}
for(const locale of locales)for(const [width,height] of viewports){
  const tabs=new Set(records.filter(record=>record.locale===locale&&record.width===width&&record.height===height&&record.kind==='settings-expanded').map(record=>record.tab))
  if(tabs.size!==10)missing.push({locale,width,height,settingsTabs:tabs.size,expected:10})
}
const findings=[...latest.values()].filter(record=>record.overflow.length||record.clipped.length||record.alignment.length||record.unreachable.length||record.headerOverlap||record.iconShrink?.length||record.splitWords?.length)
const result={measurements:records.length,uniqueStates:latest.size,coreScreenChecks:locales.length*viewports.length*cases.length,settingsPageChecks:locales.length*viewports.length*10,locales,viewports,missing,findings}
await fs.writeFile(directory+'/summary.json',JSON.stringify(result,null,2)+'\n')
console.log(JSON.stringify({...result,findings:findings.map(record=>({locale:record.locale,width:record.width,height:record.height,screen:record.screen,kind:record.kind,clipped:record.clipped,overflow:record.overflow,iconShrink:record.iconShrink}))},null,2))
if(missing.length||findings.length)process.exitCode=1
