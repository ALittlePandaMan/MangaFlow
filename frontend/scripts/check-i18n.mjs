import fs from 'node:fs'
import path from 'node:path'
import {fileURLToPath, URL} from 'node:url'
import console from 'node:console'
import process from 'node:process'
import ts from 'typescript'

const root=fileURLToPath(new URL('../src/',import.meta.url))
const catalogs=['en','ja','zh-TW'].map(locale => [locale,JSON.parse(fs.readFileSync(path.join(root,'i18n/locales',locale+'.json'),'utf8'))])
const failures=[]
let checked=0
function scan(directory) {
  for (const entry of fs.readdirSync(directory,{withFileTypes:true})) {
    const filename=path.join(directory,entry.name)
    if (entry.isDirectory()) {scan(filename);continue}
    if (!/\.tsx?$/.test(filename) || /\.(test|worker)\./.test(filename)) continue
    const source=ts.createSourceFile(filename,fs.readFileSync(filename,'utf8'),ts.ScriptTarget.Latest,true)
    function visit(node) {
      if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text==='t' && node.arguments[0] && ts.isStringLiteralLike(node.arguments[0])) {
        const key=node.arguments[0].text
        checked++
        for (const [locale,catalog] of catalogs) if (/[\p{Script=Han}]/u.test(key) && !Object.hasOwn(catalog,key)) failures.push(`${path.relative(root,filename)}: missing ${locale} translation: ${key}`)
      }
      ts.forEachChild(node,visit)
    }
    visit(source)
  }
}
scan(root)
if (failures.length) {console.error(failures.join('\n'));process.exitCode=1}
else console.log(`Checked ${checked} static translation calls across all three target catalogs.`)
