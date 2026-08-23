import { ArrowRight, FolderPlus, ImagePlus, Languages, LoaderCircle, Pencil, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { EmptyState, Loading } from '../components/AppShell'
import { useGlobalDialog } from '../components/GlobalDialog'
import { api } from '../services/api'
import type { Project } from '../types'
import {buttonClass, dangerButtonClass, eyebrowClass, inputClass, pageClass, primaryButtonClass, textareaClass} from '../ui'

export function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
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
  const openEditProjectDialog = (project: Project) => {
    openDialog({
      title:'编辑项目',
      description:'修改项目名称、说明和项目列表中展示的封面图。',
      size:'medium',
      content:({close}) => <EditProjectForm
        project={project}
        onCancel={close}
        onSave={async payload => {
          let updated = await api.projects.update(project.id, {name:payload.name, description:payload.description})
          if (payload.cover) updated = await api.projects.uploadCover(project.id, payload.cover)
          else if (payload.removeCover) updated = await api.projects.removeCover(project.id)
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
    try {
      await api.projects.remove(project.id)
      setProjects(current => current.filter(item => item.id !== project.id))
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }

  if (loading) return <Loading label="正在读取项目…" />
  return <section className={pageClass}>
    <div className="mb-8 flex items-end justify-between gap-6">
      <div><span className={eyebrowClass}>WORKSPACES</span><h1 className="mb-2 mt-2 text-[34px] leading-tight tracking-[-1.5px] text-ink">漫画翻译项目</h1><p className="m-0 text-sm text-muted">从原稿到修复、排版与交付，所有页面都保留可编辑工程数据。</p></div>
      <button className={`${primaryButtonClass} min-h-[42px] px-4`} onClick={openCreateProjectDialog}><FolderPlus size={18}/>新建项目</button>
    </div>
    {error && <div className="my-3 rounded-lg bg-danger/15 px-4 py-3 text-xs text-[#ffb0a9]">{error}</div>}
    {!projects.length ? <EmptyState title="还没有漫画项目" detail="创建第一个项目，然后批量导入 JPG、PNG 或 WebP 原稿。" action={<button className={primaryButtonClass} onClick={openCreateProjectDialog}>开始创建</button>} /> :
      <div className="grid grid-cols-[repeat(auto-fill,minmax(310px,1fr))] gap-5">{projects.map(project => <article className="overflow-hidden rounded-xl border border-line-subtle bg-panel transition duration-200 hover:-translate-y-0.5 hover:border-line-strong hover:shadow-soft" key={project.id}>
        <div className="relative grid h-[150px] place-items-center overflow-hidden bg-[repeating-linear-gradient(-35deg,var(--color-raised)_0,var(--color-raised)_8px,color-mix(in_srgb,var(--color-raised)_82%,var(--color-accent))_8px,color-mix(in_srgb,var(--color-raised)_82%,var(--color-accent))_9px)] text-secondary before:absolute before:size-40 before:rotate-[19deg] before:rounded-full before:border-[16px] before:border-double before:border-accent before:opacity-10">{project.cover_url ? <img className="absolute inset-0 size-full object-cover" src={project.cover_url} alt={`${project.name} 封面`}/> : <span className="-rotate-[5deg] text-[68px] font-extrabold text-ink [text-shadow:5px_5px_0_var(--color-accent-strong)]">漫</span>}<i className="absolute bottom-2.5 right-3 rounded bg-canvas px-2 py-1 font-mono text-[10px] not-italic text-accent">{project.page_count || '—'} PAGES</i></div>
        <div className="p-5">
          <div className="flex items-center gap-2 font-mono text-[10px] text-[#7e9b91]"><Languages size={14}/> 日本語 <ArrowRight size={12}/> 简体中文</div>
          <h2 className="mb-1 mt-3 text-xl">{project.name}</h2><p className="m-0 h-[35px] text-xs text-muted">{project.description || '尚未填写项目说明'}</p>
          <div className="mt-4 flex gap-2">
            <button className={`${primaryButtonClass} mr-auto`} onClick={() => navigate(`/projects/${project.id}/editor`)}>打开工作台 <ArrowRight size={15}/></button>
            <button className={buttonClass} title="编辑项目" aria-label={`编辑项目 ${project.name}`} onClick={() => openEditProjectDialog(project)}><Pencil size={16}/></button>
            <button className={dangerButtonClass} title="删除项目" onClick={() => removeProject(project)}><Trash2 size={16}/></button>
          </div>
        </div>
      </article>)}</div>}
  </section>
}

interface EditProjectPayload {
  name: string
  description: string
  cover: File | null
  removeCover: boolean
}

function EditProjectForm({project, onCancel, onSave}: {project: Project; onCancel: () => void; onSave: (payload: EditProjectPayload) => Promise<void>}) {
  const [name, setName] = useState(project.name)
  const [description, setDescription] = useState(project.description || '')
  const [cover, setCover] = useState<File | null>(null)
  const [coverPreview, setCoverPreview] = useState<string | null>(project.cover_url)
  const [removeCover, setRemoveCover] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const coverInput = useRef<HTMLInputElement>(null)
  const objectUrl = useRef<string | null>(null)

  useEffect(() => () => {if (objectUrl.current) URL.revokeObjectURL(objectUrl.current)}, [])

  const chooseCover = (file: File | undefined) => {
    if (!file) return
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current)
    objectUrl.current = URL.createObjectURL(file)
    setCover(file)
    setCoverPreview(objectUrl.current)
    setRemoveCover(false)
    if (coverInput.current) coverInput.current.value = ''
  }
  const clearCover = () => {
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current)
    objectUrl.current = null
    setCover(null)
    setCoverPreview(null)
    setRemoveCover(!!project.cover_url)
  }
  const submit = async () => {
    const projectName = name.trim()
    if (!projectName || submitting) return
    setSubmitting(true); setError('')
    try {
      await onSave({name:projectName, description:description.trim(), cover, removeCover})
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      setSubmitting(false)
    }
  }

  return <form onSubmit={event => {event.preventDefault(); void submit()}}>
    <div className="grid gap-5 p-6 sm:grid-cols-[190px_minmax(0,1fr)]">
      <div>
        <span className="mb-2 block text-xs text-secondary">项目封面</span>
        <div className="relative grid aspect-[4/3] w-full place-items-center overflow-hidden rounded-xl bg-raised text-muted shadow-[inset_0_0_0_1px_var(--color-line-subtle)]">
          {coverPreview ? <img className="size-full object-cover" src={coverPreview} alt="项目封面预览"/> : <span className="flex flex-col items-center gap-2 text-[10px]"><ImagePlus size={25}/>暂无封面</span>}
        </div>
        <div className="mt-2 grid grid-cols-2 gap-2">
          <button className={`${buttonClass} !min-h-8 px-2 text-[10px]`} disabled={submitting} onClick={() => coverInput.current?.click()} type="button"><ImagePlus size={14}/>{coverPreview ? '更换' : '选择'}</button>
          <button className={`${dangerButtonClass} !min-h-8 px-2 text-[10px]`} disabled={submitting || !coverPreview} onClick={clearCover} type="button"><Trash2 size={14}/>移除</button>
        </div>
        <input ref={coverInput} hidden type="file" accept="image/jpeg,image/png,image/webp" onChange={event => chooseCover(event.currentTarget.files?.[0])}/>
      </div>
      <div className="min-w-0">
        <label className="block text-xs text-secondary" htmlFor="edit-project-name">项目名称</label>
        <input autoFocus className={`${inputClass} mt-2 min-h-[44px]`} disabled={submitting} id="edit-project-name" maxLength={200} value={name} onChange={event => setName(event.target.value)} placeholder="输入项目名称"/>
        <label className="mt-4 block text-xs text-secondary" htmlFor="edit-project-description">项目说明</label>
        <textarea className={`${textareaClass} mt-2 min-h-[126px] resize-none`} disabled={submitting} id="edit-project-description" maxLength={2000} value={description} onChange={event => setDescription(event.target.value)} placeholder="简单说明章节、剧情或翻译要求"/>
      </div>
      {error && <div className="rounded-lg bg-danger/15 px-3 py-2 text-xs text-[#ffb0a9] sm:col-span-2">{error}</div>}
    </div>
    <div className="flex justify-end gap-2 border-t border-line-subtle px-6 py-4">
      <button className={buttonClass} disabled={submitting} onClick={onCancel} type="button">取消</button>
      <button className={primaryButtonClass} disabled={!name.trim() || submitting} type="submit">{submitting ? <LoaderCircle className="animate-spin" size={16}/> : <Pencil size={16}/>} {submitting ? '保存中…' : '保存修改'}</button>
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
      {error && <div className="mt-3 rounded-lg bg-danger/15 px-3 py-2 text-xs text-[#ffb0a9]">{error}</div>}
    </div>
    <div className="flex justify-end gap-2 border-t border-line-subtle px-6 py-4">
      <button className={buttonClass} disabled={submitting} onClick={onCancel} type="button">取消</button>
      <button className={primaryButtonClass} disabled={!name.trim() || submitting} type="submit">
        {submitting ? <LoaderCircle className="animate-spin" size={16}/> : <FolderPlus size={16}/>}
        {submitting ? '创建中…' : '创建项目'}
      </button>
    </div>
  </form>
}
