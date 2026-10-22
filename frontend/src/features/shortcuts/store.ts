import {t} from '../../i18n'
import {create} from 'zustand'
import {persist} from 'zustand/middleware'

type ShortcutGroupId = 'tools' | 'views' | 'editing' | 'regions' | 'page'

export type ShortcutId =
  | 'tool.pan' | 'tool.select' | 'tool.rectangle' | 'tool.polygon' | 'tool.lasso'
  | 'view.original' | 'view.clean' | 'view.translated' | 'view.comparison'
  | 'edit.undo' | 'edit.redo' | 'edit.selectAll'
  | 'zoom.out' | 'zoom.reset' | 'zoom.in'
  | 'region.delete' | 'region.cancelSelection' | 'region.merge'
  | 'region.nudgeLeft' | 'region.nudgeRight' | 'region.nudgeUp' | 'region.nudgeDown'
  | 'region.ocr' | 'region.translate' | 'region.inpaint'
  | 'page.workflowOcr' | 'page.workflowTranslate' | 'page.workflowInpaint'
  | 'page.previous' | 'page.next'
  | 'page.import' | 'page.context' | 'page.export' | 'page.batchOcr' | 'page.reset'

interface ShortcutDefinition {
  id: ShortcutId
  group: ShortcutGroupId
  label: string
  description: string
  defaultBinding: string
  allowShiftVariant?: boolean
}

export const SHORTCUT_GROUPS: Array<{id: ShortcutGroupId, label: string, description: string}> = [
  {id:'tools', get label(){return t("工具")}, get description(){return t("切换画布选择与区域创建工具")}},
  {id:'views', get label(){return t("视图")}, get description(){return t("切换原图、净图、译文与对比模式")}},
  {id:'editing', get label(){return t("编辑与画布")}, get description(){return t("撤销、重做、全选和画布缩放")}},
  {id:'regions', get label(){return t("区域操作")}, get description(){return t("操作当前选中或多选的文字区域")}},
  {id:'page', get label(){return t("页面与流程")}, get description(){return t("当前页处理、导入导出及批量任务")}},
]

export const SHORTCUT_DEFINITIONS: ShortcutDefinition[] = [
  {id:'tool.pan', group:'tools', get label(){return t("拖拽画布")}, get description(){return t("拖动画布而不修改文字区域")}, defaultBinding:'KeyH'},
  {id:'tool.select', group:'tools', get label(){return t("选择 / 变换")}, get description(){return t("选择、移动和调整文字区域")}, defaultBinding:'KeyV'},
  {id:'tool.rectangle', group:'tools', get label(){return t("矩形区域")}, get description(){return t("创建矩形文字区域")}, defaultBinding:'KeyR'},
  {id:'tool.polygon', group:'tools', get label(){return t("多边形区域")}, get description(){return t("逐点创建不规则文字区域")}, defaultBinding:'KeyP'},
  {id:'tool.lasso', group:'tools', get label(){return t("套索区域")}, get description(){return t("拖动创建自由选区")}, defaultBinding:'KeyL'},

  {id:'view.original', group:'views', get label(){return t("原图视图")}, get description(){return t("显示原始图片与原文框")}, defaultBinding:'Digit1'},
  {id:'view.clean', group:'views', get label(){return t("净图视图")}, get description(){return t("显示修复后的背景")}, defaultBinding:'Digit2'},
  {id:'view.translated', group:'views', get label(){return t("译文视图")}, get description(){return t("显示修复背景与翻译文字")}, defaultBinding:'Digit3'},
  {id:'view.comparison', group:'views', get label(){return t("对比视图")}, get description(){return t("左右对比原图与翻译结果")}, defaultBinding:'Digit4'},

  {id:'edit.undo', group:'editing', get label(){return t("撤销")}, get description(){return t("撤销最近一次编辑或图片操作（项目历史）")}, defaultBinding:'Mod+KeyZ'},
  {id:'edit.redo', group:'editing', get label(){return t("重做")}, get description(){return t("恢复最近一次撤销")}, defaultBinding:'Mod+KeyY'},
  {id:'edit.selectAll', group:'editing', get label(){return t("全选区域")}, get description(){return t("选择当前页的全部文字区域")}, defaultBinding:'Mod+KeyA'},
  {id:'zoom.out', group:'editing', get label(){return t("缩小画布")}, get description(){return t("降低画布缩放比例")}, defaultBinding:'Minus'},
  {id:'zoom.reset', group:'editing', get label(){return t("重置缩放")}, get description(){return t("恢复画布默认缩放比例")}, defaultBinding:'Digit0'},
  {id:'zoom.in', group:'editing', get label(){return t("放大画布")}, get description(){return t("提高画布缩放比例")}, defaultBinding:'Equal', allowShiftVariant:true},

  {id:'region.delete', group:'regions', get label(){return t("删除所选区域")}, get description(){return t("删除当前选中的一个或多个区域")}, defaultBinding:'Delete'},
  {id:'region.cancelSelection', group:'regions', get label(){return t("取消选择")}, get description(){return t("清除当前区域选择")}, defaultBinding:''},
  {id:'region.merge', group:'regions', get label(){return t("合并所选区域")}, get description(){return t("把多个已选区域合并为一个")}, defaultBinding:'Mod+KeyM'},
  {id:'region.nudgeLeft', group:'regions', get label(){return t("向左微调")}, get description(){return t("按住 Shift 可一次移动 10 像素")}, defaultBinding:'ArrowLeft', allowShiftVariant:true},
  {id:'region.nudgeRight', group:'regions', get label(){return t("向右微调")}, get description(){return t("按住 Shift 可一次移动 10 像素")}, defaultBinding:'ArrowRight', allowShiftVariant:true},
  {id:'region.nudgeUp', group:'regions', get label(){return t("向上微调")}, get description(){return t("按住 Shift 可一次移动 10 像素")}, defaultBinding:'ArrowUp', allowShiftVariant:true},
  {id:'region.nudgeDown', group:'regions', get label(){return t("向下微调")}, get description(){return t("按住 Shift 可一次移动 10 像素")}, defaultBinding:'ArrowDown', allowShiftVariant:true},
  {id:'region.ocr', group:'regions', get label(){return t("所选区域重新 OCR")}, get description(){return t("只重新识别当前所选区域")}, defaultBinding:'Alt+Digit1'},
  {id:'region.translate', group:'regions', get label(){return t("所选区域重新翻译")}, get description(){return t("只重新翻译当前所选区域")}, defaultBinding:'Alt+Digit2'},
  {id:'region.inpaint', group:'regions', get label(){return t("所选区域重新修复")}, get description(){return t("只重新修复当前所选区域")}, defaultBinding:'Alt+Digit3'},

  {id:'page.previous', group:'page', get label(){return t("上一张图片")}, get description(){return t("切换到页面列表中的上一张图片")}, defaultBinding:'KeyA'},
  {id:'page.next', group:'page', get label(){return t("下一张图片")}, get description(){return t("切换到页面列表中的下一张图片")}, defaultBinding:'KeyD'},
  {id:'page.workflowOcr', group:'page', get label(){return t("当前页重新检测")}, get description(){return t("按当前设置重新检测文字区域，再执行 OCR")}, defaultBinding:'Mod+Shift+Digit1'},
  {id:'page.workflowTranslate', group:'page', get label(){return t("当前页重新翻译")}, get description(){return t("执行当前页处理流程的翻译阶段")}, defaultBinding:'Mod+Shift+Digit2'},
  {id:'page.workflowInpaint', group:'page', get label(){return t("当前页重新修复")}, get description(){return t("执行当前页处理流程的修复阶段")}, defaultBinding:'Mod+Shift+Digit3'},
  {id:'page.import', group:'page', get label(){return t("导入图片")}, get description(){return t("打开漫画图片选择窗口")}, defaultBinding:'Mod+KeyO'},
  {id:'page.context', group:'page', get label(){return t("翻译上下文")}, get description(){return t("打开当前项目的翻译上下文")}, defaultBinding:'Mod+Shift+KeyC'},
  {id:'page.export', group:'page', get label(){return t("导出")}, get description(){return t("打开项目导出菜单")}, defaultBinding:'Mod+Shift+KeyE'},
  {id:'page.batchOcr', group:'page', get label(){return t("批量识别")}, get description(){return t("识别其他尚未完成 OCR 的图片")}, defaultBinding:'Mod+Shift+KeyB'},
  {id:'page.reset', group:'page', get label(){return t("重置当前页")}, get description(){return t("清空处理结果并恢复导入原图")}, defaultBinding:'Mod+Shift+Backspace'},
]

export type ShortcutMap = Record<ShortcutId, string>

export const DEFAULT_SHORTCUTS = Object.fromEntries(
  SHORTCUT_DEFINITIONS.map(item => [item.id, item.defaultBinding]),
) as ShortcutMap

interface ShortcutState {
  shortcuts: ShortcutMap
  setShortcuts: (shortcuts: Partial<ShortcutMap>) => void
  resetShortcuts: () => void
}

const normalizeShortcutMap = (value?: Partial<ShortcutMap>): ShortcutMap => Object.fromEntries(
  SHORTCUT_DEFINITIONS.map(item => [item.id, typeof value?.[item.id] === 'string' ? value[item.id] : item.defaultBinding]),
) as ShortcutMap

export const useShortcutStore = create<ShortcutState>()(persist(
  set => ({
    shortcuts: {...DEFAULT_SHORTCUTS},
    setShortcuts: shortcuts => set({shortcuts:normalizeShortcutMap(shortcuts)}),
    resetShortcuts: () => set({shortcuts:{...DEFAULT_SHORTCUTS}}),
  }),
  {
    name:'mangaflow.shortcuts',
    version:1,
    partialize: state => ({shortcuts:state.shortcuts}),
    merge: (persisted, current) => ({
      ...current,
      ...(persisted as Partial<ShortcutState>),
      shortcuts:normalizeShortcutMap((persisted as Partial<ShortcutState>)?.shortcuts),
    }),
  },
))

const MODIFIER_CODES = new Set(['ControlLeft', 'ControlRight', 'MetaLeft', 'MetaRight', 'AltLeft', 'AltRight', 'ShiftLeft', 'ShiftRight'])
const equivalentCodes = (code: string): string[] => {
  if (/^Digit\d$/.test(code)) return [code, `Numpad${code.slice(5)}`]
  if (/^Numpad\d$/.test(code)) return [code, `Digit${code.slice(6)}`]
  if (code === 'Equal' || code === 'NumpadAdd') return ['Equal', 'NumpadAdd']
  if (code === 'Minus' || code === 'NumpadSubtract') return ['Minus', 'NumpadSubtract']
  return [code]
}

export function shortcutFromEvent(event: Pick<KeyboardEvent, 'code' | 'ctrlKey' | 'metaKey' | 'altKey' | 'shiftKey'>): string | null {
  if (!event.code || MODIFIER_CODES.has(event.code)) return null
  const modifiers = [event.ctrlKey || event.metaKey ? 'Mod' : '', event.altKey ? 'Alt' : '', event.shiftKey ? 'Shift' : ''].filter(Boolean)
  return [...modifiers, event.code].join('+')
}

export function matchesShortcut(
  event: Pick<KeyboardEvent, 'code' | 'ctrlKey' | 'metaKey' | 'altKey' | 'shiftKey'>,
  binding: string,
  allowShiftVariant = false,
): boolean {
  if (!binding) return false
  const parts = new Set(binding.split('+'))
  const code = binding.split('+').at(-1)
  const hasMod = event.ctrlKey || event.metaKey
  if (!code || !equivalentCodes(code).includes(event.code) || parts.has('Mod') !== hasMod || parts.has('Alt') !== event.altKey) return false
  return parts.has('Shift') === event.shiftKey || (allowShiftVariant && !parts.has('Shift') && event.shiftKey)
}

export function shortcutForEvent(event: KeyboardEvent, shortcuts: ShortcutMap): ShortcutId | null {
  return SHORTCUT_DEFINITIONS.find(item => matchesShortcut(event, shortcuts[item.id], item.allowShiftVariant))?.id || null
}

const KEY_LABELS: Record<string, string> = {
  ArrowLeft:'←', ArrowRight:'→', ArrowUp:'↑', ArrowDown:'↓',
  Backspace:'Backspace', Delete:'Delete', Escape:'Esc', Enter:'Enter', Space:'Space',
  Minus:'-', Equal:'=', BracketLeft:'[', BracketRight:']', Semicolon:';', Quote:"'", Comma:',', Period:'.', Slash:'/', Backslash:'\\', Backquote:'`',
}

export function formatShortcut(binding: string): string {
  if (!binding) return t("未设置")
  return binding.split('+').map(part => {
    if (part === 'Mod') return 'Ctrl'
    if (part === 'Alt' || part === 'Shift') return part
    if (part.startsWith('Key')) return part.slice(3)
    if (part.startsWith('Digit')) return part.slice(5)
    if (part.startsWith('Numpad')) return `Num ${part.slice(6)}`
    return KEY_LABELS[part] || part
  }).join(' + ')
}

export function shortcutToAria(binding: string): string | undefined {
  if (!binding) return undefined
  return binding.replace('Mod', 'Control').replace(/Key([A-Z])/g, '$1').replace(/Digit([0-9])/g, '$1')
}

export function shortcutConflicts(shortcuts: ShortcutMap): Map<string, ShortcutId[]> {
  const bindings = new Map<string, ShortcutId[]>()
  SHORTCUT_DEFINITIONS.forEach(item => {
    const binding = shortcuts[item.id]
    if (!binding) return
    const parts = binding.split('+')
    const code = parts.at(-1)!
    const prefix = parts.slice(0, -1)
    const coveredBindings = equivalentCodes(code).map(coveredCode => [...prefix, coveredCode].join('+'))
    if (item.allowShiftVariant && !binding.split('+').includes('Shift')) {
      coveredBindings.push(...equivalentCodes(code).map(coveredCode => [...prefix, 'Shift', coveredCode].join('+')))
    }
    coveredBindings.forEach(covered => bindings.set(covered, [...(bindings.get(covered) || []), item.id]))
  })
  return new Map([...bindings].filter(([, ids]) => ids.length > 1))
}
