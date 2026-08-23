import { AlertOctagon, AlertTriangle, ArrowLeft, ExternalLink } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Loading } from '../components/AppShell'
import { api } from '../services/api'
import type { Project, QualityIssue } from '../types'
import {cn, eyebrowClass, pageClass} from '../ui'

export function ReviewPage() {
  const {projectId = ''} = useParams()
  const [project, setProject] = useState<Project | null>(null)
  const [issues, setIssues] = useState<QualityIssue[]>([])
  const [loading, setLoading] = useState(true)
  useEffect(() => { Promise.all([api.projects.get(projectId), api.projects.review(projectId)]).then(([item, found]) => {setProject(item); setIssues(found)}).finally(() => setLoading(false)) }, [projectId])
  if (loading) return <Loading label="正在执行质量检查…"/>
  const groups = issues.reduce<Record<string, QualityIssue[]>>((result, issue) => {
    ;(result[issue.image_id] ||= []).push(issue)
    return result
  }, {})
  return <section className={pageClass}>
    <Link className="inline-flex items-center gap-2 text-xs text-secondary no-underline hover:text-accent" to={`/projects/${projectId}/editor`}><ArrowLeft size={16}/>返回编辑器</Link>
    <div className="mb-8 mt-6 flex items-end justify-between gap-6"><div><span className={eyebrowClass}>QUALITY CONTROL</span><h1 className="mb-2 mt-2 text-[34px] leading-tight tracking-[-1.5px]">Needs Review</h1><p className="m-0 text-sm text-muted">{project?.name} · 自动检查 OCR、翻译、Mask、修复与排版结果。</p></div><div className="flex flex-col items-end"><strong className="font-mono text-[38px] font-medium text-warning">{issues.length}</strong><span className="text-[10px] text-muted">个问题</span></div></div>
    {!issues.length ? <div className="flex min-h-[350px] flex-col items-center justify-center rounded-xl border border-dashed border-success/30"><span className="grid size-14 place-items-center rounded-full bg-success/15 text-[28px] text-success">✓</span><h2 className="mb-1 mt-4">当前没有发现问题</h2><p className="m-0 text-muted">所有已处理区域均通过自动质量检查。</p></div> : <div className="grid grid-cols-[repeat(auto-fit,minmax(390px,1fr))] items-start gap-4">{Object.entries(groups).map(([image, imageIssues], index) => <article key={image} className="overflow-hidden rounded-xl border border-line bg-panel"><header className="flex justify-between bg-surface px-4 py-3 font-mono text-[10px] text-muted"><span>PAGE {String(index + 1).padStart(2,'0')}</span><strong className="text-warning">{imageIssues?.length} issues</strong></header>{imageIssues?.map((issue, itemIndex) => <Link key={`${issue.code}-${itemIndex}`} to={`/projects/${projectId}/editor/${issue.image_id}?region=${issue.region_id || ''}`} className="grid grid-cols-[22px_1fr_18px] items-center gap-3 border-t border-line-subtle px-4 py-3 text-inherit no-underline hover:bg-raised">
      {issue.severity === 'error' ? <AlertOctagon className="text-danger" size={19}/> : <AlertTriangle className="text-warning" size={19}/>}<div><strong className="text-[11px] uppercase">{issue.region_key || '页面任务'} · {issue.code.replaceAll('_',' ')}</strong><p className="mb-0 mt-1 text-[10px] text-muted">{issue.message}</p></div><ExternalLink className={cn('text-muted', issue.severity === 'error' && 'text-danger/70')} size={16}/>
    </Link>)}</article>)}</div>}
  </section>
}
