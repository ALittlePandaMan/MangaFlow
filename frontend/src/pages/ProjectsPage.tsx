import { ArchiveRestore, ArrowRight, FolderPlus, Languages, Pencil, Trash2, Upload } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { EmptyState } from '../components/AppShell'
import { useGlobalDialog } from '../components/GlobalDialog'
import {ButtonLoading, ProjectsSkeleton, useMinimumLoadingTime} from '../components/LoadingUI'
import { api } from '../services/api'
import type { Project } from '../types'
import {buttonClass, dangerButtonClass, eyebrowClass, inputClass, pageClass, primaryButtonClass, textareaClass} from '../ui'

export function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [deletingProjectId, setDeletingProjectId] = useState('')
  const showingInitialLoading = useMinimumLoadingTime(loading, 500)
  const navigate = useNavigate()
  const {confirm: confirmDialog, openDialog} = useGlobalDialog()

  const refresh = async () => {
    try { setProjects(await api.projects.list()) } catch (reason) { setError(String(reason)) } finally { setLoading(false) }
  }
  useEffect(() => { void refresh() }, [])

  const openCreateProjectDialog = () => {
    openDialog({
      title:'新建项目',
      description:'创建漫画翻译工作区，创建后可在工作台继续导入 JPG、PNG 或 WebP 原稿。',
      size:'small',
      content:({close}) => <CreateProjectForm
        onCancel={close}
        onCreate={async name => {
          const project = await api.projects.create({name, source_language:'ja', target_language:'zh-CN'})
          await refresh()
          close()
          navigate(`/projects/${project.id}/editor`)
        }}
      />,
    })
  }
  const openImportProjectDialog = () => {
    openDialog({
      title:'导入源项目',
      description:'选择由 MangaFlow“导出源项目”生成的 ZIP 工程包，页面、文字区域、译文和样式会恢复为可编辑状态。',
      size:'small',
      content:({close}) => <ImportProjectForm
        onCancel={close}
        onImport={async file => {
          const imported = await api.projects.import(file)
          await refresh()
          close()
          navigate(`/projects/${imported.id}/editor`)
        }}
      />,
    })
  }
  const openEditProjectDialog = (project: Project) => {
    openDialog({
      title:'编辑项目',
      description:'修改项目名称和项目说明。项目封面会自动使用排序第一的漫画图片。',
      size:'small',
      content:({close}) => <EditProjectForm
        project={project}
        onCancel={close}
        onSave={async payload => {
          const updated = await api.projects.update(project.id, {name:payload.name, description:payload.description})
          setProjects(current => current.map(item => item.id === updated.id ? updated : item))
          close()
        }}
      />,
    })
  }
  const removeProject = async (project: Project) => {
    const accepted = await confirmDialog({
      title: '删除项目？',
      message: `“${project.name}”及其全部页面、区域和工程文件将被永久删除。此操作无法撤销。`,
      tone: 'danger',
      confirmLabel: '删除项目',
    })
    if (!accepted) return
    setDeletingProjectId(project.id)
    try {
      await api.projects.remove(project.id)
      setProjects(current => current.filter(item => item.id !== project.id))
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally {setDeletingProjectId('')}
  }

  if (showingInitialLoading) return <ProjectsSkeleton/>
  return <section className={`${pageClass} page-content-enter`}>
    <div className="mb-10 flex items-end justify-between gap-8">
      <div><span className={eyebrowClass}>WORKSPACES</span><h1 className="mb-2 mt-2 text-[36px] font-semibold leading-tight tracking-[-1.6px] text-ink text-balance">漫画翻译项目</h1><p className="m-0 text-[14px] leading-6 text-muted">从原稿到修复、排版与交付，所有页面都保留可编辑工程数据。</p></div>
      <div className="flex items-center gap-2.5"><button className={`${buttonClass} min-h-[42px] px-4`} onClick={openImportProjectDialog}><ArchiveRestore aria-hidden="true" size={18}/>导入源项目</button><button className={`${primaryButtonClass} min-h-[42px] px-4`} onClick={openCreateProjectDialog}><FolderPlus aria-hidden="true" size={18}/>新建项目</button></div>
    </div>
    {error && <div className="my-3 rounded-[10px] border border-danger/20 bg-danger/10 px-4 py-3 text-[13px] text-danger-soft-ink" role="alert">{error}</div>}
    {!projects.length ? <EmptyState title="还没有漫画项目" detail="创建第一个项目，然后批量导入 JPG、PNG 或 WebP 原稿。" action={<button className={primaryButtonClass} onClick={openCreateProjectDialog}>开始创建</button>} /> :
      <div className="grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-6">{projects.map(project => <article className="overflow-hidden rounded-2xl border border-line bg-panel [box-shadow:var(--shadow-card)] transition-[transform,border-color,box-shadow] duration-200 hover:-translate-y-0.5 hover:border-line-strong hover:[box-shadow:var(--shadow-card-hover)]" key={project.id}>
        <div className="relative grid h-[150px] place-items-center overflow-hidden bg-[repeating-linear-gradient(-35deg,var(--color-raised)_0,var(--color-raised)_8px,color-mix(in_srgb,var(--color-raised)_82%,var(--color-accent))_8px,color-mix(in_srgb,var(--color-raised)_82%,var(--color-accent))_9px)] text-secondary before:absolute before:size-40 before:rotate-[19deg] before:rounded-full before:border-[16px] before:border-double before:border-accent before:opacity-10">{project.cover_url ? <img className="absolute inset-0 size-full object-cover" src={project.cover_url} alt={`${project.name} 封面`}/> : <span className="-rotate-[5deg] text-[68px] font-extrabold text-ink [text-shadow:5px_5px_0_var(--color-accent-strong)]">漫</span>}<i className="absolute bottom-2.5 right-3 rounded bg-canvas px-2 py-1 font-mono text-[10px] not-italic text-accent">{project.page_count || '—'} PAGES</i></div>
        <div className="p-5.5">
          <div className="flex items-center gap-2 font-mono text-[10px] text-meta"><Languages size={14}/> 日本語 <ArrowRight size={12}/> 简体中文</div>
          <h2 className="mb-1 mt-3 text-[21px] font-semibold tracking-[-.3px]">{project.name}</h2><p className="m-0 min-h-[40px] text-[13px] leading-5 text-muted line-clamp-2">{project.description || '尚未填写项目说明'}</p>
          <div className="mt-5 flex gap-2.5">
            <button className={`${primaryButtonClass} mr-auto`} onClick={() => navigate(`/projects/${project.id}/editor`)}>打开工作台 <ArrowRight size={15}/></button>
            <button className={buttonClass} title="编辑项目" aria-label={`编辑项目 ${project.name}`} onClick={() => openEditProjectDialog(project)}><Pencil size={16}/></button>
            <button className={dangerButtonClass} disabled={Boolean(deletingProjectId)} title="删除项目" aria-label={`删除项目 ${project.name}`} onClick={() => removeProject(project)}>{deletingProjectId === project.id ? <ButtonLoading compact label="正在删除项目"/> : <Trash2 size={16}/>}</button>
          </div>
        </div>
      </article>)}</div>}
  </section>
}

function ImportProjectForm({onCancel, onImport}: {onCancel: () => void; onImport: (file: File) => Promise<void>}) {
  const input = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const submit = async () => {
    if (!file || submitting) return
    setSubmitting(true); setError('')
    try {await onImport(file)}
    catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      setSubmitting(false)
    }
  }
  return <form onSubmit={event => {event.preventDefault(); void submit()}}>
    <div className="p-6">
      <button type="button" disabled={submitting} className="flex min-h-[118px] w-full cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-line-strong bg-canvas px-5 text-center text-secondary outline-none transition-colors hover:border-accent/60 hover:bg-accent/[.03] hover:text-ink focus-visible:ring-3 focus-visible:ring-accent/15 disabled:cursor-not-allowed disabled:opacity-50" onClick={() => input.current?.click()}>
        <span className="grid size-10 place-items-center rounded-xl bg-accent/10 text-accent"><Upload size={19}/></span>
        <span className="max-w-full"><strong className="block truncate text-xs">{file?.name || '选择 MangaFlow 项目包'}</strong><small className="mt-1 block text-[10px] text-muted">仅支持“导出源项目”生成的 ZIP 文件</small></span>
      </button>
      <input ref={input} hidden type="file" accept=".zip,application/zip" disabled={submitting} onChange={event => setFile(event.target.files?.[0] || null)}/>
      {error && <div className="mt-3 rounded-lg bg-danger/15 px-3 py-2 text-xs text-danger-soft-ink">{error}</div>}
    </div>
    <div className="flex justify-end gap-2 border-t border-line-subtle px-6 py-4">
      <button className={buttonClass} disabled={submitting} onClick={onCancel} type="button">取消</button>
      <button className={primaryButtonClass} disabled={!file || submitting} type="submit">{submitting ? <ButtonLoading label="正在导入…"/> : <><ArchiveRestore size={16}/>导入并打开</>}</button>
    </div>
  </form>
}

interface EditProjectPayload {
  name: string
  description: string
}

function EditProjectForm({project, onCancel, onSave}: {project: Project; onCancel: () => void; onSave: (payload: EditProjectPayload) => Promise<void>}) {
  const [name, setName] = useState(project.name)
  const [description, setDescription] = useState(project.description || '')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const submit = async () => {
    const projectName = name.trim()
    if (!projectName || submitting) return
    setSubmitting(true); setError('')
    try {
      await onSave({name:projectName, description:description.trim()})
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      setSubmitting(false)
    }
  }

  return <form onSubmit={event => {event.preventDefault(); void submit()}}>
    <div className="p-6">
      <label className="block text-xs text-secondary" htmlFor="edit-project-name">项目名称</label>
      <input autoFocus className={`${inputClass} mt-2 min-h-[44px]`} disabled={submitting} id="edit-project-name" maxLength={200} value={name} onChange={event => setName(event.target.value)} placeholder="输入项目名称"/>
      <label className="mt-4 block text-xs text-secondary" htmlFor="edit-project-description">项目说明</label>
      <textarea className={`${textareaClass} mt-2 min-h-[126px] resize-none`} disabled={submitting} id="edit-project-description" maxLength={2000} value={description} onChange={event => setDescription(event.target.value)} placeholder="简单说明章节、剧情或翻译要求"/>
      <p className="mb-0 mt-3 text-[10px] leading-relaxed text-muted">项目封面自动使用页面列表中排序第一的原图。</p>
      {error && <div className="mt-3 rounded-lg bg-danger/15 px-3 py-2 text-xs text-danger-soft-ink">{error}</div>}
    </div>
    <div className="flex justify-end gap-2 border-t border-line-subtle px-6 py-4">
      <button className={buttonClass} disabled={submitting} onClick={onCancel} type="button">取消</button>
      <button className={primaryButtonClass} disabled={!name.trim() || submitting} type="submit">{submitting ? <ButtonLoading label="保存中…"/> : <><Pencil size={16}/>保存修改</>}</button>
    </div>
  </form>
}

function CreateProjectForm({onCancel, onCreate}: {onCancel: () => void; onCreate: (name: string) => Promise<void>}) {
  const [name, setName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    const projectName = name.trim()
    if (!projectName || submitting) return
    setSubmitting(true)
    setError('')
    try {
      await onCreate(projectName)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      setSubmitting(false)
    }
  }

  return <form onSubmit={event => {event.preventDefault(); void submit()}}>
    <div className="p-6">
      <label className="block text-xs text-secondary" htmlFor="new-project-name">项目名称</label>
      <input
        autoFocus
        className={`${inputClass} mt-2 min-h-[46px]`}
        disabled={submitting}
        id="new-project-name"
        maxLength={120}
        onChange={event => setName(event.target.value)}
        placeholder="例如：第一话 · 雨夜"
        value={name}
      />
      <p className="mb-0 mt-2 text-[11px] leading-relaxed text-muted">项目创建后将直接进入工作台，你可以在那里导入和处理漫画图片。</p>
      {error && <div className="mt-3 rounded-lg bg-danger/15 px-3 py-2 text-xs text-danger-soft-ink">{error}</div>}
    </div>
    <div className="flex justify-end gap-2 border-t border-line-subtle px-6 py-4">
      <button className={buttonClass} disabled={submitting} onClick={onCancel} type="button">取消</button>
      <button className={primaryButtonClass} disabled={!name.trim() || submitting} type="submit">
        {submitting ? <ButtonLoading label="创建中…"/> : <><FolderPlus size={16}/>创建项目</>}
      </button>
    </div>
  </form>
}
