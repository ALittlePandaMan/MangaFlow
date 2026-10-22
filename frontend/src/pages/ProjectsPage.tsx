import {t, useLocale} from '../i18n'
import { ArchiveRestore, ArrowRight, FolderPlus, Languages, Pencil, Trash2, Upload } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { EmptyState } from '../components/AppShell'
import { useGlobalDialog } from '../components/GlobalDialog'
import {useGlobalAlert} from '../components/GlobalAlert'
import {ButtonLoading, ProjectsSkeleton} from '../components/LoadingUI'
import {SelectControl} from '../components/FormControls'
import { api } from '../services/api'
import type { Project } from '../types'
import {buttonClass, eyebrowClass, iconButtonClass, inputClass, pageClass, primaryButtonClass} from '../ui'
import './ProjectsPage.css'

const PROJECT_LANGUAGES = [
  {value:'ja',get label(){return t("日语")}}, {value:'zh-CN',get label(){return t("简体中文")}}, {value:'zh-TW',get label(){return t("繁体中文")}},
  {value:'en',get label(){return t("英语")}}, {value:'ko',get label(){return t("韩语")}}, {value:'fr',get label(){return t("法语")}},
  {value:'de',get label(){return t("德语")}}, {value:'es',get label(){return t("西班牙语")}}, {value:'pt',get label(){return t("葡萄牙语")}}, {value:'ru',get label(){return t("俄语")}},
]

type ProjectDetailsPayload = Pick<Project,'name' | 'source_language' | 'target_language'>

function languageLabel(code: string): string {
  return PROJECT_LANGUAGES.find(language => language.value.toLowerCase() === code.toLowerCase())?.label
    || (code === 'zh' ? t("简体中文") : code)
}

function languageOptions(current: string) {
  return PROJECT_LANGUAGES.some(language => language.value === current)
    ? PROJECT_LANGUAGES : [...PROJECT_LANGUAGES,{value:current,label:languageLabel(current)}]
}

function ProjectLanguageFields({source, target, disabled, onSourceChange, onTargetChange}: {
  source: string; target: string; disabled: boolean
  onSourceChange: (value: string) => void; onTargetChange: (value: string) => void
}) {
  useLocale()
  return <fieldset className="project-language-fields" disabled={disabled}>
    <legend>{t("翻译方向")}</legend>
    <div className="project-language-options">
      <SelectControl ariaLabel={t("原文语言")} name="source_language" value={source} options={languageOptions(source)} onChange={onSourceChange} disabled={disabled}/>
      <ArrowRight size={18} aria-hidden="true"/>
      <SelectControl ariaLabel={t("译文语言")} name="target_language" value={target} options={languageOptions(target)} onChange={onTargetChange} disabled={disabled}/>
    </div>
  </fieldset>
}

export function ProjectsPage() {
  useLocale()
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [deletingProjectId, setDeletingProjectId] = useState('')
  const navigate = useNavigate()
  const {confirm: confirmDialog, openDialog} = useGlobalDialog()
  const {alert} = useGlobalAlert()

  const refresh = async () => {
    try { setProjects(await api.projects.list()) } catch (reason) { setError(String(reason)) } finally { setLoading(false) }
  }
  useEffect(() => { void refresh() }, [])

  const openCreateProjectDialog = () => {
    openDialog({
      title:t("新建项目"),
      className:'project-dialog',
      size:'small',
      content:({close}) => <CreateProjectForm
        onCancel={close}
        onCreate={async payload => {
          const project = await api.projects.create(payload)
          await refresh()
          close()
          navigate(`/projects/${project.id}/editor`)
        }}
      />,
    })
  }
  const openImportProjectDialog = () => {
    openDialog({
      title:t("导入源项目"),
      description:t("选择由 MangaFlow“导出源项目”生成的 ZIP 工程包，页面、文字区域、译文和样式会恢复为可编辑状态。"),
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
      title:t("编辑项目"),
      className:'project-dialog',
      size:'small',
      content:({close}) => <EditProjectForm
        project={project}
        onCancel={close}
        onSave={async payload => {
          const updated = await api.projects.update(project.id, payload)
          setProjects(current => current.map(item => item.id === updated.id ? updated : item))
          close()
        }}
      />,
    })
  }
  const removeProject = async (project: Project) => {
    const accepted = await confirmDialog({
      title: t("删除项目？"),
      message: t("“{{v0}}”及其全部页面、区域和工程文件将被永久删除。此操作无法撤销。", {v0: project.name}),
      tone: 'danger',
      confirmLabel: t("删除项目"),
    })
    if (!accepted) return
    setDeletingProjectId(project.id)
    try {
      await api.projects.remove(project.id)
      setProjects(current => current.filter(item => item.id !== project.id))
    }
    catch (reason) {alert({title:t('删除项目'),message:reason instanceof Error ? reason.message : String(reason),tone:'danger'})}
    finally {setDeletingProjectId('')}
  }

  if (loading) return <ProjectsSkeleton/>
  return <section className={`${pageClass} page-content-enter`}>
    <div className="page-heading mb-10 flex items-end justify-between gap-8">
      <div><span className={eyebrowClass}>WORKSPACES</span><h1 className="mb-2 mt-2 text-[36px] font-semibold leading-tight tracking-[-1.6px] text-ink text-balance">{t("漫画翻译项目")}</h1><p className="m-0 text-[14px] leading-6 text-muted">{t("从原稿到修复、排版与交付，所有页面都保留可编辑工程数据。")}</p></div>
      <div className="flex items-center gap-2.5"><button className={`${buttonClass} min-h-[42px] px-4`} onClick={openImportProjectDialog}><ArchiveRestore aria-hidden="true" size={18}/>{t("导入源项目")}</button><button className={`${primaryButtonClass} min-h-[42px] px-4`} onClick={openCreateProjectDialog}><FolderPlus aria-hidden="true" size={18}/>{t("新建项目")}</button></div>
    </div>
    {error && <div className="[overflow-wrap:anywhere] my-3 rounded-[10px] border border-danger/20 bg-danger/10 px-4 py-3 text-[13px] text-danger-soft-ink" role="alert">{error}</div>}
    {!projects.length ? <EmptyState title={t("还没有漫画项目")} detail={t("创建第一个项目，然后批量导入 JPG、PNG 或 WebP 原稿。")} action={<button className={primaryButtonClass} onClick={openCreateProjectDialog}>{t("开始创建")}</button>} /> :
      <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,320px),1fr))] gap-6">{projects.map((project, index) => <article className="project-card flex min-w-0 flex-col overflow-hidden rounded-2xl border border-line bg-panel [box-shadow:var(--shadow-card)] hover:border-line-strong hover:[box-shadow:var(--shadow-card-hover)]" key={project.id}>
        <div className="relative grid h-[150px] shrink-0 place-items-center overflow-hidden bg-surface text-secondary before:absolute before:size-40 before:rotate-[19deg] before:rounded-full before:border-[16px] before:border-double before:border-line before:opacity-60">{project.cover_url ? <img className="absolute inset-0 size-full object-cover" src={project.cover_thumbnail_url || project.cover_url} alt={t("{{v0}} 封面", {v0: project.name})} loading={index < 3 ? 'eager' : 'lazy'} decoding="async" {...{fetchpriority: index < 3 ? 'high' : 'low'}} onError={event => {if (project.cover_url && event.currentTarget.getAttribute('src') !== project.cover_url) event.currentTarget.src = project.cover_url}}/> : <span className="-rotate-[5deg] text-[68px] font-extrabold text-muted">漫</span>}<i className="absolute bottom-2.5 right-3 rounded bg-canvas px-2 py-1 text-[10px] not-italic text-accent">{t('{{count}} 页',{count:project.page_count || 0})}</i></div>
        <div className="flex min-w-0 flex-1 flex-col p-5.5">
          <div className="flex min-w-0 flex-wrap items-center gap-2 text-[13px] text-meta"><Languages size={14} className="shrink-0"/><span className="min-w-0 break-words">{languageLabel(project.source_language)}</span><ArrowRight size={12} className="shrink-0"/><span className="min-w-0 break-words">{languageLabel(project.target_language)}</span></div>
          <h2 className="mb-1 mt-3 min-h-[3em] text-[21px] font-semibold leading-normal tracking-[-.3px] line-clamp-2 [overflow-wrap:anywhere]" title={project.name}>{project.name}</h2>
          <div className="mt-auto flex flex-wrap gap-2.5 pt-5">
            <button className={`${primaryButtonClass} mr-auto`} onClick={() => navigate(`/projects/${project.id}/editor`)}>{t("打开工作台")}{' '}<ArrowRight size={15}/></button>
            <button className={iconButtonClass} title={t("编辑项目")} aria-label={t("编辑项目 {{v0}}", {v0: project.name})} onClick={() => openEditProjectDialog(project)}><Pencil aria-hidden="true" size={17}/></button>
            <button className={iconButtonClass+' icon-button-danger'} disabled={Boolean(deletingProjectId)} title={t("删除项目")} aria-label={t("删除项目 {{v0}}", {v0: project.name})} onClick={() => removeProject(project)}>{deletingProjectId === project.id ? <ButtonLoading compact label={t("正在删除项目")}/> : <Trash2 aria-hidden="true" size={17}/>}</button>
          </div>
        </div>
      </article>)}</div>}
  </section>
}

function ImportProjectForm({onCancel, onImport}: {onCancel: () => void; onImport: (file: File) => Promise<void>}) {
  useLocale()
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
      <button type="button" disabled={submitting} className="flex min-h-[118px] w-full cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-line-strong bg-canvas px-5 text-center text-secondary outline-none motion-feedback hover:border-accent/60 hover:bg-accent/[.03] hover:text-ink focus-visible:ring-3 focus-visible:ring-accent/15 disabled:cursor-not-allowed disabled:opacity-50" onClick={() => input.current?.click()}>
        <span className="grid size-10 place-items-center rounded-xl bg-accent/10 text-accent"><Upload size={19}/></span>
        <span className="max-w-full"><strong className="block truncate text-sm">{file?.name || t("选择 MangaFlow 项目包")}</strong><small className="mt-1 block text-[13px] leading-relaxed text-muted">{t("仅支持“导出源项目”生成的 ZIP 文件")}</small></span>
      </button>
      <input ref={input} hidden type="file" accept=".zip,application/zip" disabled={submitting} onChange={event => setFile(event.target.files?.[0] || null)}/>
      {error && <div className="mt-3 break-words rounded-lg bg-danger/15 px-3 py-2 text-[13px] text-danger-soft-ink">{error}</div>}
    </div>
    <div className="flex justify-end gap-2 border-t border-line-subtle px-6 py-4">
      <button className={buttonClass} disabled={submitting} onClick={onCancel} type="button">{t("取消")}</button>
      <button className={primaryButtonClass} disabled={!file || submitting} type="submit">{submitting ? <ButtonLoading label={t("正在导入…")}/> : <><ArchiveRestore size={16}/>{t("导入并打开")}</>}</button>
    </div>
  </form>
}

function EditProjectForm({project, onCancel, onSave}: {project: Project; onCancel: () => void; onSave: (payload: ProjectDetailsPayload) => Promise<void>}) {
  useLocale()
  const [name, setName] = useState(project.name)
  const [source, setSource] = useState(project.source_language)
  const [target, setTarget] = useState(project.target_language)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const submit = async () => {
    const projectName = name.trim()
    if (!projectName || submitting) return
    setSubmitting(true); setError('')
    try {
      await onSave({name:projectName, source_language:source, target_language:target})
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      setSubmitting(false)
    }
  }

  return <form onSubmit={event => {event.preventDefault(); void submit()}}>
    <div className="p-6">
      <label className="block text-sm text-secondary" htmlFor="edit-project-name">{t("项目标题")}</label>
      <input autoFocus className={`${inputClass} mt-2 min-h-[44px]`} disabled={submitting} id="edit-project-name" maxLength={200} value={name} onChange={event => setName(event.target.value)} placeholder={t("输入项目标题")}/>
      <ProjectLanguageFields source={source} target={target} onSourceChange={setSource} onTargetChange={setTarget} disabled={submitting}/>
      {error && <div className="mt-3 break-words rounded-lg bg-danger/15 px-3 py-2 text-[13px] text-danger-soft-ink" role="alert">{error}</div>}
    </div>
    <div className="flex justify-end gap-2 border-t border-line-subtle px-6 py-4">
      <button className={buttonClass} disabled={submitting} onClick={onCancel} type="button">{t("取消")}</button>
      <button className={primaryButtonClass} disabled={!name.trim() || submitting} type="submit">{submitting ? <ButtonLoading label={t("保存中…")}/> : <><Pencil size={16}/>{t("保存修改")}</>}</button>
    </div>
  </form>
}

function CreateProjectForm({onCancel, onCreate}: {onCancel: () => void; onCreate: (payload: ProjectDetailsPayload) => Promise<void>}) {
  useLocale()
  const [name, setName] = useState('')
  const [source, setSource] = useState('ja')
  const [target, setTarget] = useState('zh-CN')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    const projectName = name.trim()
    if (!projectName || submitting) return
    setSubmitting(true)
    setError('')
    try {
      await onCreate({name:projectName, source_language:source, target_language:target})
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      setSubmitting(false)
    }
  }

  return <form onSubmit={event => {event.preventDefault(); void submit()}}>
    <div className="p-6">
      <label className="block text-sm text-secondary" htmlFor="new-project-name">{t("项目标题")}</label>
      <input
        autoFocus
        className={`${inputClass} mt-2 min-h-[44px]`}
        disabled={submitting}
        id="new-project-name"
        maxLength={120}
        onChange={event => setName(event.target.value)}
        placeholder={t("例如：第一话 · 雨夜")}
        value={name}
      />
      <ProjectLanguageFields source={source} target={target} onSourceChange={setSource} onTargetChange={setTarget} disabled={submitting}/>
      {error && <div className="mt-3 break-words rounded-lg bg-danger/15 px-3 py-2 text-[13px] text-danger-soft-ink" role="alert">{error}</div>}
    </div>
    <div className="flex justify-end gap-2 border-t border-line-subtle px-6 py-4">
      <button className={buttonClass} disabled={submitting} onClick={onCancel} type="button">{t("取消")}</button>
      <button className={primaryButtonClass} disabled={!name.trim() || submitting} type="submit">
        {submitting ? <ButtonLoading label={t("创建中…")}/> : <><FolderPlus size={16}/>{t("创建项目")}</>}
      </button>
    </div>
  </form>
}
