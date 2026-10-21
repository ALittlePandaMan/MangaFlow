import {Cpu, Eraser, Keyboard, Languages, ScanText, SlidersHorizontal, TextCursorInput, Type} from 'lucide-react'

export const SETTINGS_PAGES = [
  {id:'detection', label:'文字检测', icon:ScanText, description:'定位漫画中的文字区域，配置检测阈值与文本框扩边。'},
  {id:'ocr', label:'OCR 识别', icon:TextCursorInput, description:'读取文字区域中的原文，配置识别模型及运行参数。'},
  {id:'translation', label:'云端翻译', icon:Languages, description:'配置翻译接口、模型与认证信息。'},
  {id:'inpainting', label:'图像修复', icon:Eraser, description:'选择背景修复模型与运行设备。'},
  {id:'rendering', label:'排版渲染', icon:Type, description:'配置译文排版与渲染引擎。'},
  {id:'typography', label:'文本与排版', icon:SlidersHorizontal, description:'设置新建文本框的初始样式。'},
  {id:'fonts', label:'字体管理', icon:Type, description:'查看内置字体，上传自定义字体，统一管理漫画排版资源。'},
  {id:'shortcuts', label:'快捷键', icon:Keyboard, description:'自定义工具、视图、区域与页面操作的键盘快捷键。'},
  {id:'performance', label:'性能与并发', icon:Cpu, description:'配置页面调度与资源上限，查看模型实际运行设备。'},
] as const
export type SettingsPageId = typeof SETTINGS_PAGES[number]['id']
