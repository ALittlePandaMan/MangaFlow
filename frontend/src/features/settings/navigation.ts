import {t} from '../../i18n'
import {Cpu, Eraser, Keyboard, Languages, ScanText, SlidersHorizontal, TextCursorInput, Type} from 'lucide-react'

export const SETTINGS_PAGES = [
  {id:'detection', get label(){return t("文字检测")}, icon:ScanText, get description(){return t("定位漫画中的文字区域，配置检测阈值与文本框扩边。")}},
  {id:'ocr', get label(){return t("OCR 识别")}, icon:TextCursorInput, get description(){return t("读取文字区域中的原文，配置识别模型及运行参数。")}},
  {id:'translation', get label(){return t("云端翻译")}, icon:Languages, get description(){return t("配置翻译接口、模型与认证信息。")}},
  {id:'inpainting', get label(){return t("图像修复")}, icon:Eraser, get description(){return t("选择背景修复模型与运行设备。")}},
  {id:'rendering', get label(){return t("排版渲染")}, icon:Type, get description(){return t("配置译文排版与渲染引擎。")}},
  {id:'typography', get label(){return t("文本与排版")}, icon:SlidersHorizontal, get description(){return t("设置新建文本框的初始样式。")}},
  {id:'fonts', get label(){return t("字体管理")}, icon:Type, description:''},
  {id:'shortcuts', get label(){return t("快捷键")}, icon:Keyboard, get description(){return t("自定义工具、视图、区域与页面操作的键盘快捷键。")}},
  {id:'performance', get label(){return t("性能与并发")}, icon:Cpu, get description(){return t("配置页面调度与资源上限，查看模型实际运行设备。")}},
  {id:'language', get label(){return t("界面语言")}, icon:Languages, get description(){return t("选择应用界面使用的语言。")}},
] as const
export type SettingsPageId = typeof SETTINGS_PAGES[number]['id']
